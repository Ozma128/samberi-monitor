from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED_ARTIFACTS = {".pyc", ".zip"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yml", ".yaml", ".sh", ".ps1"}
TEXT_FILENAMES = {".env.example", ".dockerignore", "Dockerfile"}
SECRET_PATTERNS = {
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "Google API key (AQ format)": re.compile(r"\bAQ\.[A-Za-z0-9_-]{30,}\b"),
    "Telegram bot token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "hard-coded server password": re.compile(
        r"(?i)\b(?:root|vps)[_-]?password\s*=\s*['\"][^'\"\r\n]{8,}['\"]"
    ),
}


def _tracked_paths() -> list[Path]:
    if not (PROJECT_ROOT / ".git").exists():
        pytest.skip("Git metadata is not available in this source package.")
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return [PROJECT_ROOT / item for item in result.stdout.decode("utf-8").split("\0") if item]


def test_tracked_tree_has_no_generated_binary_artifacts() -> None:
    offenders = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in _tracked_paths()
        if path.exists()
        and (
            path.suffix.casefold() in FORBIDDEN_TRACKED_ARTIFACTS
            or path.name == "test_monitoring_output.xlsx"
        )
    ]
    assert offenders == []


def test_tracked_text_has_no_high_confidence_secrets() -> None:
    offenders: list[str] = []
    for path in _tracked_paths():
        if not path.is_file() or (
            path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in TEXT_FILENAMES
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()}: {label}")
    assert offenders == []
