"""
RAM profiling (debug).
When debug.ram_profiling is enabled, logs process RSS periodically to help
identify memory growth on low-RAM devices (e.g. Pi Zero 2).
Uses /proc on Linux for minimal overhead; optional tracemalloc for allocation details.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_tracemalloc_started = False


def _get_rss_kb() -> Optional[int]:
    """Read process RSS in KiB from /proc/self/status (Linux). Returns None on failure."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
                    return None
    except (OSError, ValueError):
        pass
    return None


def get_rss_mb() -> Optional[float]:
    """Current process resident set size in MiB. None if unavailable."""
    kb = _get_rss_kb()
    if kb is not None:
        return round(kb / 1024.0, 2)
    return None


def log_memory(label: Optional[str] = None) -> None:
    """
    Log current process RSS. No-op if debug.ram_profiling is disabled.
    Optional label for context (e.g. "after_sync", "after_convert").
    """
    try:
        from .config_manager import config
        if not config.get("debug.ram_profiling", False):
            return
    except Exception:
        return

    rss_mb = get_rss_mb()
    if rss_mb is not None:
        msg = f"RAM RSS={rss_mb} MiB"
        if label:
            msg = f"[{label}] {msg}"
        logger.info(msg)

    if config.get("debug.tracemalloc", False):
        _log_tracemalloc_top(label)


def _log_tracemalloc_top(label: Optional[str] = None, limit: int = 8) -> None:
    """Log top allocation sites from tracemalloc (if enabled and started)."""
    global _tracemalloc_started
    try:
        import tracemalloc
        if not _tracemalloc_started:
            tracemalloc.start()
            _tracemalloc_started = True
        snap = tracemalloc.take_snapshot()
        top = snap.statistics("lineno")[:limit]
        lines = ["tracemalloc top (current size):"]
        for i, stat in enumerate(top, 1):
            lines.append(f"  {i}. {stat.size / 1024:.1f} KiB  {stat.traceback}")
        msg = "\n".join(lines)
        if label:
            msg = f"[{label}] {msg}"
        logger.debug(msg)
    except Exception as e:
        logger.debug("tracemalloc snapshot failed: %s", e)


def start_tracemalloc_if_enabled() -> None:
    """Start tracemalloc if debug.tracemalloc is True. Call once at startup if using."""
    try:
        from .config_manager import config
        if not config.get("debug.tracemalloc", False):
            return
        import tracemalloc
        global _tracemalloc_started
        if not _tracemalloc_started:
            tracemalloc.start()
            _tracemalloc_started = True
            logger.info("RAM profiler: tracemalloc started (allocation tracking enabled)")
    except Exception as e:
        logger.debug("Could not start tracemalloc: %s", e)


def collect() -> None:
    """
    Single sample: log RSS (and optional tracemalloc). Intended to be
    called periodically by the scheduler when ram_profiling is enabled.
    """
    log_memory("periodic")
