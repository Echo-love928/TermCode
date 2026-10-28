"""校验正常配置、错误定位和密钥不泄露。"""

from pathlib import Path

import pytest
import yaml

from termcode.config import ConfigError, _from_dict, load


def entry(**changes):
    return {
        "name": "test",
        "protocol": "openai",
        "model": "test-model",
        "api_key": "TEST_SECRET",
        **changes,
    }


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
