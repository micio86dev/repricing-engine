"""Unit tests for the Typer CLI."""

from pathlib import Path

from typer.testing import CliRunner

from repricing_engine.cli import app

runner = CliRunner()


class TestCli:
    def test_help(self):
        result = runner.invoke(app, ["match", "--help"])
        assert result.exit_code == 0
        assert "--catalog" in result.output
        assert "--skip-ai" in result.output

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
        assert "Match Summary" in result.output

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
