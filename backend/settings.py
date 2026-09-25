"""Runtime configuration: environment settings and the process-spec file."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_SPEC_PATH = BACKEND_DIR / "config" / "spec_limits.json"
DEFAULT_FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Limit:
    min: float | None = None
    max: float | None = None

    def violated_by(self, value: float) -> bool:
        return (self.min is not None and value < self.min) or (
            self.max is not None and value > self.max
        )

    def describe(self) -> str:
        if self.min is not None and self.max is not None:
            return f"{self.min:g} to {self.max:g}"
        if self.max is not None:
            return f"at most {self.max:g}"
        return f"at least {self.min:g}"


@dataclass(frozen=True)
class UnitSpec:
    column: str = "unit"
    quantity_column: str = "quantity"
    canonical: str = "kg"
    to_canonical: dict[str, float] = field(default_factory=lambda: {"kg": 1.0})

    def factor(self, unit: object) -> float | None:
        if not isinstance(unit, str):
            return None
        return self.to_canonical.get(unit.strip().lower())


@dataclass(frozen=True)
class SpecLimits:
    """Customer-supplied acceptance criteria, per product with a default fallback."""

    pass_statuses: frozenset[str]
    default: dict[str, Limit]
    products: dict[str, dict[str, Limit]]
    units: UnitSpec

    def limits_for(self, product_id: object) -> dict[str, Limit]:
        merged = dict(self.default)
        if isinstance(product_id, str):
            merged.update(self.products.get(product_id.strip(), {}))
        return merged

    @property
    def parameters(self) -> set[str]:
        params = set(self.default)
        for limits in self.products.values():
            params.update(limits)
        return params

    @classmethod
    def from_dict(cls, raw: dict) -> SpecLimits:
        def limits(block: dict) -> dict[str, Limit]:
            return {
                param: Limit(min=bounds.get("min"), max=bounds.get("max"))
                for param, bounds in block.items()
            }

        units_raw = raw.get("units", {})
        return cls(
            pass_statuses=frozenset(s.upper() for s in raw.get("pass_statuses", ["PASS"])),
            default=limits(raw.get("default", {})),
            products={pid: limits(block) for pid, block in raw.get("products", {}).items()},
            units=UnitSpec(
                column=units_raw.get("column", "unit"),
                quantity_column=units_raw.get("quantity_column", "quantity"),
                canonical=units_raw.get("canonical", "kg").lower(),
                to_canonical={
                    k.lower(): float(v) for k, v in units_raw.get("to_canonical", {"kg": 1}).items()
                },
            ),
        )

    @classmethod
    def load(cls, path: Path) -> SpecLimits:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    llm_enabled: bool
    llm_model: str
    llm_timeout_s: float
    llm_max_retries: int
    max_upload_bytes: int
    max_rows: int
    max_stored_runs: int
    allowed_origins: list[str]
    spec_path: Path
    frontend_dir: Path | None

    @classmethod
    def from_env(cls) -> Settings:
        api_key = os.getenv("ANTHROPIC_API_KEY") or None
        llm_flag = os.getenv("AUDITGUARD_LLM_ENABLED", "auto").lower()
        llm_enabled = bool(api_key) if llm_flag == "auto" else llm_flag in {"1", "true", "yes"}
        frontend = os.getenv("AUDITGUARD_FRONTEND_DIR")
        frontend_dir = Path(frontend) if frontend else DEFAULT_FRONTEND_DIR
        return cls(
            anthropic_api_key=api_key,
            llm_enabled=llm_enabled,
            llm_model=os.getenv("AUDITGUARD_LLM_MODEL", "claude-sonnet-4-6"),
            llm_timeout_s=_env_float("AUDITGUARD_LLM_TIMEOUT_S", 60.0),
            llm_max_retries=_env_int("AUDITGUARD_LLM_MAX_RETRIES", 2),
            max_upload_bytes=_env_int("AUDITGUARD_MAX_UPLOAD_MB", 20) * 1024 * 1024,
            max_rows=_env_int("AUDITGUARD_MAX_ROWS", 500_000),
            max_stored_runs=_env_int("AUDITGUARD_MAX_STORED_RUNS", 50),
            allowed_origins=_env_list("AUDITGUARD_ALLOWED_ORIGINS"),
            spec_path=Path(os.getenv("AUDITGUARD_SPEC_FILE", str(DEFAULT_SPEC_PATH))),
            frontend_dir=frontend_dir if frontend_dir.is_dir() else None,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
