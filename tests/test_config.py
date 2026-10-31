"""校验正常配置、错误定位和密钥不泄露。"""

from pathlib import Path

import pytest
import yaml

from termcode.config import ConfigError, _from_dict, effective_context_window, load


def entry(**changes):
    return {
        "name": "test",
        "protocol": "openai",
        "model": "test-model",
        "api_key": "TEST_SECRET",
        **changes,
    }


@pytest.mark.parametrize(
    "protocol,value,expected",
    [
        ("anthropic", 0, 200000),
        ("openai", 0, 128000),
        ("anthropic", 80000, 80000),
        ("openai", 100000, 100000),
    ],
)
def test_effective_context_window(protocol, value, expected):
    cfg = _from_dict({"providers": [entry(protocol=protocol, context_window=value)]})
    assert cfg.providers[0].context_window == value
    assert effective_context_window(cfg.providers[0]) == expected


def test_effective_context_window_unknown_protocol():
    from dataclasses import replace

    cfg = _from_dict({"providers": [entry()]}).providers[0]
    assert effective_context_window(replace(cfg, protocol="unknown")) == 200000


@pytest.mark.parametrize("value", [True, "80000", 1.5, -1, None])
def test_invalid_context_window(value):
    with pytest.raises(ConfigError, match="context_window"):
        _from_dict({"providers": [entry(context_window=value)]})


def test_example_config_context_window():
    path = Path(__file__).resolve().parents[1] / ".termcode/config.yaml.example"
    cfg = _from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert all(p.context_window > 0 for p in cfg.providers)


def test_load_valid_and_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "providers": [
                    entry(),
                    entry(name="claude", protocol="anthropic", thinking=True),
                ]
            }
        ),
        encoding="utf-8",
    )
    config = load(path)
    assert len(config.providers) == 2
    assert config.providers[0].thinking is False
    assert config.providers[0].base_url is None
    assert config.providers[1].thinking is True
    assert "TEST_SECRET" not in repr(config)


@pytest.mark.parametrize(
    "data",
    [None, [], "x", {}, {"providers": []}, {"providers": "x"}, {"providers": [None]}],
)
def test_invalid_structure(data):
    with pytest.raises(ConfigError):
        _from_dict(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("name", ""),
        ("protocol", "bad"),
        ("model", 12),
        ("api_key", " "),
        ("thinking", "true"),
        ("thinking", 1),
        ("base_url", "ftp://example.com"),
        ("base_url", "https://example.com:bad"),
        ("base_url", ""),
        ("base_url", 123),
        ("unknown", "TEST_SECRET"),
    ],
)
def test_invalid_field_is_located_without_secret(key, value):
    with pytest.raises(ConfigError, match=r"providers\[0\]") as caught:
        _from_dict({"providers": [entry(**{key: value})]})
    assert "TEST_SECRET" not in str(caught.value)


@pytest.mark.parametrize("field", ["name", "protocol", "model", "api_key"])
def test_missing_required(field):
    item = entry()
    del item[field]
    with pytest.raises(ConfigError, match=field):
        _from_dict({"providers": [item]})


def test_duplicate_names():
    with pytest.raises(ConfigError, match="重名"):
        _from_dict({"providers": [entry(), entry()]})


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load(tmp_path / "missing.yaml")


def test_yaml_error_does_not_echo_secret(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("providers: [TEST_SECRET\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load(path)
    assert "TEST_SECRET" not in str(caught.value)
    assert "YAML" in str(caught.value)


def test_example_is_valid():
    path = Path(__file__).resolve().parents[1] / ".termcode/config.yaml.example"
    assert load(path).providers[0].protocol == "anthropic"
