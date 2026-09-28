import pytest
from pydantic import ValidationError

from jobhunter.config import Settings


def test_host_must_stay_on_localhost(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings(host="0.0.0.0", data_dir=tmp_path)


def test_localhost_binds_to_loopback(tmp_path) -> None:
    settings = Settings(host="localhost", data_dir=tmp_path)
    assert settings.bind_host() == "127.0.0.1"


def test_toml_config_is_loaded(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text('port = 8799\nlog_level = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("JOBHUNTER_CONFIG", str(config))
    monkeypatch.delenv("JOBHUNTER_PORT", raising=False)
    monkeypatch.delenv("JOBHUNTER_LOG_LEVEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.port == 8799
    assert settings.log_level == "DEBUG"


def test_env_overrides_toml(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text("port = 8799\n", encoding="utf-8")
    monkeypatch.setenv("JOBHUNTER_CONFIG", str(config))
    monkeypatch.setenv("JOBHUNTER_PORT", "8801")
    settings = Settings(_env_file=None)
    assert settings.port == 8801
