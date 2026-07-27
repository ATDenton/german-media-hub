"""Command line interface for the German comprehensible-input engine.

    german-ci analyze  Show.de.srt --en Show.en.srt      score an episode
    german-ci review                                      mark words in a browser
    german-ci export   --out deck.apkg                    build an Anki deck
    german-ci profile  --show                             inspect known vocabulary
"""

from __future__ import annotations

import argparse
import os
import sys

from . import align, anki, media, score, studyset, subtitles
from .lexicon import Lexicon
from .profile import LEVEL_SEEDS, Profile
from .select import MODES, select

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_SET = os.path.join(DATA, "study_set.json")


def _lexicon() -> Lexicon:
    try:
        return Lexicon()
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error


def _profile(path: str, lexicon: Lexicon, level: str | None) -> Profile:
    profile = Profile.load(path)
    if not profile.known:
        added = profile.seed_from_level(lexicon, level or profile.level)
        print(f"Seeded profile at {profile.level}: {added:,} known lemmas")
        profile.save()
    elif level and level.upper() != profile.level:
        added = profile.seed_from_level(lexicon, level)
        print(f"Reseeded at {level.upper()}: +{added:,} lemmas")
        profile.save()
    return profile


def _bar(value: float, width: int = 22) -> str:
    filled = int(round(value / 100 * width))
    return "█" * filled + "·" * (width - filled)


def _print_stats(stats: dict, source: str) -> None:
    print(f"\n  {source}")
    print(f"  {'-' * 58}")
    print(f"  sentences         {stats['sentences']:,}")
    print(f"  running words     {stats['tokens']:,}")
    print(f"  distinct lemmas   {stats['unique_lemmas']:,}")
    print(f"  unknown lemmas    {stats['unknown_lemmas']:,}")
    print(f"  comprehension     {stats['comprehension']:.1f}%  {_bar(stats['comprehension'])}")
    print(f"  mean difficulty   {stats['mean_difficulty']:.1f}/100")
    print(f"  i+1 sentences     {stats['i_plus_1_sentences']:,}")

    bands = stats["cefr_distribution"]
    total = sum(bands.values()) or 1
    spread = "  ".join(
        f"{band} {bands.get(band, 0) / total * 100:4.1f}%"
        for band in ["A1", "A2", "B1", "B2", "C1", "C2"]
    )
    print(f"  level mix         {spread}")

    comprehension = stats["comprehension"]
    if comprehension >= 98:
        verdict = "very comfortable - good for relaxed watching"
    elif comprehension >= 95:
        verdict = "comfortable - the sweet spot for input"
    elif comprehension >= 90:
        verdict = "a stretch - workable with subtitles"
    else:
        verdict = "hard - expect to lean on the translation"
    print(f"  verdict           {verdict}")
    print()


def _print_items(items, limit: int = 15) -> None:
    if not items:
        print("  (no sentences matched)")
        return
    print(f"  {'#':>3}  {'lvl':<3} {'diff':>4}  {'target':<18} sentence")
    print(f"  {'-' * 74}")
    for position, item in enumerate(items[:limit], start=1):
        target = (item.target or "")[:18]
        text = item.text if len(item.text) <= 48 else item.text[:45] + "..."
        print(f"  {position:>3}  {item.cefr:<3} {item.difficulty:>4.0f}  "
              f"{target:<18} {text}")
    if len(items) > limit:
        print(f"  ... and {len(items) - limit} more")
    print()


# -- commands -------------------------------------------------------------


