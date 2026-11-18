#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ani_diag.py - Render Target AFBC & Fallback Diagnostic Utility for ani-driver
"""

import os
import sys
import re
from datetime import datetime

LOG_FILE = "ani_diag_output.log"

def init_diagnostic_logger():
    """Initializes the diagnostic log file with header info."""
    print("[*] Initializing ani-driver diagnostic tracker...")
    with open(LOG_FILE, "w") as f:
        f.write(f"=== ani-driver Diagnostic & Fallback Report ===\n")
        f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*60}\n\n")

def parse_emulator_log_stream(log_path):
    """
    Parses the emulator log stream (e.g., vita3k.log) to extract Vulkan format queries,
    uncompressed fallbacks, and AFBC modifier states.
    """
    results = {
        "format_queries": 0,
        "format_false_fallbacks": 0,
        "afbc_active": 0,
        "fallback_reasons": {}
    }
    
    # Regular expressions for matching driver/emulator log flags
    format_pattern = re.compile(r"Support for format \[([^\]]+)\] with tiling optimal: (true|false)", re.IGNORECASE)
    afbc_pattern = re.compile(r"\[MALI_AFBC\] Target: (\w+) \| Format: ([^\s]+) \| AFBC: (TRUE|FALSE) \| Rule: (.+)")
    
    if not os.path.exists(log_path):
        print(f"[!] Log path not found: {log_path}")
        return results

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            # Check for optimal tiling format rejections
            fmt_match = format_pattern.search(line)
            if fmt_match:
                fmt_name, status = fmt_match.groups()
                results["format_queries"] += 1
                if status.lower() == "false":
                    results["format_false_fallbacks"] += 1

            # Check for explicit AFBC tracking tags
            afbc_match = afbc_pattern.search(line)
            if afbc_match:
                target_type, fmt, status, rule = afbc_match.groups()
                if status == "TRUE":
                    results["afbc_active"] += 1
                else:
                    results["fallback_reasons"][rule] = results["fallback_reasons"].get(rule, 0) + 1

    return results

def generate_diagnostic_summary(stats):
    """Writes a parsed summary of performance bottlenecks and fallbacks to the log file."""
    summary = (
        f"\n--- Diagnostic Log Analysis Summary ---\n"
        f"Total Format Tiling Queries: {stats['format_queries']}\n"
        f"Format Tiling Rejections (false): {stats['format_false_fallbacks']}\n"
        f"Successful AFBC Targets: {stats['afbc_active']}\n"
        f"Uncompressed Fallback Rules Triggered:\n"
    )
    
    if stats["fallback_reasons"]:
        for rule, count in stats["fallback_reasons"].items():
            summary += f"  - [{rule}]: {count} occurrences\n"
    else:
        summary += f"  - No explicit [MALI_AFBC] rejection rules captured in this log pass.\n"
        
    print(summary)
    with open(LOG_FILE, "a") as f:
        f.write(summary)

if __name__ == "__main__":
    init_diagnostic_logger()
    
    # Point this to your pulled vita3k.log file path for testing
    target_log_file = sys.argv[1] if len(sys.argv) > 1 else "vita3k.log"
    
    print(f"[*] Parsing target log file: {target_log_file}")
    statistics = parse_emulator_log_stream(target_log_file)
    generate_diagnostic_summary(statistics)
            
