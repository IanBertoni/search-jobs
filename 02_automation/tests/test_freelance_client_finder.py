import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from freelance_client_finder import (
    PITCH_PROMPT,
    github_clients,
    github_queries,
    hn_clients,
    latest_freelancer_thread,
    reddit_clients,
    reddit_token,
    render,
    write_pitches,
)

FIXTURES = Path(__file__).parent / "fixtures"
REDDIT = json.loads((FIXTURES / "reddit_search.json").read_text("utf-8"))
GITHUB = json.loads((FIXTURES / "github_issues_search.json").read_text("utf-8"))
PITCH = "Hola, vi que necesitan un chatbot RAG. github.com/IanBertoni ¿10 minutos?"


def groq_session(*contents: str) -> MagicMock:
    replies = [
        MagicMock(
            ok=True,
            json=MagicMock(return_value={"choices": [{"message": {"content": c}}]}),
        )
        for c in contents
    ]
    session = MagicMock()
    session.post.side_effect = replies
    return session


def test_reddit_keeps_hiring_posts_with_target_skills_only():
    clients = reddit_clients(REDDIT)

    assert [c["autor"] for c in clients] == ["saas_founder", "shop_owner"]
    assert clients[0] == {
        "fuente": "Reddit r/forhire",
        "titulo": "[Hiring] Python dev to build a RAG chatbot over our docs",
        "autor": "saas_founder",
        "link": "https://www.reddit.com/r/forhire/comments/abc/hiring_python_dev/",
        "texto": "[Hiring] Python dev to build a RAG chatbot over our docs\nBudget $1500. Remote, anywhere.",
    }


def test_reddit_malformed_listing_gives_no_clients():
    assert reddit_clients({"oops": 1}) == []


def test_reddit_token_is_none_without_credentials():
    session = MagicMock()

    assert reddit_token(session, "", "") is None
    session.post.assert_not_called()


def thread(*texts: str) -> dict:
    return {
        "children": [
            {"id": i, "author": f"a{i}", "text": t, "children": []}
            for i, t in enumerate(texts)
        ]
    }


def test_hn_keeps_seeking_freelancer_posts_only():
    clients = hn_clients(
        thread(
            "SEEKING FREELANCER | Remote<p>Need a Python dev for an LLM pipeline.",
            "SEEKING WORK | Argentina<p>Python, RAG, FastAPI.",
            "SEEKING FREELANCER | Remote US only<p>Python API work.",
            "SEEKING FREELANCER | Remote<p>Wordpress theme tweaks.",
        )
    )

    assert [c["autor"] for c in clients] == ["a0"]
    assert clients[0]["fuente"] == "HN Seeking freelancer"
    assert clients[0]["link"] == "https://news.ycombinator.com/item?id=0"


@pytest.mark.parametrize(
    "text",
    ["Senior Python dev needed", "Python API, 5+ years", "Python scraper, US only"],
)
def test_dealbreakers_drop_the_post(text):
    listing = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": f"[Hiring] {text}",
                        "selftext": "",
                        "author": "x",
                        "subreddit": "forhire",
                        "permalink": "/r/x/",
                    }
                }
            ]
        }
    }

    assert reddit_clients(listing) == []


def test_write_pitches_calls_groq_once_per_client_and_drops_skip():
    clients = reddit_clients(REDDIT)
    session = groq_session(PITCH, "SKIP")

    pitched = write_pitches(clients, session)

    assert session.post.call_count == 2
    assert [p["autor"] for p in pitched] == ["saas_founder"]
    assert pitched[0]["pitch"] == PITCH


def test_write_pitches_skips_a_client_when_groq_fails():
    clients = reddit_clients(REDDIT)
    session = groq_session(PITCH)
    session.post.side_effect = [
        MagicMock(ok=False, status_code=500, text="boom"),
        *session.post.side_effect,
    ]

    assert [p["autor"] for p in write_pitches(clients, session)] == ["shop_owner"]


def test_render_lists_each_client_with_link_and_pitch():
    clients = [{**reddit_clients(REDDIT)[0], "pitch": PITCH}]

    text = render(clients)

    assert "## [Hiring] Python dev to build a RAG chatbot over our docs" in text
    assert "https://www.reddit.com/r/forhire/comments/abc/hiring_python_dev/" in text
    assert PITCH in text


def test_latest_freelancer_thread_takes_the_newest_matching_title():
    hits = {
        "hits": [
            {"objectID": "9", "title": "Show HN: Seeking freelancer tool"},
            {
                "objectID": "50338744",
                "title": "Ask HN: Freelancer? Seeking freelancer? (November 2026)",
            },
            {
                "objectID": "49922572",
                "title": "Ask HN: Freelancer? Seeking freelancer? (October 2026)",
            },
        ]
    }
    session = MagicMock()
    session.get.return_value = MagicMock(json=MagicMock(return_value=hits))

    assert latest_freelancer_thread(session) == "50338744"


def test_github_keeps_only_issues_that_mention_a_payment():
    clients = github_clients(GITHUB)

    assert [c["link"] for c in clients] == [
        "https://github.com/acme/ragkit/issues/12",
        "https://github.com/ferris/fastparse/issues/3",
    ]
    assert clients[0]["fuente"] == "GitHub bounty"
    assert clients[0]["autor"] == "acme/ragkit"
    assert clients[0]["titulo"] == "Add hybrid search to the retriever"
    assert clients[0]["pago"] == "$250"
    assert clients[1]["pago"] == "100 USD"


def test_github_malformed_payload_gives_no_clients():
    assert github_clients({"message": "API rate limit exceeded"}) == []


def test_github_queries_cover_every_label_and_language():
    queries = github_queries()

    assert len(queries) == 6
    assert 'is:issue is:open label:"help wanted" language:rust' in queries
    assert "is:issue is:open label:bounty language:python" in queries


def test_pitch_prompt_names_the_whole_stack_and_the_github():
    for word in (
        "RAG",
        "FastAPI",
        "Rust",
        "Python",
        "github.com/IanBertoni",
        "I saw your post/issue looking for",
    ):
        assert word in PITCH_PROMPT


def test_render_shows_the_payment_when_known():
    client = {**github_clients(GITHUB)[0], "pitch": PITCH}

    assert "- Pago: $250" in render([client])


def issue(title: str, body: str = "", repo: str = "o/r", n: int = 1) -> dict:
    return {
        "html_url": f"https://github.com/{repo}/issues/{n}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "title": title,
        "body": body,
    }


@pytest.mark.parametrize(
    "title",
    ["[$2 site credit] Add an example", "Bounty: fix the parser", "Costs 10 USDC"],
)
def test_github_drops_tiny_or_amountless_payments(title):
    assert github_clients({"items": [issue(title)]}) == []


def test_github_reads_thousands_shorthand():
    assert (
        github_clients({"items": [issue("Port to Rust ($1.5k)")]})[0]["pago"] == "$1.5k"
    )


def test_github_caps_issues_per_repo():
    items = [issue(f"Task {n} $100", n=n) for n in range(5)]

    assert len(github_clients({"items": items})) == 2
