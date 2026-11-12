import logging

import pytest

import config
from config import setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    yield
    logging.basicConfig(force=True)  # close the file handler so tmp_path can go


@pytest.mark.parametrize(("verbose", "info_logged"), [(False, False), (True, True)])
def test_setup_logging_writes_dated_file_at_requested_level(
    tmp_path, verbose, info_logged
):
    setup_logging(verbose=verbose, log_dir=tmp_path)

    logging.getLogger("scrapers.demo").info("detalle")
    logging.getLogger("scrapers.demo").warning("cuidado")

    (log_file,) = tmp_path.glob("scraper_????-??-??.log")
    text = log_file.read_text(encoding="utf-8")
    assert " - WARNING - scrapers.demo - cuidado" in text
    assert ("detalle" in text) == info_logged


def test_env_casts_values_and_names_the_bad_variable(monkeypatch):
    monkeypatch.setenv("MIN_SCORE", "5")
    assert config._env("MIN_SCORE", "4", int) == 5

    monkeypatch.setenv("MIN_SCORE", "alto")
    with pytest.raises(ValueError, match="MIN_SCORE='alto'"):
        config._env("MIN_SCORE", "4", int)


def test_keywords_splits_and_trims():
    assert config._keywords(" data, machine learning ,,ia ") == [
        "data",
        "machine learning",
        "ia",
    ]
