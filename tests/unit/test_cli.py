"""Unit tests for the Typer CLI."""

import re
from pathlib import Path

from typer.testing import CliRunner

from repricing_engine.cli import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI styling so assertions don't depend on Rich's color output.

    Rich emits color codes when it detects a CI/forced-color environment, which
    can split a token like ``--catalog`` across escape sequences.
    """
    return _ANSI_RE.sub("", text)


class TestCli:
    def test_help(self):
        result = runner.invoke(app, ["match", "--help"])
        assert result.exit_code == 0
        output = _plain(result.output)
        assert "--catalog" in output
        assert "--skip-ai" in output

    def test_match_produces_output(
        self, sample_catalog_path: Path, sample_oxylabs_path: Path, tmp_path: Path
    ):
        out = tmp_path / "results.csv"
        result = runner.invoke(
            app,
            [
                "match",
                "--catalog",
                str(sample_catalog_path),
                "--competitors",
                str(sample_oxylabs_path),
                "--output",
                str(out),
                "--skip-ai",
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "Match Summary" in _plain(result.output)

    def test_invalid_market_exits(
        self, sample_catalog_path: Path, sample_oxylabs_path: Path, tmp_path: Path
    ):
        result = runner.invoke(
            app,
            [
                "match",
                "--catalog",
                str(sample_catalog_path),
                "--competitors",
                str(sample_oxylabs_path),
                "--output",
                str(tmp_path / "r.csv"),
                "--market",
                "ZZ",
                "--skip-ai",
            ],
        )
        assert result.exit_code == 2
