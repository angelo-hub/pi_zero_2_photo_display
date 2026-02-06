"""
Lightweight 24-hour telemetry for CPU and RAM.
Stores samples in SQLite and prunes data older than 24h.
Uses only /proc (no psutil) for minimal overhead on Pi Zero 2.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# Store DB in same place as state
TELEMETRY_DB = Path.home() / ".photoframe" / "telemetry.db"
# Keep 24 hours of data; sample every 60 seconds -> 1440 rows max
RETENTION_SECONDS = 24 * 3600
# For CPU % we need previous sample (idle, total) to compute delta
_last_cpu_idle: Optional[float] = None
_last_cpu_total: Optional[float] = None
_last_cpu_ts: Optional[float] = None


def _ensure_db() -> None:
    """Create DB and table if needed."""
    TELEMETRY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TELEMETRY_DB), timeout=10)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS metrics (
                ts INTEGER NOT NULL,
                cpu_percent REAL,
                mem_used_mb REAL,
                mem_total_mb REAL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)")
        conn.commit()
    finally:
        conn.close()


def _read_cpu() -> Tuple[Optional[float], Optional[float], float]:
    """Read /proc/stat; return (idle, total, now). Total and idle in jiffies."""
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        # cpu  user nice system idle iowait irq softirq steal guest guest_nice
        parts = line.split()
        if parts[0] != "cpu" or len(parts) < 5:
            return None, None, time.time()
        user = int(parts[1])
        nice = int(parts[2])
        system = int(parts[3])
        idle = int(parts[4])
        iowait = int(parts[5]) if len(parts) > 5 else 0
        irq = int(parts[6]) if len(parts) > 6 else 0
        softirq = int(parts[7]) if len(parts) > 7 else 0
        steal = int(parts[8]) if len(parts) > 8 else 0
        total = user + nice + system + idle + iowait + irq + softirq + steal
        return float(idle), float(total), time.time()
    except Exception as e:
        logger.debug("Telemetry: read cpu failed: %s", e)
        return None, None, time.time()


def _read_mem() -> Tuple[Optional[float], Optional[float]]:
    """Read /proc/meminfo; return (used_mb, total_mb)."""
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip().split()[0]
                    meminfo[key] = int(value)
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        used_kb = total_kb - available_kb
        return used_kb / 1024.0, total_kb / 1024.0
    except Exception as e:
        logger.debug("Telemetry: read mem failed: %s", e)
        return None, None


def collect() -> None:
    """Sample CPU and memory once and append to DB. Prune rows older than 24h."""
    global _last_cpu_idle, _last_cpu_total, _last_cpu_ts
    _ensure_db()
    ts = int(time.time())
    idle, total, now_float = _read_cpu()
    mem_used, mem_total = _read_mem()
    cpu_percent: Optional[float] = None
    if idle is not None and total is not None and _last_cpu_idle is not None and _last_cpu_total is not None:
        delta_idle = idle - _last_cpu_idle
        delta_total = total - _last_cpu_total
        if delta_total > 0:
            cpu_percent = (1.0 - delta_idle / delta_total) * 100.0
            cpu_percent = max(0.0, min(100.0, cpu_percent))
    _last_cpu_idle = idle
    _last_cpu_total = total
    _last_cpu_ts = now_float
    if mem_used is None:
        mem_used = 0.0
    if mem_total is None:
        mem_total = 0.0
    conn = sqlite3.connect(str(TELEMETRY_DB), timeout=10)
    try:
        conn.execute(
            "INSERT INTO metrics (ts, cpu_percent, mem_used_mb, mem_total_mb) VALUES (?, ?, ?, ?)",
            (ts, cpu_percent, mem_used, mem_total),
        )
        cutoff = ts - RETENTION_SECONDS
        conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        conn.commit()
    except Exception as e:
        logger.warning("Telemetry collect failed: %s", e)
    finally:
        conn.close()


def get_24h() -> dict:
    """Return last 24h of metrics for API: { 'cpu': [[ts, percent], ...], 'memory': [[ts, used_mb], ...], 'memory_percent': [[ts, percent], ...] }."""
    _ensure_db()
    cutoff = int(time.time()) - RETENTION_SECONDS
    conn = sqlite3.connect(str(TELEMETRY_DB), timeout=10)
    try:
        rows = conn.execute(
            "SELECT ts, cpu_percent, mem_used_mb, mem_total_mb FROM metrics WHERE ts >= ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    cpu = []
    memory = []
    memory_percent = []
    for ts, cpu_pct, mem_used, mem_total in rows:
        cpu.append([ts, round(cpu_pct, 1) if cpu_pct is not None else None])
        memory.append([ts, round(mem_used, 1) if mem_used is not None else None])
        if mem_total and mem_total > 0 and mem_used is not None:
            memory_percent.append([ts, round(100.0 * mem_used / mem_total, 1)])
        else:
            memory_percent.append([ts, None])
    return {"cpu": cpu, "memory": memory, "memory_percent": memory_percent}
