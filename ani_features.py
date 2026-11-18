#!/usr/bin/env python3
"""
ani_features.py - Configures driver-level feature overrides, AFBC options, and performance hints.
"""
import os
from ani_diag import log

def configure_features():
    log("Configuring driver feature flags and memory guardrails...")
    os.environ["MESA_NO_ERROR"] = "1"
    os.environ["TU_DEBUG"] = "noconform"
    log("Injected driver environment flags (MESA_NO_ERROR=1, TU_DEBUG=noconform)", "OK")

if __name__ == "__main__":
    configure_features()
    
