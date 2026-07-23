#!/usr/bin/env python3
"""Secret scan for the repository.

Greps the tree for accidentally-committed credentials:
- Cloud provider access keys (AWS)
- API keys / tokens (NovoMCP, OpenAI, GitHub, Slack)

Run before every release. Wired into `.github/workflows/smoke.yml`.
Exits non-zero if any pattern hits.

Usage:
    python3 scripts/security_scan.py [--verbose]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# (pattern, description). Every pattern is tested with re.search across all
# text files. Zero matches expected across the entire repo. Add new secret
# formats here as they appear.
PATTERNS: list[tuple[str, str]] = [
    (r"AKIA[0-9A-Z]{16}",
     "AWS access key ID leaked"),
    (r"\bnmcp_[A-Za-z0-9]{20,}",
     "NovoMCP core API key leaked"),
    (r"\bncmcp_[A-Za-z0-9]{20,}",
     "NovoMCP compute API key leaked"),
    (r"sk-[a-zA-Z0-9]{40,}",
     "OpenAI API key leaked"),
    (r"ghp_[A-Za-z0-9]{36}",
     "GitHub personal-access token leaked"),
    (r"xoxb-[0-9]+-[0-9]+-[a-zA-Z0-9]+",
     "Slack bot token leaked"),
]

# File extensions to scan. Skip binaries, generated files, node_modules, etc.
TEXT_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".md", ".txt",
             ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml", ".env",
             ".dockerfile"}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
             "site", ".next"}

# Files that legitimately contain pattern strings (the scan script's own
# pattern definitions match its own regex). Skipped from all patterns.
SELF_SKIP = {"scripts/security_scan.py"}

# Allowlist entries: (path glob, pattern) — where a known-good match lives.
# Extend when a legitimate match appears.
ALLOWLIST: list[tuple[str, str]] = []


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # skip directories in the pruning set
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in TEXT_EXTS:
            continue
        rel = str(path.relative_to(root))
        if rel in SELF_SKIP:
            continue
        yield path


def _is_allowed(rel_path: str, pattern: str) -> bool:
    from fnmatch import fnmatch
    for path_glob, allowed_pattern in ALLOWLIST:
        if fnmatch(rel_path, path_glob) and allowed_pattern == pattern:
            return True
    return False


def scan(root: Path, verbose: bool = False) -> int:
    failures: list[str] = []

    for pattern, description in PATTERNS:
        compiled = re.compile(pattern)
        hits: list[tuple[str, int, str]] = []
        for path in _iter_files(root):
            rel = str(path.relative_to(root))
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line_no, line in enumerate(fh, start=1):
                        for m in compiled.finditer(line):
                            match_text = m.group()
                            if _is_allowed(rel, pattern):
                                continue
                            hits.append((rel, line_no, match_text))
            except UnicodeDecodeError:
                continue

        if hits:
            failures.append(f"[FAIL] {description}\n  pattern: {pattern}\n  hits ({len(hits)}):")
            for rel, ln, snippet in hits[:15]:
                # trim snippet for readability
                snippet = snippet[:120]
                failures.append(f"    {rel}:{ln}: {snippet}")
            if len(hits) > 15:
                failures.append(f"    ... and {len(hits) - 15} more")
        elif verbose:
            print(f"[OK]   {description}")

    if failures:
        print("=" * 70)
        print("SECURITY SCAN FAILED")
        print("=" * 70)
        for line in failures:
            print(line)
        print()
        print("Fix each hit above before releasing. Add legitimate matches to")
        print("the ALLOWLIST in scripts/security_scan.py with an inline reason.")
        return 1

    print(f"security scan: {len(PATTERNS)} patterns, 0 hits, all clean")
    return 0


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    root = Path(__file__).resolve().parent.parent
    sys.exit(scan(root, verbose=verbose))
