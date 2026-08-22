"""Loads YAML config into frozen typed objects, failing on anything unexpected.
Deliberately not: defaulting, merging, env-var interpolation, or CLI parsing.

A config that is wrong but runs costs a whole labelling pass and produces a number
that looks real. So: a missing key is an error, an unknown key is an error (it is
almost always a typo silently doing nothing), and a wrong type is an error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
CONFIGS_DIR: Final[Path] = REPO_ROOT / "configs"
DATA_DIR: Final[Path] = REPO_ROOT / "data"
GOLD_DIR: Final[Path] = DATA_DIR / "gold"
UNSEEN_DIR: Final[Path] = DATA_DIR / "unseen"
GENERATED_DIR: Final[Path] = DATA_DIR / "generated"
RAW_DIR: Final[Path] = DATA_DIR / "raw"
REPORTS_DIR: Final[Path] = REPO_ROOT / "reports"
RUNS_DIR: Final[Path] = REPO_ROOT / "runs"

HOSTS_CONFIG_PATH: Final[Path] = CONFIGS_DIR / "hosts.yaml"

KNOWN_HOSTS: Final[tuple[str, ...]] = ("spark", "laptop")


class ConfigError(ValueError):
    """Raised for any malformed config. Always names the file and the key."""


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{where}: missing required key '{key}'")
    value = mapping[key]
    if value is None:
        raise ConfigError(f"{where}: key '{key}' is null; give it a value or remove the key")
    return value


def require_str(mapping: dict[str, Any], key: str, where: str) -> str:
    value = _require(mapping, key, where)
    if not isinstance(value, str):
        raise ConfigError(f"{where}.{key}: expected a string, got {type(value).__name__}")
    if not value.strip():
        raise ConfigError(f"{where}.{key}: is empty")
    return value


def require_int(mapping: dict[str, Any], key: str, where: str) -> int:
    value = _require(mapping, key, where)
    # bool is an int subclass and `max_concurrency: true` should not silently mean 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}.{key}: expected an integer, got {value!r}")
    return value


def require_float(mapping: dict[str, Any], key: str, where: str) -> float:
    value = _require(mapping, key, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}.{key}: expected a number, got {value!r}")
    return float(value)


def require_path(mapping: dict[str, Any], key: str, where: str) -> Path:
    """Repo-relative in YAML, absolute in memory - so cwd never changes behaviour."""
    raw = require_str(mapping, key, where)
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def reject_unknown(mapping: dict[str, Any], known: set[str], where: str) -> None:
    extra = sorted(mapping.keys() - known)
    if extra:
        raise ConfigError(f"{where}: unknown key(s) {extra}; known keys are {sorted(known)}")


@dataclass(frozen=True)
class SparkHost:
    host: str
    user: str
    repo: str
    gateway: str

    @property
    def ssh_target(self) -> str:
        return f"{self.user}@{self.host}"


@dataclass(frozen=True)
class LaptopHost:
    gateway: str


@dataclass(frozen=True)
class HostsConfig:
    spark: SparkHost
    laptop: LaptopHost

    def gateway_for(self, name: str) -> str:
        if name == "spark":
            return self.spark.gateway
        if name == "laptop":
            return self.laptop.gateway
        raise ConfigError(f"unknown host '{name}'; known hosts are {list(KNOWN_HOSTS)}")


@dataclass(frozen=True)
class LLMConfig:
    """Everything src/llm.py needs. base_url is resolved from hosts.yaml, never literal."""

    host: str
    base_url: str
    model: str
    api_key_env: str
    max_concurrency: int
    timeout_s: float
    max_retries: int
    backoff_base_s: float
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class ArmConfig:
    """The keys every arm shares. Arm-specific keys go in `params` and are parsed
    by the arm itself with require_* - so they stay just as strict."""

    arm: str
    seed: int
    input_path: Path
    output_path: Path
    report_path: Path
    schema_path: Path
    llm: LLMConfig | None  # explicit null for arms that use no model (e.g. arm1)
    params: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        raise ConfigError(f"{path}: file is empty")
    return _as_mapping(loaded, str(path))


def load_hosts(path: Path = HOSTS_CONFIG_PATH) -> HostsConfig:
    raw = _load_yaml(path)
    where = str(path)
    reject_unknown(raw, set(KNOWN_HOSTS), where)

    spark_raw = _as_mapping(_require(raw, "spark", where), f"{where}.spark")
    reject_unknown(spark_raw, {"host", "user", "repo", "gateway"}, f"{where}.spark")
    spark = SparkHost(
        host=require_str(spark_raw, "host", f"{where}.spark"),
        user=require_str(spark_raw, "user", f"{where}.spark"),
        repo=require_str(spark_raw, "repo", f"{where}.spark"),
        gateway=require_str(spark_raw, "gateway", f"{where}.spark"),
    )

    laptop_raw = _as_mapping(_require(raw, "laptop", where), f"{where}.laptop")
    reject_unknown(laptop_raw, {"gateway"}, f"{where}.laptop")
    laptop = LaptopHost(gateway=require_str(laptop_raw, "gateway", f"{where}.laptop"))

    return HostsConfig(spark=spark, laptop=laptop)


_LLM_KEYS: Final[set[str]] = {
    "host",
    "model",
    "api_key_env",
    "max_concurrency",
    "timeout_s",
    "max_retries",
    "backoff_base_s",
    "temperature",
    "max_tokens",
}


def _load_llm(raw: dict[str, Any], hosts: HostsConfig, where: str) -> LLMConfig:
    reject_unknown(raw, _LLM_KEYS, where)
    host = require_str(raw, "host", where)
    max_concurrency = require_int(raw, "max_concurrency", where)
    if max_concurrency < 1:
        raise ConfigError(f"{where}.max_concurrency: must be >= 1, got {max_concurrency}")
    max_retries = require_int(raw, "max_retries", where)
    if max_retries < 0:
        raise ConfigError(f"{where}.max_retries: must be >= 0, got {max_retries}")
    return LLMConfig(
        host=host,
        base_url=hosts.gateway_for(host),
        model=require_str(raw, "model", where),
        api_key_env=require_str(raw, "api_key_env", where),
        max_concurrency=max_concurrency,
        timeout_s=require_float(raw, "timeout_s", where),
        max_retries=max_retries,
        backoff_base_s=require_float(raw, "backoff_base_s", where),
        temperature=require_float(raw, "temperature", where),
        max_tokens=require_int(raw, "max_tokens", where),
    )


_ARM_KEYS: Final[set[str]] = {
    "arm",
    "seed",
    "input_path",
    "output_path",
    "report_path",
    "schema_path",
    "llm",
    "params",
}


def load_arm_config(path: Path, hosts: HostsConfig | None = None) -> ArmConfig:
    """`llm: null` means 'this arm uses no model'; an absent `llm` key is an error."""
    raw = _load_yaml(path)
    where = str(path)
    reject_unknown(raw, _ARM_KEYS, where)
    for key in _ARM_KEYS:
        if key not in raw:
            raise ConfigError(f"{where}: missing required key '{key}'")

    llm_raw = raw["llm"]
    if llm_raw is None:
        llm = None
    else:
        if hosts is None:
            hosts = load_hosts()
        llm = _load_llm(_as_mapping(llm_raw, f"{where}.llm"), hosts, f"{where}.llm")

    params = _as_mapping(raw["params"] if raw["params"] is not None else {}, f"{where}.params")

    return ArmConfig(
        arm=require_str(raw, "arm", where),
        seed=require_int(raw, "seed", where),
        input_path=require_path(raw, "input_path", where),
        output_path=require_path(raw, "output_path", where),
        report_path=require_path(raw, "report_path", where),
        schema_path=require_path(raw, "schema_path", where),
        llm=llm,
        params=params,
    )


def resolve_api_key(llm: LLMConfig) -> str:
    """Read at call time, not load time, so tests and --dry-run need no secrets."""
    key = os.environ.get(llm.api_key_env)
    if not key:
        raise ConfigError(
            f"environment variable {llm.api_key_env} is unset; the gateway is keyed"
        )
    return key
