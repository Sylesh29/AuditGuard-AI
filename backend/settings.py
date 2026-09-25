"""Runtime configuration: environment settings and the process-spec file."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_SPEC_PATH = BACKEND_DIR / "config" / "spec_limits.json"
DEFAULT_FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


class SpecError(ValueError):
    """The process-spec file is malformed. Raised at startup, never mid-audit."""


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
    fail_statuses: frozenset[str]
    default: dict[str, Limit]
    products: dict[str, dict[str, Limit]]
    units: UnitSpec
    outlier_z: float = 3.5
    sha256: str = ""

    def limits_for(self, product_id: object) -> dict[str, Limit]:
        merged = dict(self.default)
        if isinstance(product_id, str):
            merged.update(self.products.get(product_id.strip(), {}))
        return merged

    @property
    def parameters(self) -> list[str]:
        params = dict.fromkeys(self.default)
        for limits in self.products.values():
            params.update(dict.fromkeys(limits))
        return list(params)

    @property
    def known_statuses(self) -> frozenset[str]:
        return self.pass_statuses | self.fail_statuses

    @classmethod
    def from_dict(cls, raw: dict, sha256: str = "") -> SpecLimits:
        if not isinstance(raw, dict):
            raise SpecError("The spec file must contain a JSON object.")

        def limits(block: object, where: str) -> dict[str, Limit]:
            if not isinstance(block, dict):
                raise SpecError(f"{where} must be an object of parameter limits.")
            parsed = {}
            for param, bounds in block.items():
                if not isinstance(bounds, dict) or not {"min", "max"} & set(bounds):
                    raise SpecError(f"{where}.{param} needs a 'min' and/or 'max'.")
                lo, hi = bounds.get("min"), bounds.get("max")
                for value in (lo, hi):
                    if value is not None and not isinstance(value, int | float):
                        raise SpecError(f"{where}.{param} limits must be numbers.")
                if lo is not None and hi is not None and lo > hi:
                    raise SpecError(f"{where}.{param} has min greater than max.")
                parsed[param] = Limit(min=lo, max=hi)
            return parsed

        units_raw = raw.get("units", {})
        factors = {k.strip().lower(): float(v)
                   for k, v in units_raw.get("to_canonical", {"kg": 1}).items()}
        canonical = units_raw.get("canonical", "kg").strip().lower()
        if factors.get(canonical) != 1.0:
            raise SpecError(f"units.to_canonical must map the canonical unit '{canonical}' to 1.")
        if any(f <= 0 for f in factors.values()):
            raise SpecError("Unit conversion factors must be positive.")

        outlier_z = raw.get("outlier_robust_z", 3.5)
        if not isinstance(outlier_z, int | float) or outlier_z <= 0:
            raise SpecError("outlier_robust_z must be a positive number.")

        pass_statuses = frozenset(s.strip().upper() for s in raw.get("pass_statuses", ["PASS"]))
        fail_statuses = frozenset(s.strip().upper() for s in raw.get("fail_statuses", ["FAIL"]))
        if pass_statuses & fail_statuses:
            raise SpecError("A status cannot be both a pass and a fail status.")

        return cls(
            pass_statuses=pass_statuses,
            fail_statuses=fail_statuses,
            default=limits(raw.get("default", {}), "default"),
            products={pid.strip(): limits(block, f"products.{pid}")
                      for pid, block in raw.get("products", {}).items()},
            units=UnitSpec(
                column=units_raw.get("column", "unit"),
                quantity_column=units_raw.get("quantity_column", "quantity"),
                canonical=canonical,
                to_canonical=factors,
            ),
            outlier_z=float(outlier_z),
            sha256=sha256,
        )

    @classmethod
    def load(cls, path: Path) -> SpecLimits:
        content = path.read_bytes()
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SpecError(f"{path} is not valid JSON: {exc}") from exc
        return cls.from_dict(raw, sha256=hashlib.sha256(content).hexdigest())


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
    run_ttl_s: int
    max_concurrent_runs: int
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
            max_upload_bytes=_env_int("AUDITGUARD_MAX_UPLOAD_MB", 50) * 1024 * 1024,
            max_rows=_env_int("AUDITGUARD_MAX_ROWS", 500_000),
            max_stored_runs=_env_int("AUDITGUARD_MAX_STORED_RUNS", 20),
            run_ttl_s=_env_int("AUDITGUARD_RUN_TTL_MINUTES", 120) * 60,
            max_concurrent_runs=_env_int("AUDITGUARD_MAX_CONCURRENT_RUNS", 2),
            allowed_origins=_env_list("AUDITGUARD_ALLOWED_ORIGINS"),
            spec_path=Path(os.getenv("AUDITGUARD_SPEC_FILE", str(DEFAULT_SPEC_PATH))),
            frontend_dir=frontend_dir if frontend_dir.is_dir() else None,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
