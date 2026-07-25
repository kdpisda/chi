import pytest


@pytest.fixture(autouse=True)
def _chi_isolated_dirs(tmp_path, monkeypatch):
    """Every test gets private chi config/data homes — never the real ones."""
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "_chi_config"))
    monkeypatch.setenv("CHI_DATA_DIR", str(tmp_path / "_chi_data"))
