from unittest.mock import MagicMock

import pytest

from matcher.seniority_judge import JUDGE_PROMPT, judge_fit, parse_verdict

APPLY = (
    '{"verdict": "apply", "min_years": 1, "degree_required": false, '
    '"from_argentina": true, "reason": "1+ years Python, LatAm remote"}'
)


def groq_session(content: str) -> MagicMock:
    session = MagicMock()
    session.post.return_value = MagicMock(
        ok=True,
        json=MagicMock(return_value={"choices": [{"message": {"content": content}}]}),
    )
    return session


def test_parse_verdict_reads_the_json_object():
    verdict = parse_verdict(APPLY)

    assert verdict["verdict"] == "apply"
    assert verdict["min_years"] == 1
    assert verdict["degree_required"] is False
    assert verdict["reason"] == "1+ years Python, LatAm remote"


def test_parse_verdict_ignores_text_around_the_json():
    assert parse_verdict(f"Here it is:\n```json\n{APPLY}\n```")["verdict"] == "apply"


@pytest.mark.parametrize(
    "reply", ["no json here", '{"verdict": "maybe"}', '{"verdict": "apply"']
)
def test_parse_verdict_rejects_missing_or_unknown_verdicts(reply):
    with pytest.raises(ValueError):
        parse_verdict(reply)


def test_judge_fit_sends_the_posting_and_returns_the_verdict():
    session = groq_session(APPLY)

    verdict = judge_fit("Data Engineer", "LatAm", "1+ years with Python", session)

    assert verdict["verdict"] == "apply"
    sent = session.post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "Data Engineer" in sent
    assert "1+ years with Python" in sent


def test_judge_prompt_judges_requirements_not_the_title():
    assert "not the title" in JUDGE_PROMPT
    assert "no university degree" in JUDGE_PROMPT
    assert "equivalent experience" in JUDGE_PROMPT
    assert "Argentina" in JUDGE_PROMPT
