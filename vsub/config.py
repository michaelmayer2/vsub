"""Configuration loading.

Config lives in ``~/.config/vsub/config.toml`` (override with ``$VSUB_CONFIG``)::

    [viya]
    url     = "https://viya.example.com"
    context = "SAS Studio compute context"
    verify  = true          # false to skip TLS verification (self-signed certs)
    cafile  = ""            # path to the Viya CA bundle, if needed

    [storage]
    remote_root = "~/vsub_data"   # "~" is the user's HOME inside the compute pod

    [batch]
    cli          = "sas-viya"     # path to the sas-viya CLI
    profile      = ""             # sas-viya --profile, if not the default
    context      = "default"      # batch context
    home_mounted = false          # true if HOME is persistent AND allowed in batch lockdown
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "vsub"


def config_path() -> Path:
    return Path(os.environ.get("VSUB_CONFIG") or config_dir() / "config.toml")


class ConfigError(Exception):
    pass


@dataclass
class ViyaConfig:
    url: str
    context: str | None = None
    verify: bool = True
    cafile: str | None = None


@dataclass
class StorageConfig:
    remote_root: str = "~/vsub_data"


@dataclass
class BatchConfig:
    cli: str = "sas-viya"
    profile: str | None = None
    context: str = "default"
    home_mounted: bool = False


@dataclass
class Config:
    viya: ViyaConfig
    storage: StorageConfig = field(default_factory=StorageConfig)
    batch: BatchConfig = field(default_factory=BatchConfig)


def _none_if_empty(v):
    return v if v not in ("", None) else None


def parse_config(data: dict) -> Config:
    viya = data.get("viya") or {}
    url = viya.get("url") or os.environ.get("VSUB_URL")
    if not url:
        raise ConfigError("[viya] url is required (or set VSUB_URL)")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"[viya] url must start with http:// or https:// (got {url!r})")

    storage = data.get("storage") or {}
    batch = data.get("batch") or {}
    remote_root = storage.get("remote_root", "~/vsub_data").rstrip("/") or "/"
    if not (remote_root.startswith("/") or remote_root == "~" or remote_root.startswith("~/")):
        raise ConfigError("[storage] remote_root must be absolute or start with '~/'")
    if any(c in remote_root for c in "\n\r'\""):
        raise ConfigError("[storage] remote_root must not contain quotes or newlines")

    return Config(
        viya=ViyaConfig(
            url=url.rstrip("/"),
            context=_none_if_empty(viya.get("context") or os.environ.get("VSUB_CONTEXT")),
            verify=bool(viya.get("verify", True)),
            cafile=_none_if_empty(viya.get("cafile")),
        ),
        storage=StorageConfig(remote_root=remote_root),
        batch=BatchConfig(
            cli=batch.get("cli", "sas-viya"),
            profile=_none_if_empty(batch.get("profile")),
            context=batch.get("context", "default"),
            home_mounted=bool(batch.get("home_mounted", False)),
        ),
    )


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        if os.environ.get("VSUB_URL"):
            return parse_config({})
        raise ConfigError(f"No config found at {path}. Run `vsub init` or set VSUB_URL.")
    with open(path, "rb") as fh:
        return parse_config(tomllib.load(fh))


TEMPLATE = '''[viya]
url     = "{url}"
context = "{context}"
verify  = true
cafile  = ""

[storage]
# Persistent dataset location on the Viya side. "~" = HOME in the compute pod.
# Only persistent if your admin mounted home directories into the compute pods
# (check with `vsub check-home`).
remote_root = "~/vsub_data"

[batch]
cli          = "sas-viya"
profile      = ""
context      = "default"
# Set to true only if HOME is mounted in batch pods AND allowed by the batch
# server's LOCKDOWN path list.
home_mounted = false
'''
