"""Gmail drafts through the official API: creates drafts, never sends them.

OAuth scope gmail.compose; the client secret and the token live in
02_automation/.secrets/ (git-ignored). The first run opens the browser to
authorize; later runs refresh the token on their own.
Setup: README "Gmail drafts", or cold_outreach.py --gmail-drafts --help.
"""

import base64
import re
from email.message import EmailMessage
from pathlib import Path

import requests

SECRETS_DIR = Path(__file__).with_name(".secrets")
CLIENT_SECRET = SECRETS_DIR / "gmail_client_secret.json"
TOKEN = SECRETS_DIR / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
TIMEOUT_S = 30

# plain "a@b.io" or HN-style "a [at] b [dot] io" / "(at)" / "{at}"
_AT = r"(?:@|\s*[\[({]at[\])}]\s*)"
_DOT = r"(?:\.|\s*[\[({]dot[\])}]\s*)"
EMAIL = re.compile(
    rf"([\w.+-]+){_AT}([\w-]+(?:{_DOT}[\w-]+)*{_DOT}[a-z]{{2,}})\b", re.IGNORECASE
)
DOT_WORD = re.compile(_DOT, re.IGNORECASE)


def find_email(text: str) -> str | None:
    """First contact address in a post, de-obfuscated; None if there is none."""
    match = EMAIL.search(text)
    if not match:
        return None
    user, domain = match.groups()
    return f"{user}@{DOT_WORD.sub('.', domain)}"


def raw_message(to: str | None, subject: str, body: str) -> str:
    """RFC 2822 message, base64url as the Gmail API wants it."""
    message = EmailMessage()
    if to:
        message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def create_draft(
    session: requests.Session, to: str | None, subject: str, body: str
) -> str:
    """Draft id; RuntimeError if Gmail answers an error."""
    response = session.post(
        DRAFTS_URL,
        json={"message": {"raw": raw_message(to, subject, body)}},
        timeout=TIMEOUT_S,
    )
    if not response.ok:
        raise RuntimeError(f"gmail {response.status_code}: {response.text[:200]}")
    return response.json()["id"]


def gmail_session() -> requests.Session:  # pragma: no cover - needs a browser
    """Authorized session; opens the OAuth consent page the first time."""
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = (
        Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        if TOKEN.exists()
        else None
    )
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            raise FileNotFoundError(f"OAuth client missing: {CLIENT_SECRET}")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0)
    SECRETS_DIR.mkdir(exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return AuthorizedSession(creds)
