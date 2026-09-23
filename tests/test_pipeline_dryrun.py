"""
Exercises the full generate -> validate -> regenerate loop with a fake Gemini
client (no network, no API key) so the pipeline's control flow can be proven
correct before real keys exist.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
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


class TestPipelineDryRun(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

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
        saved = db.get_item(result.content_id)
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
        saved = db.get_item(result.content_id)
        self.assertEqual(saved["status"], "failed")


if __name__ == "__main__":
    unittest.main()
