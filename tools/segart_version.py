"""Capture software versions for inclusion in produced metadata files.

Every TOC and articles file we write carries a `software_versions` block
so downstream consumers (and our future selves) know what produced it.
The block deliberately captures more than just our own git SHA — it also
includes interpreter, key dependencies, and external tool versions when
known.
"""
import json
import platform
import subprocess
import sys
from pathlib import Path

SEGART = Path("/Users/brewster/tmp/segart")


def _safe(callable_, default=None):
    try:
        return callable_()
    except Exception:
        return default


def _git_sha():
    r = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=SEGART, capture_output=True, text=True
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _git_dirty():
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=SEGART, capture_output=True, text=True
    )
    return bool(r.stdout.strip()) if r.returncode == 0 else None


def _ia_cli_version():
    r = subprocess.run(
        ["ia", "--version"], capture_output=True, text=True
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _pkg_version(name):
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def software_versions(extra: dict = None) -> dict:
    """Return a dict suitable for embedding as a `software_versions` block
    in produced metadata files. Includes:
      - segart git SHA (+ dirty flag)
      - Python interpreter version
      - ia CLI version
      - key Python deps when installed
      - extra fields passed in by caller (e.g. crossref_data_pulled_at)
    """
    out = {
        "segart_git_sha":     _safe(_git_sha),
        "segart_git_dirty":   _safe(_git_dirty),
        "python":             sys.version.split()[0],
        "platform":           platform.platform(),
        "ia_cli":             _safe(_ia_cli_version),
        "deps": {
            "anthropic":      _safe(lambda: _pkg_version("anthropic")),
            "docling":        _safe(lambda: _pkg_version("docling")),
            "internetarchive": _safe(lambda: _pkg_version("internetarchive")),
        },
    }
    if extra:
        out.update(extra)
    return out


if __name__ == "__main__":
    print(json.dumps(software_versions(), indent=2))
