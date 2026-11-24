import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import freelance_client_finder as finder
from freelance_client_finder import (
    GITHUB_API,
    GITHUB_SEARCH,
    fetch_github,
    getonboard_clients,
    github_clients,
    github_queries,
    hn_clients,
    latest_freelancer_thread,
    radar_clients,
    remotive_clients,
    render,
    repo_ok,
)

FIXTURES = Path(__file__).parent / "fixtures"
GITHUB = json.loads((FIXTURES / "github_issues_search.json").read_text("utf-8"))
# repo -> (stars, archived, open bounty issues)
REPOS = {
    "acme/ragkit": (120, False, 3),
    "ferris/fastparse": (10, False, 1),  # too few stars
    "tscircuit/core": (900, False, 30),
    "farm/money": (500, False, 250),  # bounty farm
    "old/lib": (300, True, 2),  # archived
}


def github_session() -> MagicMock:
    """Fake GitHub API: issue search, per-repo bounty count and repo info."""

    def get(url, params=None, **_):
        if url == GITHUB_SEARCH and params["q"].startswith("repo:"):
            repo = params["q"].split()[0].removeprefix("repo:")
            payload = {"total_count": REPOS[repo][2], "items": []}
        elif url == GITHUB_SEARCH:
            payload = GITHUB
        else:
            stars, archived, _count = REPOS[url.removeprefix(f"{GITHUB_API}/repos/")]
            payload = {"stargazers_count": stars, "archived": archived}
        return MagicMock(json=MagicMock(return_value=payload))

    session = MagicMock()
    session.get.side_effect = get
    return session


def test_github_keeps_paid_issues_and_drops_honeypots_and_scam_patterns():
    links = [c["link"] for c in github_clients(GITHUB)]

    assert links == [
        "https://github.com/acme/ragkit/issues/12",
        "https://github.com/ferris/fastparse/issues/3",
        "https://github.com/tscircuit/core/issues/501",
        "https://github.com/farm/money/issues/3",
        "https://github.com/old/lib/issues/5",
    ]


def test_github_reads_the_amount_from_text_or_algora_label():
    clients = {c["autor"]: c for c in github_clients(GITHUB)}

    assert clients["acme/ragkit"]["pago"] == "$250"
    assert clients["acme/ragkit"]["fuente"] == "GitHub bounty"
    assert clients["ferris/fastparse"]["pago"] == "100 USD"
    assert clients["tscircuit/core"]["pago"] == "$150"
    assert clients["tscircuit/core"]["fuente"] == "Algora (GitHub)"


def test_algora_bounties_need_more_than_100_usd():
    assert "small/org" not in [c["autor"] for c in github_clients(GITHUB)]


def test_github_malformed_payload_gives_no_clients():
    assert github_clients({"message": "API rate limit exceeded"}) == []


def issue(title: str, body: str = "", repo: str = "o/r", n: int = 1) -> dict:
    return {
        "html_url": f"https://github.com/{repo}/issues/{n}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "title": title,
        "body": body,
    }


@pytest.mark.parametrize(
    "title",
    [
        "[$2 site credit] Add an example",
        "Bounty: fix the parser",
        "Costs 500 USDC",
        "Honeypot task $500",
    ],
)
def test_github_drops_tiny_crypto_amountless_or_blacklisted(title):
    assert github_clients({"items": [issue(title)]}) == []


@pytest.mark.parametrize(
    "repo", ["x/bounty-plaza", "x/honeypot-repo", "x/trap-benchmark", "x/bot-trap"]
)
def test_github_drops_blacklisted_repos(repo):
    assert github_clients({"items": [issue("Fix it $500", repo=repo)]}) == []


def test_github_reads_thousands_shorthand():
    clients = github_clients({"items": [issue("Port to Rust ($1.5k)")]})

    assert clients[0]["pago"] == "$1.5k"


def test_github_caps_issues_per_repo():
    items = [issue(f"Task {n} $100", n=n) for n in range(5)]

    assert len(github_clients({"items": items})) == 2


@pytest.mark.parametrize(
    ("stars", "archived", "bounties", "ok"),
    [
        (50, False, 100, True),
        (49, False, 1, False),
        (500, True, 1, False),
        (500, False, 101, False),
    ],
)
def test_repo_ok(stars, archived, bounties, ok):
    info = {"stargazers_count": stars, "archived": archived}

    assert repo_ok(info, bounties) is ok