def command_analyze(args) -> int:
    german_path, english_path, video_path = args.subtitles, args.en, args.video

    if args.youtube:
        print(f"Fetching {args.youtube} ...")
        try:
            fetched = media.fetch_youtube(
                args.youtube, args.media_dir, download_video=args.media
            )
        except media.MediaError as error:
            raise SystemExit(str(error)) from error
        german_path = fetched["german"] or german_path
        english_path = english_path or fetched["english"]
        video_path = video_path or fetched["video"]
        if not german_path:
            raise SystemExit("no subtitle track was available for that video")
        for warning in fetched.get("warnings", []):
            print(f"  ! {warning}")

        # Auto-captions are not merely "rough": with no punctuation to split
        # on, the merger invents sentence boundaries mid-clause and produces
        # fragments that are not German. Cards built from those actively
        # teach the wrong thing, so this needs an explicit opt-in rather than
        # a warning that scrolls off the top of the terminal.
        if fetched["auto_generated"] and not args.allow_auto_subs:
            raise SystemExit(
                "\n  Only auto-generated captions are available for this video.\n\n"
                "  They carry no punctuation, so sentences get split mid-clause\n"
                "  and the resulting cards teach broken German. Better options:\n\n"
                "    - pick a video whose uploader wrote real subtitles:\n"
                "        yt-dlp --skip-download --list-subs 'URL' \\\n"
                "          | grep -A4 'Available subtitles'\n"
                "    - or proceed anyway with --allow-auto-subs\n"
            )
        print(f"  subtitles: {os.path.basename(german_path)}")
        if video_path:
            print(f"  video:     {os.path.basename(video_path)}")

    if not german_path:
        raise SystemExit("give a subtitle file, or --youtube URL")
    if not os.path.exists(german_path):
        raise SystemExit(f"no such file: {german_path}")

    sentences = subtitles.load(german_path)
    if not sentences:
        raise SystemExit(f"no sentences found in {german_path}")

    lexicon = _lexicon()
    profile = _profile(args.profile, lexicon, args.level)

    translations: dict[int, str] = {}
    if english_path:
        if not os.path.exists(english_path):
            raise SystemExit(f"no such file: {english_path}")
        references = align.load_reference(english_path)
        translations, report = align.align(sentences, references, args.en_offset)
        print(
            f"Aligned English: {report.matched}/{report.total} sentences "
            f"({report.rate:.0f}%), offset {report.offset:+.2f}s"
        )
        if report.rate < 50:
            print("  ! low match rate - the two tracks may be different releases")

    scored = score.score_all(sentences, lexicon, profile, translations)
    stats = score.corpus_stats(scored, profile)
    source = os.path.basename(german_path)
    _print_stats(stats, source)

    items = select(
        args.mode, scored, lexicon, profile,
        limit=args.limit, min_tokens=args.min_tokens, max_tokens=args.max_tokens,
    )
    print(f"Study set ({args.mode}, {len(items)} sentences):\n")
    _print_items(items)

    if video_path and args.media:
        print(f"Cutting media from {os.path.basename(video_path)} ...")
        rows = anki.build_rows(items, source)
        try:
            report = media.extract_clips(items, rows, video_path, args.media_dir)
            print(f"  {len(report['files'])} media files -> {args.media_dir}")
            if report["out_of_range"]:
                length = report["duration"]
                print(
                    f"  ! {report['out_of_range']} sentences fall past the end of "
                    f"the video ({length:.0f}s)." if length else
                    f"  ! {report['out_of_range']} sentences fall past the end."
                )
                print("    The subtitles and the video look like different cuts.")
            if report["failed"]:
                print(f"  ! {report['failed']} clips could not be extracted")
            print()
        except media.MediaError as error:
            print(f"  ! media skipped: {error}\n")

    studyset.save(
        args.out, items, source, stats, args.mode,
        extra={"video": video_path or "", "media_dir": args.media_dir},
    )
    print(f"Saved study set -> {args.out}")
    print(f"Next: german-ci review    (or: german-ci export --out deck.apkg)")

    if args.export:
        return _do_export(args.out, args.export, args.format, args.profile, None)
    return 0


def _do_export(set_path: str, out_path: str, fmt: str, profile_path: str,
               deck_name: str | None) -> int:
    payload = studyset.load(set_path)
    items = payload["items"]
    if not items:
        raise SystemExit("study set is empty")

    source = payload.get("source", "german-ci")
    media_dir = payload.get("media_dir") or ""
    media_files: list[str] = []
    if media_dir and os.path.isdir(media_dir):
        rows = anki.build_rows(items, source)
        for row in rows:
            for suffix in (".mp3", ".jpg"):
                candidate = os.path.join(media_dir, f"germanci-{row['_guid']}{suffix}")
                if os.path.exists(candidate):
                    media_files.append(candidate)

    kwargs = {}
    if fmt == "apkg":
        kwargs = {"deck_name": deck_name, "media_files": media_files}
    result = anki.export(items, source, out_path, fmt=fmt, **kwargs)

    profile = Profile.load(profile_path)
    for row in result["rows"]:
        if row["_lemma"]:
            profile.record_export(row["_lemma"], row["_guid"])
    profile.save()

    print(f"\nExported {result['notes']} notes -> {result['path']}")
    if fmt == "apkg":
        print(f"  deck:  {result['deck']}")
        print(f"  media: {result['media']} files")
        print("  Import it in Anki: File > Import")
    else:
        print("  Import in Anki with fields in this order:")
        print("  " + ", ".join(anki.FIELDS))
    print(f"  {len(result['rows'])} lemmas recorded as mined in the profile")
    return 0


def command_export(args) -> int:
    return _do_export(args.set, args.out, args.format, args.profile, args.deck)


