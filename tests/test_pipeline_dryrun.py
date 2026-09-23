"""
Exercises the full generate -> validate -> regenerate loop with a fake Gemini
client AND a fake in-memory db (no network, no API key, no Supabase project
needed) so the pipeline's control flow can be proven correct in isolation.
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

BAD_BODY_BANNED_WORD = GOOD_BODY + " This is a total game-changer."


class FakeGeminiClient:
    """Replaces services.gemini_client.GeminiClient. `responses` is a queue consumed
    in order: draft attempt 1, critique 1, draft attempt 2, critique 2, ..."""

    def __init__(self, responses):
        self._responses = list(responses)

    def generate_json(self, *, system_instruction, contents):
        return self._responses.pop(0)


class FakeDB:
    """Replaces db.py's Supabase calls with an in-memory dict, keyed like the
    real table's `id` column, so pipeline tests don't need a live Supabase
    project or network access."""

    def __init__(self):
        self._rows: dict[int, dict] = {}
        self._next_id = 1

    def save_draft(self, **kwargs) -> int:
        content_id = self._next_id
        self._next_id += 1
        self._rows[content_id] = {"id": content_id, **kwargs}
        return content_id

    def update_status(self, content_id, status, telegram_message_id=None) -> None:
        self._rows[content_id]["status"] = status

    def get_item(self, content_id):
        return self._rows.get(content_id)

    def recent_summaries(self, voice_skill, platform, limit=5):
        return []


class TestPipelineDryRun(unittest.TestCase):
    def setUp(self):
        self.fake_db = FakeDB()
        patcher = patch.multiple(
            "services.pipeline.db",
            save_draft=self.fake_db.save_draft,
            update_status=self.fake_db.update_status,
            get_item=self.fake_db.get_item,
            recent_summaries=self.fake_db.recent_summaries,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_passes_on_first_attempt(self):
        fake = FakeGeminiClient(
            responses=[
                {
                    "platform": "linkedin", "subject_line": None, "body": GOOD_BODY,
                    "placeholders_used": ["[INSERT: label %]"], "sources_cited": [],
                },
                {
                    "sounds_like_specific_person": True, "layers_add_mechanism": True,
                    "steelman_before_critique": True, "closing_is_action_or_pointer": True,
                    "issues": [], "verdict": "pass",
                },
            ]
        )
        with patch("services.pipeline.GeminiClient", return_value=fake):
            result = pipeline.generate_content(
                category="ingredient", platform="linkedin", topic="vitamin C stability",
            )
        self.assertTrue(result.passed)
        self.assertEqual(result.attempts, 1)
        self.assertIsNotNone(result.content_id)
        saved = self.fake_db.get_item(result.content_id)
        self.assertEqual(saved["status"], "draft")

    def test_regenerates_after_deterministic_failure_then_passes(self):
        fake = FakeGeminiClient(
            responses=[
                {
                    "platform": "linkedin", "subject_line": None, "body": BAD_BODY_BANNED_WORD,
                    "placeholders_used": [], "sources_cited": [],
                },
                # second attempt: fixed
                {
                    "platform": "linkedin", "subject_line": None, "body": GOOD_BODY,
                    "placeholders_used": ["[INSERT: label %]"], "sources_cited": [],
                },
                {
                    "sounds_like_specific_person": True, "layers_add_mechanism": True,
                    "steelman_before_critique": True, "closing_is_action_or_pointer": True,
                    "issues": [], "verdict": "pass",
                },
            ]
        )
        with patch("services.pipeline.GeminiClient", return_value=fake):
            result = pipeline.generate_content(
                category="ingredient", platform="linkedin", topic="vitamin C stability",
            )
        self.assertTrue(result.passed)
        self.assertEqual(result.attempts, 2)

    def test_exhausts_attempts_and_flags_for_review(self):
        fake = FakeGeminiClient(
            responses=[
                {
                    "platform": "linkedin", "subject_line": None, "body": BAD_BODY_BANNED_WORD,
                    "placeholders_used": [], "sources_cited": [],
                }
            ]
            * 6  # more than enough for max_generation_attempts (default 3) x1 call each
        )
        with patch("services.pipeline.GeminiClient", return_value=fake):
            result = pipeline.generate_content(
                category="ingredient", platform="linkedin", topic="vitamin C stability",
            )
        self.assertFalse(result.passed)
        saved = self.fake_db.get_item(result.content_id)
        self.assertEqual(saved["status"], "failed")


if __name__ == "__main__":
    unittest.main()
