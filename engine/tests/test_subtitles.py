"""Tests for cue parsing and sentence reconstruction."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from german_ci import subtitles  # noqa: E402

SAMPLES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples"
)


def srt(body: str) -> list[subtitles.Sentence]:
    return subtitles.merge_into_sentences(subtitles.parse_cues(body))


class TestParsing(unittest.TestCase):
    def test_parses_time_formats(self):
        self.assertAlmostEqual(subtitles.parse_time("00:00:04,500"), 4.5)
        self.assertAlmostEqual(subtitles.parse_time("01:02:03.250"), 3723.25)
        self.assertAlmostEqual(subtitles.parse_time("02:03.100"), 123.1)

    def test_handles_bom_and_crlf(self):
        body = "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nHallo Welt.\r\n"
        cues = subtitles.parse_cues(body)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text.strip(), "Hallo Welt.")

    def test_parses_vtt(self):
        body = (
            "WEBVTT\n\nNOTE something\n\n"
            "cue-1\n00:00:01.000 --> 00:00:02.000 line:0\nHallo Welt.\n"
        )
        cues = subtitles.parse_cues(body)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text.strip(), "Hallo Welt.")

    def test_drops_pure_annotation_cues(self):
        body = "1\n00:00:01,000 --> 00:00:02,000\n[Musik]\n"
        self.assertEqual(subtitles.parse_cues(body), [])

    def test_strips_tags_entities_and_ass(self):
        text = subtitles.clean_text("{\\an8}<i>Der Zug f&auml;hrt</i>")
        self.assertEqual(text.strip(), "Der Zug fährt")

    def test_rejoins_hyphenated_line_break(self):
        self.assertEqual(subtitles.clean_text("Ent-\nschuldigung"), "Entschuldigung")


class TestSentenceMerging(unittest.TestCase):
    def test_joins_sentence_split_across_cues(self):
        body = (
            "1\n00:00:04,000 --> 00:00:06,200\nIch habe gestern einen sehr\n\n"
            "2\n00:00:06,300 --> 00:00:08,900\ninteressanten Film gesehen.\n"
        )
        sentences = srt(body)
        self.assertEqual(len(sentences), 1)
        self.assertEqual(
            sentences[0].text, "Ich habe gestern einen sehr interessanten Film gesehen."
        )
        # Span covers both cues.
        self.assertAlmostEqual(sentences[0].start, 4.0)
        self.assertAlmostEqual(sentences[0].end, 8.9)

    def test_dialogue_dashes_split_speakers(self):
        body = "1\n00:00:09,200 --> 00:00:12,000\n- Wie war er?\n- Er war lang.\n"
        sentences = srt(body)
        self.assertEqual([s.text for s in sentences], ["Wie war er?", "Er war lang."])

    def test_ordinal_period_does_not_split(self):
        body = (
            "1\n00:00:12,400 --> 00:00:15,800\nAm 3. Oktober fahren wir\n"
            "nach Berlin.\n"
        )
        sentences = srt(body)
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0].text, "Am 3. Oktober fahren wir nach Berlin.")

    def test_abbreviation_does_not_split(self):
        body = "1\n00:00:20,000 --> 00:00:23,000\nEs wird gut, z.B. sonnig.\n"
        sentences = srt(body)
        self.assertEqual([s.text for s in sentences], ["Es wird gut, z.B. sonnig."])

    def test_title_abbreviation_does_not_split(self):
        body = "1\n00:00:16,000 --> 00:00:19,500\nWir treffen Dr. Schmidt heute.\n"
        sentences = srt(body)
        self.assertEqual(len(sentences), 1)

    def test_speaker_label_stripped(self):
        body = "1\n00:00:12,400 --> 00:00:15,800\nPETER: Wir fahren los.\n"
        self.assertEqual(srt(body)[0].text, "Wir fahren los.")

    def test_scene_gap_forces_boundary(self):
        # Two unpunctuated fragments 14s apart must not fuse.
        body = (
            "1\n00:00:04,000 --> 00:00:06,000\nEr geht nach Hause\n\n"
            "2\n00:00:20,000 --> 00:00:22,000\nund sie bleibt hier\n"
        )
        self.assertEqual(len(srt(body)), 2)

    def test_adjacent_fragments_still_join(self):
        body = (
            "1\n00:00:40,000 --> 00:00:43,000\nDer Zug fährt um acht Uhr ab\n\n"
            "2\n00:00:43,200 --> 00:00:46,000\nund kommt am Abend an.\n"
        )
        sentences = srt(body)
        self.assertEqual(len(sentences), 1)
        self.assertEqual(
            sentences[0].text, "Der Zug fährt um acht Uhr ab und kommt am Abend an."
        )

    def test_two_sentences_in_one_cue_split(self):
        body = "1\n00:00:01,000 --> 00:00:04,000\nEs regnet. Wir bleiben hier.\n"
        self.assertEqual(
            [s.text for s in srt(body)], ["Es regnet.", "Wir bleiben hier."]
        )

    def test_timestamp_formatting(self):
        sentence = subtitles.Sentence(text="x", start=3725.0, end=3730.0)
        self.assertEqual(sentence.timestamp(), "01:02:05")


class TestRollingCaptions(unittest.TestCase):
    """YouTube auto-captions scroll: each cue redisplays the previous line."""

    ROLLING = (
        "1\n00:00:00,880 --> 00:00:03,310\nAlkohol ist die Substanz\n\n"
        "2\n00:00:03,310 --> 00:00:03,320\nAlkohol ist die Substanz\n \n\n"
        "3\n00:00:03,320 --> 00:00:05,630\nAlkohol ist die Substanz\n"
        "der Welt. Jedes Jahr stirbt\n\n"
        "4\n00:00:05,630 --> 00:00:05,640\nder Welt. Jedes Jahr stirbt\n \n\n"
        "5\n00:00:05,640 --> 00:00:08,230\nder Welt. Jedes Jahr stirbt\n"
        "jemand daran.\n\n"
        "6\n00:00:08,230 --> 00:00:08,240\njemand daran.\n \n"
    )

    def test_detects_rolling_layout(self):
        cues = subtitles.parse_cues(self.ROLLING)
        text = " ".join(cue.text for cue in cues)
        # Each line must survive exactly once, not two or three times.
        self.assertEqual(text.count("Alkohol ist die Substanz"), 1)
        self.assertEqual(text.count("der Welt."), 1)
        self.assertEqual(text.count("jemand daran."), 1)

    def test_rolling_captions_produce_clean_sentences(self):
        sentences = subtitles.dedupe(
            subtitles.merge_into_sentences(subtitles.parse_cues(self.ROLLING))
        )
        texts = [s.text for s in sentences]
        self.assertIn("Alkohol ist die Substanz der Welt.", texts)
        self.assertIn("Jedes Jahr stirbt jemand daran.", texts)

    def test_ordinary_subtitles_are_left_alone(self):
        # A legitimately repeated line must not be swallowed as a rolling dup.
        body = "".join(
            f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nZeile {i}.\n\n"
            for i in range(1, 9)
        )
        self.assertEqual(len(subtitles.parse_cues(body)), 8)


class TestPunctuationRatio(unittest.TestCase):
    def test_punctuated_transcript_scores_high(self):
        items = [
            subtitles.Sentence(text="Es regnet.", start=0, end=1),
            subtitles.Sentence(text="Wie geht's?", start=2, end=3),
        ]
        self.assertEqual(subtitles.punctuation_ratio(items), 1.0)

    def test_bare_word_stream_scores_low(self):
        items = [
            subtitles.Sentence(text="es regnet und dann", start=0, end=1),
            subtitles.Sentence(text="gehen wir nach hause", start=2, end=3),
        ]
        self.assertEqual(subtitles.punctuation_ratio(items), 0.0)


class TestDedupe(unittest.TestCase):
    def test_removes_repeats_ignoring_case_and_punctuation(self):
        items = [
            subtitles.Sentence(text="Hoffentlich regnet es nicht.", start=0, end=1),
            subtitles.Sentence(text="hoffentlich regnet es nicht", start=9, end=10),
            subtitles.Sentence(text="Etwas anderes.", start=20, end=21),
        ]
        self.assertEqual(len(subtitles.dedupe(items)), 2)


class TestSampleFile(unittest.TestCase):
    def test_end_to_end_sample(self):
        sentences = subtitles.load(os.path.join(SAMPLES, "sample.de.srt"))
        texts = [s.text for s in sentences]

        self.assertIn(
            "Ich habe gestern einen sehr interessanten Film gesehen.", texts
        )
        self.assertIn("Am 3. Oktober fahren wir nach Berlin.", texts)
        self.assertIn(
            "Der Zug fährt um acht Uhr ab und kommt am Abend an.", texts
        )
        # SDH cue dropped, speaker labels gone, duplicate line removed.
        self.assertFalse(any("Musik" in t for t in texts))
        self.assertFalse(any(t.startswith("PETER") for t in texts))
        self.assertEqual(sum("Hoffentlich regnet" in t for t in texts), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