def command_review(args) -> int:
    from .server import serve

    if not os.path.exists(args.set):
        raise SystemExit(
            f"no study set at {args.set}\nRun: german-ci analyze <subtitles.srt>"
        )
    serve(args.set, args.profile, port=args.port, open_browser=not args.no_browser)
    return 0


def command_profile(args) -> int:
    lexicon = _lexicon()
    profile = Profile.load(args.profile)

    if args.reset:
        profile.known.clear()
        profile.unknown.clear()
        profile.exported.clear()
        print("Profile cleared")

    if args.seed:
        added = profile.seed_from_level(lexicon, args.seed)
        print(f"Seeded at {profile.level}: +{added:,} lemmas")

    for word in args.known or []:
        entry = lexicon.lookup(word)
        profile.mark_known(entry.lemma)
        print(f"known:   {word} -> {entry.lemma}")

    for word in args.unknown or []:
        entry = lexicon.lookup(word)
        profile.mark_unknown(entry.lemma)
        print(f"unknown: {word} -> {entry.lemma}")

    if args.seed or args.known or args.unknown or args.reset:
        profile.save()

    summary = profile.summary()
    print(f"\n  level            {summary['level']}")
    print(f"  known lemmas     {summary['known']:,}")
    print(f"  marked unknown   {summary['marked_unknown']:,}")
    print(f"  cards exported   {summary['exported']:,}")
    print(f"  file             {summary['path']}\n")
    return 0


def command_stats(args) -> int:
    lexicon = _lexicon()
    stats = lexicon.stats()
    print(f"\n  lemmas             {stats['lemmas']:,}")
    print(f"  inflected forms    {stats['forms']:,}")
    print(f"  frequency entries  {stats['frequency_entries']:,}")
    print(f"  gloss overrides    {stats['overrides']:,}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="german-ci", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Shared by every subcommand rather than sitting on the top-level parser,
    # so `analyze foo.srt --profile x` works as anyone would expect instead of
    # requiring the option before the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--profile", default=os.path.join(DATA, "profile.json"),
        help="learner profile file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", parents=[common],
                                    help="score a subtitle file")
    analyze.add_argument("subtitles", nargs="?", help="German .srt/.vtt")
    analyze.add_argument("--en", help="English .srt for sentence translations")
    analyze.add_argument("--en-offset", type=float, default=None,
                         help="seconds to shift the English track (default: auto)")
    analyze.add_argument("--youtube", help="fetch subtitles and video from a URL")
    analyze.add_argument("--video", help="video file for audio/screenshot clips")
    analyze.add_argument("--no-media", dest="media", action="store_false",
                         help="skip clip extraction even with a video")
    analyze.add_argument("--allow-auto-subs", action="store_true",
                         help="proceed even if only auto-generated captions exist")
    analyze.add_argument("--media-dir", default=os.path.join(DATA, "media"))
    analyze.add_argument("--mode", choices=MODES, default="i+1")
    analyze.add_argument("--limit", type=int, default=50)
    analyze.add_argument("--min-tokens", type=int, default=4)
    analyze.add_argument("--max-tokens", type=int, default=18)
    analyze.add_argument("--level", choices=sorted(LEVEL_SEEDS),
                         help="seed known vocabulary at this CEFR level")
    analyze.add_argument("--out", default=DEFAULT_SET)
    analyze.add_argument("--export", help="also export straight to this path")
    analyze.add_argument("--format", choices=["apkg", "tsv"], default="apkg")
    analyze.set_defaults(func=command_analyze, media=True)

    export = subparsers.add_parser("export", parents=[common], help="export a saved study set")
    export.add_argument("--set", default=DEFAULT_SET)
    export.add_argument("--out", required=True)
    export.add_argument("--format", choices=["apkg", "tsv"], default="apkg")
    export.add_argument("--deck", help="Anki deck name")
    export.set_defaults(func=command_export)

    review = subparsers.add_parser("review", parents=[common], help="review a study set in a browser")
    review.add_argument("--set", default=DEFAULT_SET)
    review.add_argument("--port", type=int, default=8777)
    review.add_argument("--no-browser", action="store_true")
    review.set_defaults(func=command_review)

    profile_parser = subparsers.add_parser("profile", parents=[common], help="inspect known vocabulary")
    profile_parser.add_argument("--show", action="store_true")
    profile_parser.add_argument("--seed", choices=sorted(LEVEL_SEEDS))
    profile_parser.add_argument("--known", nargs="+", metavar="WORD")
    profile_parser.add_argument("--unknown", nargs="+", metavar="WORD")
    profile_parser.add_argument("--reset", action="store_true")
    profile_parser.set_defaults(func=command_profile)

    stats = subparsers.add_parser("stats", parents=[common], help="lexicon size and coverage")
    stats.set_defaults(func=command_stats)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
