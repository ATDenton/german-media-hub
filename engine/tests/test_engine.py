"""Tests for lexicon lookup, scoring, selection, alignment and export."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from german_ci import align, anki, lexicon, score, select  # noqa: E402
from german_ci.profile import Profile  # noqa: E402
from german_ci.subtitles import Sentence  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
HAS_LEXICON = os.path.exists(os.path.join(ROOT, "data", "lexicon.sqlite"))
needs_lexicon = unittest.skipUnless(
    HAS_LEXICON, "lexicon.sqlite not built (python3 scripts/build_lexicon.py)"
)


@needs_lexicon
class TestLexicon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lx = lexicon.Lexicon()

    def test_resolves_inflected_forms_to_lemma(self):
        for surface, expected in [
            ("gegangen", "gehen"),
            ("Häuser", "Haus"),
            ("freier", "frei"),
            ("ist", "sein"),
            ("war", "sein"),
        ]:
            self.assertEqual(self.lx.lookup(surface).lemma, expected, surface)

    def test_exact_headword_beats_inflected_match(self):
        # `Essen` is the meal, not the dative plural of `Esse` (chimney-hood).
        self.assertEqual(self.lx.lookup("Essen").lemma, "Essen")

    def test_capitalization_separates_homographs(self):
        self.assertEqual(self.lx.lookup("essen").pos, "verb")
        self.assertEqual(self.lx.lookup("Essen").pos, "noun")

    def test_lowercase_form_is_not_matched_to_a_noun_headword(self):
        # German capitalizes nouns, so lowercase `freier` is the adjective.
        self.assertEqual(self.lx.lookup("freier").lemma, "frei")
        self.assertEqual(self.lx.lookup("Freier").lemma, "Freier")

    def test_common_words_rank_plausibly(self):
        # The regression that motivated ambiguity-split ranking put Haus at
        # rank 9884; anything outside the top 2000 here is a red flag.
        for word in ["Haus", "Wasser", "Frau", "gehen", "sprechen"]:
            rank = self.lx.lookup(word).rank
            self.assertIsNotNone(rank, word)
            self.assertLess(rank, 2000, f"{word} ranked {rank}")

    def test_rarer_words_rank_below_common_ones(self):
        self.assertGreater(
            self.lx.lookup("Schmetterling").rank, self.lx.lookup("Haus").rank
        )

    def test_names_are_flagged_not_scored_as_rare_vocabulary(self):
        entry = self.lx.lookup("davina")
        self.assertTrue(entry.foreign_or_name)
        self.assertFalse(entry.known_word)

    def test_personal_pronouns_resolve_to_themselves(self):
        # Wiktionary presents the German personal pronouns in one combined
        # table, so `ich`/`du`/`er` are all listed as forms of `ihr`. Weighting
        # frequency over exact headword match collapsed every pronoun onto
        # `Ihr` -- and pronouns are the commonest words in any subtitle file.
        sentence = "Ich sehe dich, er sieht uns, wir sehen sie."
        lemmas = [entry.lemma for entry in self.lx.analyze_tokens(sentence)]
        for pronoun in ["ich", "er", "wir"]:
            self.assertIn(pronoun, lemmas)
        self.assertNotIn("Ihr", lemmas)

    def test_sentence_initial_capital_does_not_imply_a_noun(self):
        first = self.lx.analyze_tokens("Ich bin hier.")[0]
        self.assertEqual(first.lemma, "ich")
        self.assertEqual(first.pos, "pron")
        # Mid-sentence, the same capitalized token really is the noun.
        self.assertEqual(self.lx.lookup("Ich").pos, "noun")

    def test_numerals_are_not_mistaken_for_verbs(self):
        # `acht` collides with the verb `achten` ("to pay attention").
        for word in ["acht", "elf", "hundert", "tausend"]:
            self.assertEqual(self.lx.lookup(word).lemma, word)

    def test_proper_names_are_not_mineable_vocabulary(self):
        for name in ["Schmidt", "Berlin"]:
            entry = self.lx.lookup(name)
            self.assertTrue(entry.foreign_or_name, name)
            self.assertFalse(entry.known_word, name)

    def test_everyday_nouns_that_are_also_surnames_stay_mineable(self):
        # Bauer/Koch/Stein/Berg are ordinary words first and surnames second;
        # excluding them would throw away real vocabulary.
        for word, expected in [
            ("Bauer", "Bauer"), ("Koch", "Koch"),
            ("Stein", "Stein"), ("Berg", "Berg"),
        ]:
            entry = self.lx.lookup(word)
            self.assertEqual(entry.lemma, expected)
            self.assertTrue(entry.known_word, word)
            self.assertEqual(entry.pos, "noun", word)

    def test_overrides_gloss_auxiliaries(self):
        entry = self.lx.lookup("ist")
        self.assertEqual(entry.source, "override")
        self.assertEqual(entry.gloss, "to be")

    def test_tokenize_handles_umlauts_and_clitics(self):
        self.assertEqual(
            lexicon.tokenize("Wie geht's, Mädchen? 42 mal!"),
            ["Wie", "geht's", "Mädchen", "mal"],
        )


@needs_lexicon
class TestScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lx = lexicon.Lexicon()

    def score(self, text, profile=None):
        return score.score_sentence(
            Sentence(text=text, start=0.0, end=2.0), self.lx, profile
        )

    def test_rare_words_raise_difficulty(self):
        easy = self.score("Ich habe ein Haus.")
        hard = self.score("Die Wechselwirkung erfordert Verantwortung.")
        self.assertLess(easy.difficulty, hard.difficulty)

    def test_longer_sentences_are_harder_all_else_equal(self):
        short = self.score("Ich gehe.")
        long = self.score(
            "Ich gehe heute mit meiner Frau und meinem Kind in die Stadt."
        )
        self.assertLess(short.difficulty, long.difficulty)

    def test_subordinate_clause_adds_complexity(self):
        plain = self.score("Er kommt nach Hause.")
        subordinate = self.score("Er sagt, dass er nach Hause kommt.")
        self.assertGreater(subordinate.complexity, plain.complexity)

    def test_unknown_words_measured_against_profile(self):
        profile = Profile(known={"ich", "haben", "ein"})
        item = self.score("Ich habe ein Haus.", profile)
        self.assertEqual(item.unknown_lemmas, ["Haus"])
        self.assertEqual(item.unknown_count, 1)

    def test_names_excluded_from_unknown_words(self):
        profile = Profile(known={"ich", "heißen"})
        item = self.score("Ich heiße Davina.", profile)
        self.assertNotIn("Davina", item.unknown_lemmas)

    def test_corpus_stats_reports_comprehension(self):
        profile = Profile(known={"ich", "haben", "ein", "Haus"})
        scored = [self.score("Ich habe ein Haus.", profile)]
        stats = score.corpus_stats(scored, profile)
        self.assertEqual(stats["comprehension"], 100.0)
        self.assertEqual(stats["sentences"], 1)

    def test_round_trip_through_dict(self):
        item = self.score("Ich habe ein Haus.")
        restored = score.ScoredSentence.from_dict(item.to_dict())
        self.assertEqual(restored.text, item.text)
        self.assertEqual(len(restored.entries), len(item.entries))
        self.assertEqual(restored.entries[0].lemma, item.entries[0].lemma)


@needs_lexicon
class TestSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lx = lexicon.Lexicon()

    def build(self, texts, known):
        profile = Profile(known=set(known))
        sentences = [
            Sentence(text=text, start=float(i * 5), end=float(i * 5 + 3))
            for i, text in enumerate(texts)
        ]
        return score.score_all(sentences, self.lx, profile), profile

    def test_i_plus_1_keeps_only_single_unknown_sentences(self):
        scored, profile = self.build(
            [
                "Ich habe ein Haus gekauft.",          # 1 unknown: Haus
                "Die Wechselwirkung erfordert Mut.",   # several unknown
            ],
            known=["ich", "haben", "ein", "kaufen", "die", "Mut"],
        )
        chosen = select.select_i_plus_1(scored, self.lx, profile, min_tokens=3)
        self.assertTrue(all(item.unknown_count == 1 for item in chosen))
        self.assertIn("Haus", [item.target for item in chosen])

    def test_i_plus_1_orders_useful_words_first(self):
        scored, profile = self.build(
            [
                "Er hat einen Schmetterling gesehen.",
                "Er hat ein Haus gesehen.",
            ],
            known=["er", "haben", "ein", "einen", "sehen"],
        )
        chosen = select.select_i_plus_1(scored, self.lx, profile, min_tokens=3)
        self.assertEqual(
            [item.target for item in chosen], ["Haus", "Schmetterling"]
        )

    def test_i_plus_1_emits_one_sentence_per_target(self):
        scored, profile = self.build(
            [
                "Ich sehe ein Haus.",
                "Du siehst ein Haus.",
                "Wir sehen ein Haus dort.",
            ],
            known=["ich", "du", "wir", "sehen", "ein", "dort"],
        )
        chosen = select.select_i_plus_1(scored, self.lx, profile, min_tokens=3)
        self.assertEqual(len(chosen), 1)

    def test_already_mined_words_are_skipped(self):
        scored, profile = self.build(
            ["Ich sehe ein Haus."], known=["ich", "sehen", "ein"]
        )
        profile.record_export("Haus", "guid-1")
        self.assertEqual(
            select.select_i_plus_1(scored, self.lx, profile, min_tokens=3), []
        )

    def test_coverage_mode_prefers_sentences_teaching_more(self):
        scored, profile = self.build(
            [
                "Ich sehe ein Haus.",
                "Ich sehe ein Haus und eine Frau.",
            ],
            known=["ich", "sehen", "ein", "eine", "und"],
        )
        chosen = select.select_coverage(scored, self.lx, profile, min_tokens=3)
        self.assertTrue(chosen)
        self.assertIn("Frau", chosen[0].extras["teaches"])

    def test_difficulty_mode_is_ordered_easy_to_hard(self):
        scored, _ = self.build(
            [
                "Die Wechselwirkung erfordert Verantwortung heute.",
                "Ich habe ein Haus.",
            ],
            known=[],
        )
        chosen = select.select_by_difficulty(scored, self.lx, limit=10, min_tokens=3)
        self.assertEqual(
            [item.difficulty for item in chosen],
            sorted(item.difficulty for item in chosen),
        )


class TestAlignment(unittest.TestCase):
    def spans(self, triples):
        return [
            Sentence(text=text, start=start, end=end) for text, start, end in triples
        ]

    def test_matches_overlapping_sentences(self):
        german = self.spans([("Hallo.", 1.0, 3.0), ("Tschüss.", 10.0, 12.0)])
        english = self.spans([("Hello.", 1.0, 3.0), ("Bye.", 10.0, 12.0)])
        translations, report = align.align(german, english, offset=0.0)
        self.assertEqual(translations, {0: "Hello.", 1: "Bye."})
        self.assertEqual(report.matched, 2)

    def test_estimates_constant_offset(self):
        german = self.spans([("A.", 10.0, 12.0), ("B.", 20.0, 22.0),
                             ("C.", 30.0, 32.0)])
        english = self.spans([("A!", 13.0, 15.0), ("B!", 23.0, 25.0),
                              ("C!", 33.0, 35.0)])
        self.assertAlmostEqual(align.estimate_offset(german, english), -3.0, places=1)

    def test_aligns_despite_drift(self):
        german = self.spans([("A.", 10.0, 12.0), ("B.", 20.0, 22.0)])
        english = self.spans([("A!", 15.0, 17.0), ("B!", 25.0, 27.0)])
        translations, report = align.align(german, english)
        self.assertEqual(translations, {0: "A!", 1: "B!"})
        self.assertAlmostEqual(report.offset, -5.0, places=1)

    def test_missing_reference_is_not_an_error(self):
        translations, report = align.align(self.spans([("A.", 0.0, 1.0)]), [])
        self.assertEqual(translations, {})
        self.assertEqual(report.matched, 0)

    def test_sample_files_align(self):
        from german_ci import subtitles

        german = subtitles.load(os.path.join(SAMPLES, "sample.de.srt"))
        english = align.load_reference(os.path.join(SAMPLES, "sample.en.srt"))
        translations, report = align.align(german, english)
        self.assertGreater(report.rate, 80.0)
        self.assertIn("interesting film", translations[0])


class TestAnkiExport(unittest.TestCase):
    def make(self, text, target, surface, gloss="house"):
        item = score.ScoredSentence.from_dict(
            {
                "text": text,
                "translation": "I bought a house.",
                "start": 4.0,
                "end": 8.0,
                "difficulty": 20.0,
                "target": target,
                "unknown": [target],
                "words": [
                    {
                        "surface": surface,
                        "lemma": target,
                        "pos": "noun",
                        "gloss": gloss,
                        "cefr": "A1",
                        "rank": 298,
                        "scoreable": True,
                    }
                ],
            }
        )
        return item

    def test_guid_is_stable_across_runs(self):
        first = anki.note_guid("show.srt", "00:00:04", "Ich habe ein Haus.")
        second = anki.note_guid("show.srt", "00:00:04", "Ich habe ein Haus.")
        self.assertEqual(first, second)

    def test_guid_varies_with_content(self):
        self.assertNotEqual(
            anki.note_guid("show.srt", "00:00:04", "Ich habe ein Haus."),
            anki.note_guid("show.srt", "00:00:09", "Ich habe ein Haus."),
        )

    def test_highlight_bolds_the_inflected_surface_form(self):
        item = self.make("Ich sah Häuser dort.", "Haus", "Häuser")
        marked = anki.highlight(item.text, item.entries, "Haus")
        self.assertIn("<b>Häuser</b>", marked)

    def test_highlight_escapes_html(self):
        item = self.make("Er sagte <sehr> laut.", "laut", "laut")
        marked = anki.highlight(item.text, item.entries, "laut")
        self.assertIn("&lt;sehr&gt;", marked)
        self.assertNotIn("<sehr>", marked)

    def test_tsv_export_writes_header_and_rows(self):
        item = self.make("Ich habe ein Haus.", "Haus", "Haus")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "out.tsv")
            result = anki.export_tsv([item], "show.srt", path)
            self.assertEqual(result["notes"], 1)
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        self.assertEqual(lines[0].split("\t"), anki.FIELDS)
        self.assertIn("Haus", lines[1])

    def test_apkg_export_produces_a_file(self):
        try:
            import genanki  # noqa: F401
        except ImportError:
            self.skipTest("genanki not installed")
        item = self.make("Ich habe ein Haus.", "Haus", "Haus")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "deck.apkg")
            result = anki.export_apkg([item], "show.srt", path)
            self.assertEqual(result["notes"], 1)
            self.assertGreater(os.path.getsize(path), 0)


class TestProfile(unittest.TestCase):
    def test_marking_unknown_survives_a_reseed(self):
        class FakeLexicon:
            def top_lemmas(self, limit):
                return ["ich", "haben", "Haus"][:limit]

        profile = Profile()
        profile.mark_unknown("Haus")
        profile.seed_from_level(FakeLexicon(), "A1")
        self.assertTrue(profile.knows("ich"))
        self.assertFalse(profile.knows("Haus"))

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "profile.json")
            profile = Profile(level="B1", known={"ich", "Haus"}, path=path)
            profile.record_export("Haus", "guid-1")
            profile.save()

            restored = Profile.load(path)
            self.assertEqual(restored.level, "B1")
            self.assertEqual(restored.known, {"ich", "Haus"})
            self.assertTrue(restored.already_mined("Haus"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
