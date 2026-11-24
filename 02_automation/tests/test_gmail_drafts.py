import base64
from email import message_from_bytes, policy
from unittest.mock import MagicMock

import pytest

from gmail_drafts import DRAFTS_URL, create_draft, find_email, raw_message


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apply: jobs@acme.io today", "jobs@acme.io"),
        ("mail jane.doe+hn@acme.co.uk.", "jane.doe+hn@acme.co.uk"),
        ("jobs [at] acme [dot] com", "jobs@acme.com"),
        ("jobs (at) acme (dot) io", "jobs@acme.io"),
        ("hiring {at} big-co {dot} dev", "hiring@big-co.dev"),
        ("Apply at https://acme.io/careers", None),
        ("we are hiring at acme.com", None),
    ],
)
def test_find_email_reads_plain_and_obfuscated_addresses(text, expected):
    assert find_email(text) == expected


def test_raw_message_is_a_base64url_mime_email():
    raw = raw_message("jobs@acme.io", "Data Engineer — Acme", "Hey ñandú,\nhi")

    message = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert message["To"] == "jobs@acme.io"
    assert message["Subject"] == "Data Engineer — Acme"
    assert message.get_content().strip() == "Hey ñandú,\nhi"


def test_raw_message_without_a_recipient_leaves_to_empty():
    raw = raw_message(None, "s", "b")

    assert (
        message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)["To"]
        is None
    )


def test_create_draft_posts_the_raw_message_and_returns_the_draft_id():
    session = MagicMock()
    session.post.return_value = MagicMock(ok=True, json=lambda: {"id": "r-123"})

    assert create_draft(session, "a@b.io", "s", "body") == "r-123"
    assert session.post.call_args.args[0] == DRAFTS_URL
    raw = session.post.call_args.kwargs["json"]["message"]["raw"]
    assert (
        message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)["To"]
        == "a@b.io"
    )


def test_create_draft_raises_on_an_api_error():
    session = MagicMock()
    session.post.return_value = MagicMock(ok=False, status_code=403, text="denied")

    with pytest.raises(RuntimeError, match="403"):
        create_draft(session, None, "s", "b")
