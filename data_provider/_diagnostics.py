# -*- coding: utf-8 -*-
"""Diagnostic adapters for data_provider — thin wrappers that keep base.py
free of top-level src/ imports while preserving fail-open behaviour."""

from __future__ import annotations

from typing import Any, Optional


def record_provider_run(
    *,
    data_type: str,
    provider: str,
    operation: str,
    success: bool,
    latency_ms: Optional[int] = None,
    error_type: Optional[str] = None,
    error_message: Optional[Any] = None,
    fallback_from: Optional[str] = None,
    fallback_to: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    stale_seconds: Optional[int] = None,
    record_count: Optional[int] = None,
) -> None:
    """Delegate to src.services.run_diagnostics (lazy import, no module-level dep)."""
    try:
        from src.services.run_diagnostics import record_provider_run as _inner
        _inner(
            data_type=data_type,
            provider=provider,
            operation=operation,
            success=success,
            latency_ms=latency_ms,
            error_type=error_type,
            error_message=error_message,
            fallback_from=fallback_from,
            fallback_to=fallback_to,
            cache_hit=cache_hit,
            stale_seconds=stale_seconds,
            record_count=record_count,
        )
    except Exception:
        pass  # diagnostics are fail-open by design


def record_provider_run_started(
    *,
    data_type: str,
    provider: str,
    operation: str,
) -> None:
    """Delegate to src.services.run_diagnostics (lazy import, no module-level dep)."""
    try:
        from src.services.run_diagnostics import record_provider_run_started as _inner
        _inner(
            data_type=data_type,
            provider=provider,
            operation=operation,
        )
    except Exception:
        pass  # diagnostics are fail-open by design
