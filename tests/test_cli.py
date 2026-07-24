from typer.testing import CliRunner

from chi.cli import app

runner = CliRunner()


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
