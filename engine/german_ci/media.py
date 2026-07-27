"""Optional media enrichment: audio clips and screenshots for cards.

Strictly an add-on. The engine's contract is `.srt` in, study set out, and
everything here is skipped when there is no media -- which is the normal case
for Netflix and Prime, whose video is DRM-protected even though their subtitle
tracks are readable. YouTube and local files get the full treatment.

Requires `ffmpeg` on PATH, and `yt-dlp` for the YouTube path.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Extra seconds kept either side of a clip. Subtitle timings are tight, and a
# clip that starts exactly on the first phoneme sounds clipped.
PADDING = 0.35

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts")

# Smallest believable clip. An MP3 containing no audio is a ~329-byte header,
# which is exactly what an out-of-range seek produces.
MIN_OUTPUT_BYTES = 1024


class MediaError(RuntimeError):
    pass


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_ytdlp() -> bool:
    return shutil.which("yt-dlp") is not None


def _run(command: list[str]) -> None:
    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        raise MediaError(
            f"{command[0]} failed: {result.stderr.strip().splitlines()[-1:]}"
        )


def _try_run(command: list[str]) -> bool:
    """Run a command that is allowed to fail, reporting success as a bool.

    Used for the optional parts of a fetch -- an English track that does not
    exist, or a video download that is rate-limited -- where the run should
    carry on with whatever it did get.
    """
    try:
        _run(command)
        return True
    except MediaError:
        return False


def _safe_name(guid: str, suffix: str) -> str:
    """Anki dumps all media into one flat folder, so names must not collide."""
    return f"germanci-{guid}{suffix}"


def probe_duration(video_path: str) -> float | None:
    """Length of the media in seconds, or None if ffprobe cannot tell."""
    if not shutil.which("ffprobe"):
        return None
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        return None


def _produced(path: str, minimum: int = MIN_OUTPUT_BYTES) -> bool:
    """Did ffmpeg actually write something usable?

    Seeking past the end of a file makes ffmpeg exit 0 having written nothing
    (or a bare 329-byte MP3 header), so the exit code alone will happily
    report a full set of clips that do not exist.
    """
    return os.path.exists(path) and os.path.getsize(path) >= minimum


def extract_clips(
    items,
    rows,
    video_path: str,
    out_dir: str,
    audio: bool = True,
    screenshot: bool = True,
    padding: float = PADDING,
) -> dict:
    """Cut an audio clip and grab a frame for each selected sentence.

    `rows` comes from anki.build_rows so clip filenames key off the same GUID
    the note uses. Mutates each item's `extras` with the Anki field markup and
    reports what was produced, skipped, or failed.
    """
    if not have_ffmpeg():
        raise MediaError("ffmpeg not found on PATH (brew install ffmpeg)")
    if not os.path.exists(video_path):
        raise MediaError(f"video not found: {video_path}")

    os.makedirs(out_dir, exist_ok=True)
    duration_total = probe_duration(video_path)
    produced: list[str] = []
    failed = out_of_range = 0

    for item, row in zip(items, rows):
        guid = row["_guid"]
        start = max(0.0, item.sentence.start - padding)
        span = max(0.4, item.sentence.duration + 2 * padding)
        midpoint = item.sentence.start + item.sentence.duration / 2

        # A subtitle timeline running past the end of the video means the two
        # files do not belong together (wrong episode, or a different cut).
        # Worth saying plainly rather than emitting silent empty clips.
        if duration_total is not None and start >= duration_total:
            out_of_range += 1
            continue

        if audio:
            name = _safe_name(guid, ".mp3")
            path = os.path.join(out_dir, name)
            if not _produced(path):
                # -ss before -i seeks by keyframe (fast); -t after gives an
                # exact duration from that point.
                _run([
                    "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                    "-ss", f"{start:.3f}", "-i", video_path,
                    "-t", f"{span:.3f}",
                    "-vn", "-acodec", "libmp3lame", "-q:a", "5", path,
                ])
            if _produced(path):
                produced.append(path)
                item.extras["audio_field"] = f"[sound:{name}]"
            else:
                failed += 1

        if screenshot:
            name = _safe_name(guid, ".jpg")
            path = os.path.join(out_dir, name)
            if not _produced(path):
                _run([
                    "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                    "-ss", f"{midpoint:.3f}", "-i", video_path,
                    "-frames:v", "1", "-q:v", "3",
                    "-vf", "scale=480:-2", path,
                ])
            if _produced(path):
                produced.append(path)
                item.extras["screenshot_field"] = f'<img src="{name}">'
            else:
                failed += 1

    return {
        "files": produced,
        "failed": failed,
        "out_of_range": out_of_range,
        "duration": duration_total,
    }


def find_video_beside(subtitle_path: str) -> str | None:
    """Look for a video file sitting next to the subtitles.

    Subtitle and video files are normally downloaded together and share a stem,
    so this saves passing --video by hand in the common case.
    """
    directory = os.path.dirname(os.path.abspath(subtitle_path))
    stem = os.path.basename(subtitle_path)
    # Strip .srt plus any language tag: "Show.S01E01.de.srt" -> "Show.S01E01"
    for _ in range(2):
        stem = os.path.splitext(stem)[0]

    best = None
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(VIDEO_EXTENSIONS):
            continue
        candidate = os.path.join(directory, name)
        if os.path.splitext(name)[0] == stem:
            return candidate
        if best is None and stem.split(".")[0] and name.startswith(stem.split(".")[0]):
            best = candidate
    return best


def fetch_youtube(url: str, out_dir: str, language: str = "de",
                  download_video: bool = True) -> dict:
    """Download a video's subtitle track, and optionally the video itself.

    Subtitles are fetched first and on their own. They are a few kilobytes
    where the video is hundreds of megabytes, and if the only captions on
    offer are auto-generated the caller may well not want the video at all --
    so there is no sense paying for the download before we know what we have.

    Uploader-provided subtitles are tried before auto-generated ones: auto
    captions carry no punctuation, and without sentence punctuation the
    cue-to-sentence merge has nothing to work with.
    """
    if not have_ytdlp():
        raise MediaError(
            "yt-dlp not found on PATH.\n"
            "Install it with: pip install yt-dlp  (or brew install yt-dlp)"
        )
    os.makedirs(out_dir, exist_ok=True)
    template = os.path.join(out_dir, "%(title).80s.%(ext)s")

    def base(langs: str) -> list[str]:
        return [
            "yt-dlp", "--no-playlist", "--restrict-filenames",
            "--sub-langs", langs, "--convert-subs", "srt",
            "-o", template, "--skip-download",
        ]

    def existing_subs() -> list[str]:
        return sorted(
            os.path.join(out_dir, name)
            for name in os.listdir(out_dir)
            if name.endswith(".srt")
        )

    # --sub-langs takes REGEXES, not shell globs, and matches them in full.
    # "de-*" therefore reads as "de followed by zero or more hyphens" and
    # misses real tracks like "de-DE-9WqM8fC0bpI", making the engine reject
    # videos that do have proper German subtitles. "de.*" is what was meant.
    german_langs = f"{language},{language}.*"
    _run(base(german_langs) + ["--write-subs", url])
    auto_generated = False
    if not existing_subs():
        _run(base(german_langs) + ["--write-auto-subs", "--write-subs", url])
        auto_generated = True

    english_error = None
    if not _try_run(base("en,en.*") + ["--write-subs", url]):
        english_error = "no English subtitle track (cards will have no translation)"

    video_error = None
    if download_video:
        if not _try_run([
            "yt-dlp", "--no-playlist", "--restrict-filenames",
            "-f", "bv*[height<=720]+ba/b[height<=720]/b",
            "-o", template, url,
        ]):
            video_error = "video download failed (cards will have no audio)"

    subtitles = existing_subs()
    videos = [
        os.path.join(out_dir, name)
        for name in sorted(os.listdir(out_dir))
        if name.lower().endswith(VIDEO_EXTENSIONS)
    ]

    def pick(tag: str) -> str | None:
        for path in subtitles:
            if f".{tag}." in os.path.basename(path):
                return path
        return None

    return {
        "video": videos[0] if videos else None,
        "german": pick(language) or (subtitles[0] if subtitles else None),
        "english": pick("en"),
        "auto_generated": auto_generated,
        "subtitles": subtitles,
        "warnings": [w for w in (english_error, video_error) if w],
    }
