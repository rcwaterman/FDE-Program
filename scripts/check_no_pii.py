"""Fail if files that would be committed (or a folder given on the command line) hold personal or client data.

Scans git's view of the repo: tracked files plus untracked files that are not ignored. Ignored paths such as
src/data/ and runs/ are never read. Each finding prints the file, line and pattern with the match redacted.

Usage:
    python scripts/check_no_pii.py                 # the repo, as git sees it
    python scripts/check_no_pii.py <folder>        # any folder, e.g. an unpacked Docker image
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Documentation placeholders, GitHub's no-reply addresses, and the course's public contact domain. None is a person.
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com", "aimakerspace.io")

PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<![\d-])\d{3}[-. ]\d{3}[-. ]\d{4}(?![\d-])"),
    "ssn": re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
    # Real Salesforce ids: 15 or 18 characters, prefixed by the object key. Synthetic ids use 069SYN and are allowed.
    "salesforce_id": re.compile(r"\b(?:02[sS]|069|500)(?!SYN\d{12}\b)[A-Za-z0-9]{12,15}\b"),
    "local_user_path": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "tenant_path": re.compile(r"OneDrive-[A-Za-z]+"),
}

# This file names the patterns it looks for, so it cannot scan itself.
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".yml", ".yaml", ".toml", ".csv", ".html", ".sh",
                 ".ini", ".cfg", ".env", ".template", ".ipynb", ".js", ".ts", ".css", ""}


def _files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        return [root / p for p in out if (root / p).is_file()]
    return [p for p in root.rglob("*") if p.is_file()]


def _redact(text: str) -> str:
    return text[:3] + "***" if len(text) > 3 else "***"


def scan_file(path: Path) -> list[str]:
    if path.resolve() == SELF or path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    findings = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return []
    for n, line in enumerate(lines, 1):
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                value = m.group(0)
                if name == "email" and value.lower().split("@")[-1] in ALLOWED_EMAIL_DOMAINS:
                    continue
                findings.append(f"{path}:{n}: {name}: {_redact(value)}")
    return findings


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else REPO_ROOT
    findings = []
    for path in _files(root):
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        if rel.parts[:2] == ("src", "data"):
            findings.append(f"{path}: real client data folder must not be committed")
            continue
        findings.extend(scan_file(path))
    for f in findings:
        print(f)
    print(f"{len(findings)} finding(s) in {root}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
