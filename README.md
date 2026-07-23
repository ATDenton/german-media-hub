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

**Parallel Reader** — five original German texts, one per level, from *"Ich heiße Lena"* up to a C1 essay on translation. Tap any sentence to reveal the English. Each text comes with a note on the one grammar point that defines that level.

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

**Add a reading text** — append to the `TEXTS` array. `s` is an array of `[german, english]` sentence pairs.

**Add to a daily plan** — edit the `PLANS` array.

Starred items, filters, chosen theme and reader level are stored in `localStorage`, so they persist per browser but never leave your machine.

---

## Notes

Nothing copyrighted is bundled into this repo — it links out rather than mirroring, so it won't rot the way a scraped archive would. The German in the Parallel Reader is original writing, not quoted literature.

Some ARD/ZDF *video* is geo-restricted to German IP addresses. Their news and children's programming generally isn't — which happens to be exactly what a learner wants anyway.
