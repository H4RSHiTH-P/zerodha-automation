import pytest

from zt import config


def test_load_from_environ(env, tmp_path):
    cfg = config.load(env_file=tmp_path / "missing.env", environ=env)
    assert cfg.api_key == "key" and cfg.charges.max_brokerage == 20 and cfg.performance.min_positions == 40
    assert cfg.db_path.name == "zt.db" and cfg.secrets == ("secret",)


def test_missing_and_malformed_keys(env, tmp_path):
    del env["ZERODHA_STT"]
    with pytest.raises(config.ConfigError, match="ZERODHA_STT"):
        config.load(env_file=tmp_path / "missing.env", environ=env)
    env["ZERODHA_STT"] = "abc"
    with pytest.raises(config.ConfigError, match="ZERODHA_STT"):
        config.load(env_file=tmp_path / "missing.env", environ=env)
