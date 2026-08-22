"""Tests for strict config loading. The property under test is that bad config crashes.
Deliberately not: testing that the shipped configs point at a reachable gateway - that
needs the network, and `make test` must pass with none.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.config import (
    CONFIGS_DIR,
    GOLD_DIR,
    REPO_ROOT,
    ConfigError,
    load_arm_config,
    load_hosts,
    require_int,
    require_str,
    resolve_api_key,
)

HOSTS_YAML = textwrap.dedent(
    """
    spark:
      host: spark.lan
      user: aroni
      repo: /srv/parser-lab
      gateway: http://spark.lan:4000/v1
    laptop:
      gateway: http://localhost:11434/v1
    """
)

ARM_YAML = textwrap.dedent(
    """
    arm: arm0_zeroshot
    seed: 1337
    input_path: data/gold/sample.jsonl
    output_path: data/generated/arm0.jsonl
    report_path: reports/arm0.json
    schema_path: configs/schema.json
    llm:
      host: spark
      model: Qwen2.5-3B-Instruct
      api_key_env: PARSER_LAB_GATEWAY_KEY
      max_concurrency: 32
      timeout_s: 120.0
      max_retries: 5
      backoff_base_s: 1.0
      temperature: 0.0
      max_tokens: 512
    params:
      prompt_path: configs/prompts/arm0_zeroshot.txt
    """
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_the_repos_own_hosts_file() -> None:
    hosts = load_hosts()
    assert hosts.spark.ssh_target == f"{hosts.spark.user}@{hosts.spark.host}"
    assert hosts.gateway_for("laptop") == hosts.laptop.gateway


def test_gateway_for_rejects_an_unknown_host() -> None:
    hosts = load_hosts()
    with pytest.raises(ConfigError, match="unknown host"):
        hosts.gateway_for("dgx")


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_hosts(tmp_path / "nope.yaml")


def test_empty_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="empty"):
        load_hosts(_write(tmp_path, "hosts.yaml", "\n"))


def test_missing_key_is_an_error_not_a_default(tmp_path: Path) -> None:
    trimmed = HOSTS_YAML.replace("  repo: /srv/parser-lab\n", "")
    with pytest.raises(ConfigError, match="missing required key 'repo'"):
        load_hosts(_write(tmp_path, "hosts.yaml", trimmed))


def test_unknown_key_is_an_error(tmp_path: Path) -> None:
    """A typo'd key that loads is a config that lies about what it configured."""
    typo = HOSTS_YAML.replace("  gateway: http://spark.lan", "  gatway: http://spark.lan")
    with pytest.raises(ConfigError, match="unknown key"):
        load_hosts(_write(tmp_path, "hosts.yaml", typo))


def test_null_value_is_an_error(tmp_path: Path) -> None:
    nulled = HOSTS_YAML.replace("  host: spark.lan", "  host:")
    with pytest.raises(ConfigError, match="missing required key 'host'|is null"):
        load_hosts(_write(tmp_path, "hosts.yaml", nulled))


def test_empty_string_is_an_error(tmp_path: Path) -> None:
    blank = HOSTS_YAML.replace("  host: spark.lan", '  host: "   "')
    with pytest.raises(ConfigError, match="is empty"):
        load_hosts(_write(tmp_path, "hosts.yaml", blank))


def test_arm_config_resolves_the_gateway_from_hosts(tmp_path: Path) -> None:
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    cfg = load_arm_config(_write(tmp_path, "arm0.yaml", ARM_YAML), hosts)
    assert cfg.llm is not None
    assert cfg.llm.base_url == hosts.spark.gateway
    assert cfg.llm.model == "Qwen2.5-3B-Instruct"


def test_arm_paths_are_absolute_and_repo_rooted(tmp_path: Path) -> None:
    """Resolved against the repo, not cwd - a make target and a shell must agree."""
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    cfg = load_arm_config(_write(tmp_path, "arm0.yaml", ARM_YAML), hosts)
    assert cfg.input_path == REPO_ROOT / "data/gold/sample.jsonl"
    assert cfg.report_path.is_absolute()


def test_llm_must_be_present_even_when_unused(tmp_path: Path) -> None:
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    without = "\n".join(
        line for line in ARM_YAML.splitlines() if not line.startswith(("llm:", "  "))
    )
    with pytest.raises(ConfigError, match="missing required key 'llm'"):
        load_arm_config(_write(tmp_path, "arm.yaml", without + "\n"), hosts)


def test_explicit_null_llm_means_no_model(tmp_path: Path) -> None:
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    head, _, _ = ARM_YAML.partition("llm:")
    text = head + "llm: null\nparams: {}\n"
    cfg = load_arm_config(_write(tmp_path, "arm1.yaml", text), hosts)
    assert cfg.llm is None


def test_unknown_llm_key_is_an_error(tmp_path: Path) -> None:
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    typo = ARM_YAML.replace("  max_tokens: 512", "  max_token: 512")
    with pytest.raises(ConfigError, match="unknown key"):
        load_arm_config(_write(tmp_path, "arm0.yaml", typo), hosts)


def test_llm_host_must_be_a_known_host(tmp_path: Path) -> None:
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    bad = ARM_YAML.replace("  host: spark", "  host: gpu-box")
    with pytest.raises(ConfigError, match="unknown host"):
        load_arm_config(_write(tmp_path, "arm0.yaml", bad), hosts)


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_concurrency_below_one_is_an_error(tmp_path: Path, bad: str) -> None:
    """Batching is the whole hardware strategy; concurrency 0 would deadlock silently."""
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    text = ARM_YAML.replace("  max_concurrency: 32", f"  max_concurrency: {bad}")
    with pytest.raises(ConfigError, match="max_concurrency"):
        load_arm_config(_write(tmp_path, "arm0.yaml", text), hosts)


def test_boolean_is_not_an_integer() -> None:
    with pytest.raises(ConfigError, match="expected an integer"):
        require_int({"max_concurrency": True}, "max_concurrency", "test")


def test_number_is_not_a_string() -> None:
    with pytest.raises(ConfigError, match="expected a string"):
        require_str({"model": 3}, "model", "test")


def test_api_key_is_read_at_call_time(tmp_path: Path, monkeypatch) -> None:
    hosts = load_hosts(_write(tmp_path, "hosts.yaml", HOSTS_YAML))
    cfg = load_arm_config(_write(tmp_path, "arm0.yaml", ARM_YAML), hosts)
    assert cfg.llm is not None
    monkeypatch.delenv(cfg.llm.api_key_env, raising=False)
    with pytest.raises(ConfigError, match=cfg.llm.api_key_env):
        resolve_api_key(cfg.llm)
    monkeypatch.setenv(cfg.llm.api_key_env, "sk-test")
    assert resolve_api_key(cfg.llm) == "sk-test"


@pytest.mark.parametrize("name", ["arm0.yaml", "arm1.yaml"])
def test_shipped_arm_configs_load(name: str) -> None:
    cfg = load_arm_config(CONFIGS_DIR / name, load_hosts())
    assert cfg.seed == 1337  # same seed across arms, or the comparison drifts
    assert cfg.schema_path.is_file()


def test_no_arm_writes_into_gold() -> None:
    """Rule 1, asserted rather than commented: nothing may target data/gold/."""
    for path in sorted(CONFIGS_DIR.glob("arm*.yaml")):
        cfg = load_arm_config(path, load_hosts())
        assert GOLD_DIR not in cfg.output_path.parents
        assert GOLD_DIR not in cfg.report_path.parents
