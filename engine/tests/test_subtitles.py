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
