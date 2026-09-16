from typer.testing import CliRunner

from chi import __version__
from chi.cli import app

runner = CliRunner()


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    # assert against the package's own version, not a literal: a hardcoded
    # "0.1.0" silently rots into a red suite on the next release bump.
    assert __version__ in result.output
