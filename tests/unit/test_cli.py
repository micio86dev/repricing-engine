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

    def test_help_exposes_default_on_opt_outs(self):
        # The full online pipeline is the default; the opt-out flags must be shown.
        result = runner.invoke(app, ["match", "--help"])
        output = _plain(result.output)
        assert "--no-fetch" in output
        assert "--no-strict-match" in output
        assert "--no-require-shipping" in output

    def test_match_produces_output(
        self, sample_catalog_path: Path, sample_oxylabs_path: Path, tmp_path: Path
    ):
        # --no-fetch keeps this an offline CSV-only run (tests never hit the network).
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
                "--no-fetch",
                "--no-require-shipping",
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
