# 🇩🇪 Deutsch — Media Hub

A single-file, searchable hub of real German media — news, TV, podcasts, books, YouTube — sorted by how much German you need to actually enjoy it.

**→ [Open the live page](https://atdenton.github.io/german-media-hub/)**

No build step, no dependencies, no tracking. It's one `index.html` file. Open it locally, or use the link above.

---

## What's in it

**Library** — 68 curated resources you can search and filter by:

| Level | CEFR | |
|---|---|---|
| 🥚 Noob | A1 | Nicos Weg, Sendung mit der Maus, Coffee Break German |
| 🐣 Beginner | A2 | Nachrichtenleicht, Klexikon, Ohrka, Löwenzahn |
| 🐦 Intermediate | B1 | Langsam gesprochene Nachrichten, Top-Thema, Easy German |
| 🦅 High | B2 | Tagesschau, maiLab, *Dark*, Der Spiegel |
| 🎓 Fluent | C1–C2 | Die Zeit, STRG_F, Der Postillon, Fest & Flauschig |

Also filterable by type (📖 Read / 📺 Watch / 🎧 Listen / 🛠 Tools), by free-vs-paid, and by whether the material is genuinely downloadable for offline use. Press <kbd>/</kbd> to jump to the search box.

**Parallel Reader** — **30 original German texts, 256 sentence pairs**, from *"Ich heiße Lena"* up to a C1 essay on translation. Tap any sentence to reveal the English. Every text carries a grammar note explaining the one thing it's really teaching, and you can page through texts with Previous / Next.

Filter by level and by category:

| Category | | What's in it |
|---|---|---|
| ☕ **Alltag** | Everyday life | Mornings, the bakery, flat-hunting, the doctor, moving house |
| 💬 **Gespräche** | Dialogues | Introductions, ordering coffee, a flatmate row, a job interview, party small talk |
| 🏛 **Land & Leute** | Culture | German Sundays, recycling, politeness, *Feierabend*, East and West |
| 📗 **Geschichten** | Stories | Original short fiction — a lost key, a stranger on a train, an empty house |
| 🔬 **Wissen** | Explainers | Why we yawn, how caffeine works, why German words are long, Gutenberg |
| ✍️ **Meinung** | Opinion | Essays on learning German, cash vs. cards, translation, and hurry |

**Daily Plan** — a 30–45 minute routine for each level, pointing at specific resources in the library.

**Download / Offline** — what is actually legal to download (Project Gutenberg, LibriVox, Ohrka, podcast feeds) and the two tools that do the most work.

---

## If you only do three things

1. **[Nicos Weg](https://learngerman.dw.com/en/nicos-weg/c-36519789)** — a genuinely complete, genuinely free A1→B1 video course from Deutsche Welle.
2. **[Language Reactor](https://www.languagereactor.com/)** — browser extension that puts dual German+English subtitles on Netflix and YouTube. Turns a subscription you already pay for into a study tool.
3. **[Langsam gesprochene Nachrichten](https://learngerman.dw.com/en/langsam-gesprochene-nachrichten/s-13610)** — the real news, read slowly, every weekday, with full transcripts.

---

## Editing it

Everything lives in `index.html`. The data is plain JavaScript arrays near the bottom of the file — no framework, no JSON fetching.

**Add a resource** — append to the `D` array:

```js
{t:"Title",              // name shown on the card
 s:"Source",             // small mono line under the title
 u:"https://…",          // link
 k:"read",               // read | watch | listen | tool
 l:[3,4],                // levels: 1 Noob … 5 Fluent
 free:1,                 // 1 = free, 0 = paid / paywalled
 dl:1,                   // optional: 1 = downloadable offline
 tg:"news podcast daily",// extra search keywords, space separated
 d:"One or two sentences on why it's worth your time."},
```

**Add a reading text** — append to the `TEXTS` array:

```js
{lv:3,                        // 1 Noob … 5 Fluent
 cat:"story",                 // alltag | dialog | kultur | story | wissen | essay
 title:"Die Frau im Zug",
 note:"What this text drills", // shown on the browse card
 tip:"<b>Grammar note.</b> …", // HTML allowed; shown under the text
 s:[["German sentence.","English translation."],
    ["Nächster Satz.","Next sentence."]]},
```

In the `dialog` category, prefix a line with `"A: "` / `"B: "` and the speaker tag gets styled automatically.

**Add to a daily plan** — edit the `PLANS` array.

Starred items, filters, chosen theme and reader level are stored in `localStorage`, so they persist per browser but never leave your machine.

---

## Notes

Nothing copyrighted is bundled into this repo — it links out rather than mirroring, so it won't rot the way a scraped archive would. **All 30 reader texts are original writing**, not excerpts from books or websites. That's deliberate: it keeps the repo clean of other people's material, and it means the vocabulary and grammar can be graded to hit one target per text, which scraped prose can't do.

For real German literature, the library links to Project Gutenberg and LibriVox, where the public-domain canon lives in full.

Some ARD/ZDF *video* is geo-restricted to German IP addresses. Their news and children's programming generally isn't — which happens to be exactly what a learner wants anyway.
