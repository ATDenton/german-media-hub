# german-ci — command reference

Runs from any folder. Full path if the alias is missing:
`~/Documents/Claude/german_learning/engine/german-ci`

## The usual loop

```bash
german-ci analyze --youtube 'URL'      # score it
german-ci review                       # mark words, pick cards
german-ci export --out ~/Desktop/deck.apkg
```

`analyze` saves to `data/study_set.json`; `review` and `export` pick it up
with no arguments.

## analyze — score a source

```bash
german-ci analyze Show.de.srt                      # a file you have
german-ci analyze Show.de.srt --en Show.en.srt     # + English translations
german-ci analyze --youtube 'URL'                  # fetch subs + video
german-ci analyze --youtube 'URL' --no-media       # subs only, much faster
german-ci analyze Show.de.srt --video Show.mkv     # local video for audio
```

| flag | does |
|---|---|
| `--mode i+1` | only sentences with one new word (default) |
| `--mode coverage` | fewest sentences teaching the most new words |
| `--mode difficulty` | easiest first — reading practice, not carding |
| `--limit N` | how many sentences (default 50) |
| `--level A1..C2` | reseed known vocabulary at this level |
| `--en FILE` | English subtitles for card-back translations |
| `--en-offset SECS` | fix drift manually (auto-detected otherwise) |
| `--no-media` | skip video download / clip cutting |
| `--min-tokens` / `--max-tokens` | sentence length bounds (4 / 18) |
| `--out FILE` | where to save (default `data/study_set.json`) |
| `--export FILE` | analyse and export in one step |
| `--format apkg\|tsv` | deck format (default apkg) |

## review — the browser UI

```bash
german-ci review              # opens http://127.0.0.1:8777
german-ci review --port 8800  # if that port is busy
```

Click a word to toggle known — the list re-ranks live. Click a row to select.
Hover for lemma, part of speech, rank, gloss. Ctrl-C in the terminal stops it.

## export — build the deck

```bash
german-ci export --out ~/Desktop/deck.apkg
german-ci export --out cards.tsv --format tsv
german-ci export --out deck.apkg --deck "German::Tatort"
```

Import in Anki with File > Import. Re-exporting the same episode **updates**
those notes rather than duplicating them.

## profile — what you know

```bash
german-ci profile                        # show current state
german-ci profile --seed B1              # reseed at a level
german-ci profile --known Haus gehen     # mark words known
german-ci profile --unknown trotzdem     # mark words unknown
german-ci profile --reset                # wipe and start over
```

## stats — lexicon size

```bash
german-ci stats
```

## Is a video usable?

```bash
yt-dlp --skip-download --list-subs 'URL' 2>/dev/null | grep -E "^de"
```

Any `de` line and you are fine — auto-generated captions work too. No German
line at all is the only real dealbreaker.

## Reading the output

- **comprehension %** — share of running words you already know. 90–95% is the
  comfortable zone; below ~85% you will lean on the translation.
- **i+1 sentences** — how many have exactly one new word. Fewer at low levels,
  because most sentences have two or more gaps. Grows as you mark words known.
- **punctuation warning** — sentence boundaries were guessed; skim before
  exporting.

## Fixing a wrong word

Edit `data/overrides.json`; it beats the dictionary:

```json
"langen": {"lemma": "lang", "pos": "adj", "gloss": "long"}
```

## Rebuilding the lexicon

```bash
cd ~/Documents/Claude/german_learning/engine
python3 scripts/build_lexicon.py    # ~5 min, only needed once
```