def test_fetch_github_keeps_only_real_bounties(monkeypatch):
    monkeypatch.setattr(finder.time, "sleep", lambda _s: None)

    clients = fetch_github(github_session())

    assert [c["autor"] for c in clients] == ["acme/ragkit", "tscircuit/core"]


def test_github_queries_cover_every_label_and_language():
    queries = github_queries()

    assert len(queries) == 8
    assert (
        'is:issue is:open archived:false label:"help wanted" language:rust' in queries
    )
    assert (
        'is:issue is:open archived:false label:"\U0001f48e Bounty" language:python'
        in queries
    )


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
            "SEEKING FREELANCER | Remote<p>Senior Python dev, 5+ years.",
        )
    )

    assert [c["autor"] for c in clients] == ["a0"]
    assert clients[0]["fuente"] == "HN Seeking freelancer"
    assert clients[0]["link"] == "https://news.ycombinator.com/item?id=0"


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


def test_render_lists_each_client_with_link_payment_and_raw_description():
    text = render(github_clients(GITHUB)[:1])

    assert "## Add hybrid search to the retriever" in text
    assert "https://github.com/acme/ragkit/issues/12" in text
    assert "- Empresa/Fuente: acme/ragkit (GitHub bounty)" in text
    assert "- Pago: $250" in text
    assert (
        "```text\nAdd hybrid search to the retriever\n"
        "We will pay $250 for this, via GitHub Sponsors."
    ) in text


def test_render_clips_long_descriptions():
    client = {**github_clients(GITHUB)[0], "texto": "x" * 5000}

    assert "x" * 3001 not in render([client])


def test_finder_never_calls_groq():
    assert not hasattr(finder, "groq_chat")
    assert not hasattr(finder, "write_pitches")


def test_hn_drops_lead_roles():
    assert hn_clients(thread("SEEKING FREELANCER | Remote<p>Lead Python dev.")) == []


def test_remotive_keeps_wanted_contract_and_freelance_jobs_only():
    xml = (FIXTURES / "remotive_software_feed.xml").read_bytes()

    clients = remotive_clients(xml)

    assert [c["autor"] for c in clients] == ["Acme Ops"]
    assert clients[0]["fuente"] == "Remotive"
    assert clients[0]["titulo"] == "Python Automation Developer"
    assert clients[0]["link"].startswith("https://remotive.com/remote-jobs/")
    assert "scrapers" in clients[0]["texto"]


def test_remotive_broken_feed_gives_no_clients():
    assert remotive_clients(b"<rss><channel>") == []


def gob_job(title: str, modality: int = 3, remote: bool = True, n: int = 1) -> dict:
    return {
        "id": f"job-{n}",
        "links": {"public_url": f"https://www.getonbrd.com/jobs/job-{n}"},
        "attributes": {
            "title": title,
            "description": "<p>Python and LLM automation.</p>",
            "remote": remote,
            "modality": {"data": {"id": modality, "type": "modality"}},
            "company": {"data": {"attributes": {"name": f"Co{n}"}}},
        },
    }


def test_getonboard_keeps_remote_freelance_jobs_only():
    jobs = [
        gob_job("AI Automation Engineer - Contract", n=1),
        gob_job("Python Developer", modality=1, n=2),  # full time
        gob_job("Python Developer", remote=False, n=3),
        gob_job("Lead Python Developer", n=4),
        gob_job("AI Automation Engineer - Contract", n=1),  # seen in two queries
    ]

    clients = getonboard_clients(jobs)

    assert [c["link"] for c in clients] == ["https://www.getonbrd.com/jobs/job-1"]
    assert clients[0]["autor"] == "Co1"
    assert clients[0]["fuente"] == "Get on Board"


def test_radar_clients_maps_leads_and_drops_senior_titles():
    leads = [
        {
            "fuente": "Remote OK",
            "titulo": "Python Contractor",
            "empresa": "A",
            "tarifa": "$50/hr",
            "link": "https://remoteok.com/1",
        },
        {
            "fuente": "WWR",
            "titulo": "Senior Data Engineer",
            "empresa": "B",
            "tarifa": "?",
            "link": "https://weworkremotely.com/2",
        },
    ]

    clients = radar_clients(leads)

    assert [c["link"] for c in clients] == ["https://remoteok.com/1"]
    assert clients[0]["pago"] == "$50/hr"
    assert clients[0]["autor"] == "A"
