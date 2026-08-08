from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EmailDraftContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8-sig")
        cls.agent = (ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8-sig"
        )

    def test_explicit_draft_request_is_required_for_external_mutation(self) -> None:
        self.assertIn(
            "A request for `文面`, `メール案`, or `返信案` authorizes text generation only.",
            self.skill,
        )
        self.assertIn("authority to create exactly one reversible draft", self.skill)

    def test_draft_creation_and_update_are_distinct_from_sending(self) -> None:
        self.assertIn("create_draft", self.skill)
        self.assertIn("update the referenced draft in place", self.skill)
        self.assertIn("does not edit drafts that already contain attachments", self.skill)
        self.assertIn("saved draft was not changed", self.skill)
        self.assertIn("Creating or updating a draft never authorizes sending it.", self.skill)
        self.assertIn("Do not call an email-send or draft-send action", self.skill)

    def test_reply_drafts_require_a_real_message_id(self) -> None:
        self.assertIn("actual Gmail message ID as `reply_message_id`", self.skill)
        self.assertIn("never substitute a thread ID", self.skill)

    def test_unavailable_gmail_falls_back_without_false_success(self) -> None:
        self.assertIn("state that it was not saved", self.skill)
        self.assertIn("returned Gmail draft identifier", self.skill)
        self.assertIn("do not claim success from prose generation alone", self.skill)

    def test_ui_metadata_exposes_draft_behavior_and_send_boundary(self) -> None:
        self.assertIn("Gmail下書き", self.agent)
        self.assertIn("only when explicitly requested", self.agent)
        self.assertIn("without sending", self.agent)


if __name__ == "__main__":
    unittest.main()
