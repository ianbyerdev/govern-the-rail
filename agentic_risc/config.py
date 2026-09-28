"""Agentic RISC service configuration."""
import os


def setting(name, default=None):
    return os.environ.get("AGENTIC_RISC_" + name, default)
