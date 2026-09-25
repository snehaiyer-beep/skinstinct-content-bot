"""
Exercises generate_from_note(): the scoring gate, the news-angle lookup, and
the deterministic verify-block append — all with a fake Gemini client and an
in-memory db, no network or API key needed.
"""
import unittest
from unittest.mock import patch

from services import pipeline

GOOD_BODY = (
    "That number is almost certainly meaningless without the pH it was measured at.\n\n"
    "In 2021 I was sitting in a stability review meeting looking at a Vitamin C batch "
    "that had failed for the third time. The actives were intact. The formulation was "
    "working as designed. It was designed for the wrong context.\n\n"
    "I'm not saying every brand that skips this step is acting in bad faith. What I'm "
    "saying is that a label claiming [INSERT: label %] Vitamin C tells you nothing "
    "about whether it was stable at pH 3.5 by the time it reached a warehouse.\n\n"
    "Ask your supplier what pH the formulation was validated at. That's useful "
    "information either way."
)

CRITIQUE_PASS = {
    "sounds_like_specific_person": True, "layers_add_mechanism": True,
    "steelman_before_critique": True, "closing_is_action_or_pointer": True,
    "issues": [], "verdict": "pass",
}

FAKE_NEWS_ITEM = {
    "headline": "New study finds Vitamin C degrades faster than labels suggest",
    "source": "Example Times",
    "date": "2026-09-20",
    "link": "https://example.com/article",
    "summary": "A new study on Vitamin C stability.",
}


class FakeGeminiClient:
    """Queue consumed in call order: score, [keywords, draft, critique]*n."""

    def __init__(self, responses):
        self._responses = list(responses)

    def generate_json(self, *, system_instruction, contents):
        return self._responses.pop(0)


class FakeDB:
    def __init__(self):
        self._notes: dict[int, dict] = {}
        self._drafts: dict[int, dict] = {}
        self._next_note_id = 1
        self._next_draft_id = 1

    def save_note(self, **kwargs) -> int:
        note_id = self._next_note_id
        self._next_note_id += 1
        self._notes[note_id] = {"id": note_id, **kwargs}
        return note_id

    def update_note_score(self, note_id, **kwargs) -> None:
        self._notes[note_id].update(kwargs)

    def get_note(self, note_id):
        return self._notes.get(note_id)

    def save_draft(self, **kwargs) -> int:
        draft_id = self._next_draft_id
        self._next_draft_id += 1
        self._drafts[draft_id] = {"id": draft_id, **kwargs}
        return draft_id

    def update_status(self, content_id, status, telegram_message_id=None) -> None:
        self._drafts[content_id]["status"] = status

    def get_item(self, content_id):
        return self._drafts.get(content_id)

    def recent_summaries(self, voice_skill, platform, limit=5):
        return []


class TestNotePipeline(unittest.TestCase):
    def setUp(self):
        self.fake_db = FakeDB()
        patcher = patch.multiple(
            "services.pipeline.db",
            save_note=self.fake_db.save_note,
            update_note_score=self.fake_db.update_note_score,
            get_note=self.fake_db.get_note,
            save_draft=self.fake_db.save_draft,
            update_status=self.fake_db.update_status,
            get_item=self.fake_db.get_item,
            recent_summaries=self.fake_db.recent_summaries,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_weak_note_rejected_at_scoring_no_draft_made(self):
        """A task reminder / abandoned thought should score low and produce no draft."""
        fake = FakeGeminiClient(responses=[
            {"score": 2, "reason": "This is a task reminder, not an idea for a piece."},
        ])
        with patch("services.pipeline.GeminiClient", return_value=fake):
            result = pipeline.generate_from_note("follow up with Priya about the shipment tomorrow")

        self.assertFalse(result.accepted)
        self.assertLessEqual(result.score, 3)
        self.assertTrue(result.score_reason)
        self.assertIsNone(result.content_id)
        # note is still saved (rejected notes are kept, not deleted)
        saved_note = self.fake_db.get_note(result.note_id)
        self.assertEqual(saved_note["status"], "scored_reject")
        # no draft was ever created
        self.assertEqual(len(self.fake_db._drafts), 0)

    def test_strong_note_passes_scoring_and_drafts_without_news(self):
        fake = FakeGeminiClient(responses=[
            {"score": 8, "reason": "Specific stability failure with a concrete lesson."},
            {"search_phrase": "vitamin C serum stability pH"},
            {
                "platform": "linkedin", "subject_line": None, "body": GOOD_BODY,
                "placeholders_used": ["[INSERT: label %]"], "sources_cited": [],
                "used_news_item": False,
            },
            CRITIQUE_PASS,
        ])
        with patch("services.pipeline.GeminiClient", return_value=fake), \
             patch("services.pipeline.news.fetch_top_news", return_value=None):
            result = pipeline.generate_from_note(
                "In 2021 our Vitamin C batch failed stability for the third time because "
                "we used the wrong base for Indian humidity — took 14 months to fix."
            )

        self.assertTrue(result.accepted)
        self.assertGreaterEqual(result.score, 6)
        self.assertTrue(result.passed_validation)
        self.assertIsNotNone(result.content_id)
        self.assertFalse(result.used_news_item)
        saved = self.fake_db.get_item(result.content_id)
        self.assertEqual(saved["status"], "pending")

    def test_news_item_used_appends_deterministic_verify_block(self):
        fake = FakeGeminiClient(responses=[
            {"score": 9, "reason": "Rich, specific, concrete numbers."},
            {"search_phrase": "vitamin C stability study"},
            {
                "platform": "linkedin", "subject_line": None, "body": GOOD_BODY,
                "placeholders_used": ["[INSERT: label %]"], "sources_cited": [],
                "used_news_item": True,
            },
            CRITIQUE_PASS,
        ])
        with patch("services.pipeline.GeminiClient", return_value=fake), \
             patch("services.pipeline.news.fetch_top_news", return_value=FAKE_NEWS_ITEM):
            result = pipeline.generate_from_note(
                "In 2021 our Vitamin C batch failed stability for the third time — "
                "wrong base for Indian humidity, took 14 months to fix."
            )

        self.assertTrue(result.accepted)
        self.assertTrue(result.used_news_item)
        self.assertIn("NEWS SOURCE: New study finds Vitamin C degrades faster than labels suggest", result.body)
        self.assertIn("FROM: Example Times", result.body)
        self.assertIn("2026-09-20", result.body)
        self.assertIn("https://example.com/article", result.body)
        self.assertIn("Check this before publishing", result.body)

    def test_news_lookup_failure_does_not_block_drafting(self):
        """A broken news lookup should never prevent a draft from being made."""
        fake = FakeGeminiClient(responses=[
            {"score": 8, "reason": "Specific and substantive."},
            {"search_phrase": "vitamin C stability"},
            {
                "platform": "linkedin", "subject_line": None, "body": GOOD_BODY,
                "placeholders_used": ["[INSERT: label %]"], "sources_cited": [],
                "used_news_item": False,
            },
            CRITIQUE_PASS,
        ])
        with patch("services.pipeline.GeminiClient", return_value=fake), \
             patch("services.pipeline.news.fetch_top_news", side_effect=RuntimeError("network down")):
            result = pipeline.generate_from_note(
                "Our Vitamin C batch failed stability testing three times before we fixed it."
            )

        self.assertTrue(result.accepted)
        self.assertTrue(result.passed_validation)
        self.assertFalse(result.used_news_item)


if __name__ == "__main__":
    unittest.main()
