#!/usr/bin/env python3
"""
ani_diag.py - Diagnostic & Logging Core
Logs system memory status and execution steps to persistent storage.
"""
import os
import datetime

LOG_FILE = os.environ.get("ANI_DIAG_LOG", "/sdcard/Download/ani_driver_diag.log")

def log(msg: str, level: str = "INFO") -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [{level}] {msg}"
    print(formatted)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        fallback = "ani_driver_build_diag.log"
        with open(fallback, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")

def check_memory():
    log("Checking system memory state...", "DIAG")
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if any(k in line for k in ("MemTotal", "MemAvailable", "SwapFree")):
                    log(f"MEM: {line.strip()}", "DIAG")
    except Exception as e:
        log(f"Could not read /proc/meminfo: {e}", "WARN")

if __name__ == "__main__":
    check_memory()
    
