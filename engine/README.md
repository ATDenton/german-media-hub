# German Comprehensible-Input Engine

Turn whatever you are actually watching into study material. Feed it the `.srt`
from an episode, and it scores every sentence against a German frequency list,
works out which ones sit just past what you already know, and exports them to
Anki.

The organising idea is **i+1**: the most useful sentence is one where you know
every word but one, so the new word is pinned down by context instead of
floating free. Sorting by difficulty is easy and not very useful; finding the
sentences with exactly one new word — and ranking them by how much that word is
worth learning — is the point.

```
                    ┌── German .srt ──┐
                    │                 ├─→ score ─→ select ─→ review ─→ Anki
   (optional) English .srt, video ────┘
```

## Setup

```bash
cd engine
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 scripts/build_lexicon.py          # one-time, ~5 minutes
```

The build streams the ~1 GB kaikki.org German Wiktionary extract, keeps only
what overlaps the frequency list, and writes `data/lexicon.sqlite` (~60 MB,
gitignored). Nothing but the 662 KB frequency list is committed.

`ffmpeg` is needed only for audio/screenshot clips, `yt-dlp` only for
`--youtube`.

## Use

```bash
./german-ci analyze Tatort.de.srt --en Tatort.en.srt   # score an episode
./german-ci review                                     # mark words in a browser
./german-ci export --out deck.apkg                     # build the deck
```

`analyze` prints a readout of the whole episode before any card exists — how
much of it you already understand, how it spreads across CEFR levels, and how
many i+1 sentences it contains. That alone answers "is this show at my level?"

```
  sentences         366
  running words     3,719
  comprehension     79.8%  ██████████████████····
  i+1 sentences     84
  level mix         A1 70.5%  A2 12.0%  B1 7.2%  B2 6.0%  C1 3.1%  C2 1.2%
  verdict           hard - expect to lean on the translation
```

Useful flags: `--mode i+1|coverage|difficulty`, `--limit`, `--level A1..C2`,
`--video FILE`, `--youtube URL`, `--format apkg|tsv`, `--en-offset SECONDS`.

`coverage` mode answers a different question — the fewest sentences that
introduce the most new high-value vocabulary — which is what you want when
mining a whole episode rather than working through it in order.

## Where the material comes from

| Source | Subtitles | Video |
|---|---|---|
| YouTube | `yt-dlp`, in one step | yes — full audio cards |
| Local files / your own rips | already there | yes |
| Netflix, Prime | tracks are plain unencrypted WebVTT/TTML | no — DRM |

So Netflix and Prime give text cards, YouTube and local files give cards with
an audio clip and a screenshot. Media is always optional: the contract is
`.srt` in, study set out, and everything else is enrichment.

## How it works

**Sentences, not cues.** Subtitle cues are laid out for reading speed, not
grammar — one sentence routinely spans three cues, and one cue routinely holds
the end of one sentence and the start of the next. `subtitles.py` reassembles
them, keeping each sentence's time span, and knows that a period after `3.` or
`z.B.` or `Dr.` is not a full stop.

**Translations for free.** If you have both subtitle tracks, `align.py` pairs
them by timestamp overlap, so the English on the back of the card is a human
translation in the show's own register. Releases drift, so a constant offset is
estimated automatically (a 15.75s shift is detected and corrected in testing).

**Lemmas without a POS tagger.** Wiktionary lists every inflected form of each
headword; inverting that gives `gegangen → gehen` and `Häuser → Haus` with no
spaCy dependency.

**Difficulty** is driven mostly by the rarest word in the sentence, because
that is what actually stops you — a long sentence of common words reads easily,
a short one with an obscure noun does not. Length and clause structure are
secondary. Proper nouns are excluded throughout; without that, every scene
containing a character's name looks like advanced German.

Ranks come from a frequency list built from OpenSubtitles, so its register
matches subtitles exactly.

## Anki

Notes use a dedicated **German CI** note type: sentence with the target word
bolded, translation, gloss, lemma, rank, audio, screenshot, source and
timestamp. GUIDs are derived from `(source, timestamp, sentence)`, so
re-exporting an episode **updates** those notes instead of duplicating them —
verified by exporting twice and diffing the GUIDs.

Your profile (`data/profile.json`) tracks known lemmas, seeded from frequency
rank at a chosen CEFR level and corrected as you mark words. It also records
every lemma you have exported, which is what stops the next episode re-teaching
you `weil`.

## Known limitations

- **CEFR bands are approximate.** Mapping frequency rank to A1–C2 is a working
  convention, not a standard. Treat it as a difficulty ladder.
- **Ambiguous forms are sometimes resolved wrongly.** Without a POS tagger some
  German homographs are irreducibly ambiguous — `Mark` currently resolves to an
  obscure plant rather than the currency. The fix for any specific word is
  `data/overrides.json`, which takes priority over everything else; the
  numerals and pronouns in there were pinned exactly this way.
- **Auto-generated captions are usable, with one caveat.** They are normally
  punctuated and mine perfectly well — but YouTube delivers them as
  *rolling windows*, where each cue redisplays the previous line, so the raw
  file contains every line two or three times. The parser detects and
  collapses that; without it the merged text is fluent-looking nonsense.
  Since human-subtitled German is scarce on YouTube, this matters more than
  it sounds.

  What actually decides usability is punctuation, not provenance, so
  `analyze` measures it and warns when under half the sentences end in
  `.?!` — meaning the boundaries are guesses.
- Alignment corrects a constant offset, not variable drift.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

58 tests covering cue merging, alignment (including a deliberate offset),
lemma resolution, the i+1 selector, and GUID stability. The lexicon tests skip
themselves if `lexicon.sqlite` has not been built.

## Data sources

- Frequency list: [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords)
  (OpenSubtitles-derived, MIT), vendored as `data/de_50k.txt`.
- Dictionary: [kaikki.org](https://kaikki.org/dictionary/German/) German
  Wiktionary extract (CC BY-SA 3.0), downloaded at build time.
