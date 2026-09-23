import unittest

from services import validator

GOOD_LINKEDIN_BODY = (
    "That number is almost certainly meaningless without the pH it was measured at.\n\n"
    "In 2021 I was sitting in a stability review meeting looking at a Vitamin C batch "
    "that had failed for the third time. The actives were intact. The formulation was "
    "working as designed. It was designed for the wrong context.\n\n"
    "I'm not saying every brand that skips this step is acting in bad faith. What I'm "
    "saying is that a label claiming 15% Vitamin C tells you nothing about whether it "
    "was stable at pH 3.5 by the time it reached a warehouse in Chennai in June.\n\n"
    "Ask your supplier what pH the formulation was validated at, and for how many months. "
    "That's useful information either way."
)

GOOD_NEWSLETTER_BODY = (
    "Hi,\n\n"
    "A customer wrote to me last month asking why our niacinamide serum has a "
    "cooling sensation and whether that meant it was working.\n\n"
    "Niacinamide converts to niacin at low pH. Niacin is not inert. It causes "
    "vasodilation. In skin terms, that's the flushing reaction some people report, "
    "usually at concentrations above 5%.\n\n"
    "I'm not saying the sensation means nothing. I'm saying it isn't proof of "
    "efficacy either way, and the two get conflated constantly.\n\n"
    "More on specific actives in the next few emails.\n\n"
    "Meera"
)


class TestValidator(unittest.TestCase):
    def test_good_linkedin_passes(self):
        result = validator.validate(
            platform="linkedin", body=GOOD_LINKEDIN_BODY, subject_line=None,
            allow_unbracketed_numbers=True,
        )
        self.assertTrue(result.passed, result.violations)

    def test_good_newsletter_passes(self):
        result = validator.validate(
            platform="newsletter", body=GOOD_NEWSLETTER_BODY, subject_line="pH and flushing",
            allow_unbracketed_numbers=True,
        )
        self.assertTrue(result.passed, result.violations)

    def test_banned_word_fails(self):
        body = GOOD_LINKEDIN_BODY + " This is a total game-changer for skincare."
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("Banned vocabulary" in v for v in result.violations))

    def test_emoji_fails(self):
        body = GOOD_LINKEDIN_BODY + " 🌟"
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("emoji" in v.lower() for v in result.violations))

    def test_bullet_list_fails(self):
        body = GOOD_LINKEDIN_BODY + "\n- first point\n- second point"
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("bullet" in v.lower() for v in result.violations))

    def test_missing_number_fails(self):
        body = (
            "I'm not saying this is simple. What I'm saying is that formulation "
            "chemistry is harder than a label makes it look.\n\n"
            "Ask your supplier for the documentation. That's useful information."
        )
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("concrete figure" in v for v in result.violations))

    def test_missing_narrowing_move_fails(self):
        body = GOOD_LINKEDIN_BODY.replace(
            "I'm not saying every brand that skips this step is acting in bad faith. "
            "What I'm saying is that a label claiming 15% Vitamin C tells you nothing "
            "about whether it was stable at pH 3.5 by the time it reached a warehouse "
            "in Chennai in June.",
            "A label claiming 15% Vitamin C tells you nothing about stability at pH 3.5.",
        )
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("narrowing disclaimer" in v for v in result.violations))

    def test_fabricated_stat_without_placeholder_fails(self):
        body = GOOD_LINKEDIN_BODY + " Our return rate dropped by pH 4.2 last quarter."
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=False,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("fabricated" in v.lower() for v in result.violations))

    def test_placeholder_is_allowed_without_real_data(self):
        body = GOOD_LINKEDIN_BODY.replace(
            "a label claiming 15% Vitamin C", "a label claiming [INSERT: label %] Vitamin C"
        )
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=False,
        )
        self.assertTrue(result.passed, result.violations)

    def test_newsletter_missing_subject_fails(self):
        result = validator.validate(
            platform="newsletter", body=GOOD_NEWSLETTER_BODY, subject_line=None,
            allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("subject line" in v.lower() for v in result.violations))

    def test_linkedin_greeting_fails(self):
        body = "Hi, today I want to talk about pH.\n\n" + GOOD_LINKEDIN_BODY
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("open cold" in v.lower() for v in result.violations))

    def test_summary_closing_fails(self):
        body = GOOD_LINKEDIN_BODY + " And that's why transparency matters in skincare."
        result = validator.validate(
            platform="linkedin", body=body, subject_line=None, allow_unbracketed_numbers=True,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("moralizing summary" in v for v in result.violations))


if __name__ == "__main__":
    unittest.main()
