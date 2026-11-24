import logging

from applied import drop_applied, load_applied

LIST = """# empresa | estado
Examplecorp | rechazado
Samplesoft | no elegible (EEA)

  posthog|aplicado
línea sin separador
"""


def offer(empresa: str) -> dict:
    return {"empresa": empresa, "link": f"https://x/{empresa}"}


def test_load_applied_reads_name_and_status_and_skips_comments(tmp_path, caplog):
    path = tmp_path / "applied.txt"
    path.write_text(LIST, encoding="utf-8")

    assert load_applied(path) == {
        "examplecorp": "rechazado",
        "samplesoft": "no elegible (EEA)",
        "posthog": "aplicado",
    }
    assert "sin separador" in caplog.text


def test_load_applied_is_empty_without_the_file(tmp_path):
    assert load_applied(tmp_path / "nope.txt") == {}


def test_drop_applied_matches_the_company_as_a_whole_word_ignoring_case(caplog):
    caplog.set_level(logging.INFO)
    applied = {"examplecorp": "rechazado", "samplesoft": "no elegible (EEA)"}
    offers = [
        offer("Examplecorp"),
        offer("Samplesoft"),
        offer("Samplesoft Labs Inc."),
        offer("Examplecorpcamp"),
        offer("Acme"),
    ]

    kept = drop_applied(offers, applied)

    assert [o["empresa"] for o in kept] == ["Examplecorpcamp", "Acme"]
    assert "rechazado" in caplog.text


def test_drop_applied_does_not_mutate_its_input():
    offers = [offer("Examplecorp")]

    drop_applied(offers, {"examplecorp": "rechazado"})

    assert offers == [offer("Examplecorp")]
