"""Localhost server behind the review UI.

The browser is a viewer, not a second implementation: scoring, lemmatization
and selection all stay in Python, and the page only renders what it is given
and posts back what was clicked. That keeps one source of truth for what a
word means and how hard a sentence is.

Binds to 127.0.0.1 only -- this serves a local profile and local media, and
has no business being reachable from the network.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse

from . import anki, studyset
from .lexicon import Lexicon
from .profile import Profile

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".mp3": "audio/mpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


class State:
    """Everything the UI can see or change, held in one place."""

    def __init__(self, set_path: str, profile_path: str):
        self.set_path = set_path
        self.profile_path = profile_path
        self.payload = studyset.load(set_path)
        self.profile = Profile.load(profile_path)
        self.lexicon = Lexicon()
        self.lock = threading.Lock()
        self.refresh()

    def refresh(self) -> None:
        """Recompute unknown words against the current profile and re-order.

        Marking a word known can turn a two-unknown sentence into an i+1
        sentence, which should immediately become more valuable -- so the
        ordering is derived, never stored.
        """
        for item in self.payload["items"]:
            item.unknown_lemmas = [
                entry.lemma
                for entry in item.entries
                if entry.known_word and not self.profile.knows(entry.lemma)
            ]
            # Preserve order while removing duplicates.
            item.unknown_lemmas = list(dict.fromkeys(item.unknown_lemmas))
            if item.unknown_lemmas and (
                not item.target or item.target not in item.unknown_lemmas
            ):
                item.target = item.unknown_lemmas[0]

        def order(item):
            info = self.lexicon.lemma_info(item.target) if item.target else None
            rank = info.rank if info and info.rank else 10**9
            # i+1 sentences first, then the most useful target word.
            return (0 if item.unknown_count == 1 else 1, rank, item.difficulty)

        self.payload["items"].sort(key=order)

    def snapshot(self) -> dict:
        media_dir = self.payload.get("media_dir") or ""
        items = []
        for item in self.payload["items"]:
            raw = item.to_dict()
            guid = anki.note_guid(
                self.payload.get("source", ""), raw["timestamp"], raw["text"]
            )
            raw["guid"] = guid
            for suffix, key in ((".mp3", "audio"), (".jpg", "screenshot")):
                path = os.path.join(media_dir, f"germanci-{guid}{suffix}")
                raw[key] = (
                    f"/media/germanci-{guid}{suffix}"
                    if media_dir and os.path.exists(path)
                    else ""
                )
            raw["known"] = [
                entry.lemma
                for entry in item.entries
                if entry.known_word and self.profile.knows(entry.lemma)
            ]
            items.append(raw)
        return {
            "source": self.payload.get("source", ""),
            "mode": self.payload.get("mode", ""),
            "stats": self.payload.get("stats", {}),
            "profile": self.profile.summary(),
            "items": items,
        }

    def mark(self, lemma: str, known: bool) -> None:
        with self.lock:
            if known:
                self.profile.mark_known(lemma)
            else:
                self.profile.mark_unknown(lemma)
            self.profile.save()
            self.refresh()

    def export(self, guids: list[str], fmt: str, out_path: str) -> dict:
        with self.lock:
            source = self.payload.get("source", "german-ci")
            wanted = set(guids)
            chosen = [
                item
                for item in self.payload["items"]
                if anki.note_guid(source, item.sentence.timestamp(), item.text)
                in wanted
            ]
            if not chosen:
                raise ValueError("nothing selected")

            media_dir = self.payload.get("media_dir") or ""
            media_files = []
            if media_dir and os.path.isdir(media_dir):
                for row in anki.build_rows(chosen, source):
                    for suffix in (".mp3", ".jpg"):
                        path = os.path.join(
                            media_dir, f"germanci-{row['_guid']}{suffix}"
                        )
                        if os.path.exists(path):
                            media_files.append(path)

            kwargs = {"media_files": media_files} if fmt == "apkg" else {}
            result = anki.export(chosen, source, out_path, fmt=fmt, **kwargs)

            for row in result["rows"]:
                if row["_lemma"]:
                    self.profile.record_export(row["_lemma"], row["_guid"])
            self.profile.save()
            self.refresh()
            return {
                "path": result["path"],
                "notes": result["notes"],
                "format": fmt,
            }


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # keep the terminal readable

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, code: int = 200) -> None:
            self._send(
                code,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_GET(self):
            path = urlparse(self.path).path

            if path in ("/", "/index.html"):
                with open(os.path.join(WEB, "review.html"), "rb") as handle:
                    self._send(200, handle.read(), CONTENT_TYPES[".html"])
                return

            if path == "/api/state":
                self._json(state.snapshot())
                return

            if path.startswith("/media/"):
                name = os.path.basename(unquote(path))
                media_dir = state.payload.get("media_dir") or ""
                target = os.path.join(media_dir, name)
                # Resolve and confine to the media directory: the name comes
                # off a URL, so it must not be able to walk out of it.
                if (
                    media_dir
                    and os.path.commonpath(
                        [os.path.abspath(target), os.path.abspath(media_dir)]
                    )
                    == os.path.abspath(media_dir)
                    and os.path.exists(target)
                ):
                    extension = os.path.splitext(target)[1]
                    with open(target, "rb") as handle:
                        self._send(
                            200,
                            handle.read(),
                            CONTENT_TYPES.get(extension, "application/octet-stream"),
                        )
                    return
                self._json({"error": "not found"}, 404)
                return

            self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json({"error": "bad json"}, 400)
                return

            if path == "/api/mark":
                lemma = (body.get("lemma") or "").strip()
                if not lemma:
                    self._json({"error": "lemma required"}, 400)
                    return
                state.mark(lemma, bool(body.get("known")))
                self._json(state.snapshot())
                return

            if path == "/api/export":
                fmt = body.get("format", "apkg")
                name = "german-ci.apkg" if fmt == "apkg" else "german-ci.tsv"
                out_path = os.path.join(DATA, name)
                try:
                    result = state.export(body.get("guids") or [], fmt, out_path)
                except (ValueError, SystemExit) as error:
                    self._json({"error": str(error)}, 400)
                    return
                result["state"] = state.snapshot()
                self._json(result)
                return

            self._json({"error": "not found"}, 404)

    return Handler


def serve(set_path: str, profile_path: str, port: int = 8777,
          open_browser: bool = True) -> None:
    state = State(set_path, profile_path)
    server = HTTPServer(("127.0.0.1", port), make_handler(state))
    url = f"http://127.0.0.1:{port}/"

    print(f"\n  Review UI:  {url}")
    print(f"  Study set:  {set_path}")
    print(f"  {len(state.payload['items'])} sentences  ·  "
          f"{state.profile.summary()['known']:,} known lemmas")
    print("\n  Click a word to toggle known · Ctrl-C to stop\n")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
