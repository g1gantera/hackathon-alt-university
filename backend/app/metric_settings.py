"""Validated runtime settings, persisted atomically without recompilation."""

import os
from pathlib import Path

from .domain import ROOT
from .metrics.quality import MetricConfig


def path():
    return Path(os.environ.get("METRIC_CONFIG_PATH", str(ROOT / "data/metrics.runtime.json")))


def load_override():
    return MetricConfig.load(path()) if path().exists() else None


def save(config):
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
