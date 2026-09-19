"""SAAC service configuration."""
import os


def setting(name, default=None):
    return os.environ.get("SAAC_" + name, default)
