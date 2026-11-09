"""Reject secret files, and on a pull request allow only claim locks and finished PNGs."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from repo import load_districts, repo_root

CLAIM_RE = re.compile(r"claims/([a-z0-9]+(?:-[a-z0-9]+)*)\.md")
RESULT_RE = re.compile(r"results/([a-z0-9]+(?:-[a-z0-9]+)*)\.png")
FORBIDDEN_NAMES = {"id_rsa", "id_ed25519", "id_dsa", "credentials.json"}
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def forbidden_reason(path: str) -> str | None:
    name = Path(path).name.lower()
    if name == ".env" or name.startswith(".env."):
        return "environment file"
    if name in FORBIDDEN_NAMES:
        return "credential file"
    if name.endswith(FORBIDDEN_SUFFIXES):
        return "key file"
    return None


def pull_request_change_ok(status: str, path: str, districts: set[str]) -> bool:
    if status.startswith("R"):
        return False
    claim = CLAIM_RE.fullmatch(path)
    result = RESULT_RE.fullmatch(path)
    if status == "D":
        return claim is not None and claim.group(1) in districts
    if status not in {"A", "M"}:
        return False
    if claim is not None:
        return claim.group(1) in districts
    if result is not None:
        return result.group(1) in districts
    return False


def scan_tree(root: Path) -> list[str]:
    bad = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        reason = forbidden_reason(rel)
        if reason:
            bad.append(f"{rel} ({reason})")
    return bad


def _zero(sha: str | None) -> bool:
    if not sha:
        return True
    return set(sha) <= {"0"}


def diff_problems(root: Path, base: str, head: str, districts: set[str]) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-status", f"{base}...{head}"],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return [f"Could not diff {base}...{head}"]
    problems = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        reason = forbidden_reason(path)
        if reason:
            problems.append(f"{path} ({reason})")
            continue
        if not pull_request_change_ok(status, path, districts):
            problems.append(
                f"{path} is not allowed in a contributor pull request. "
                "Only claims/<district>.md and results/<district>.png may change."
            )
    return problems


def main() -> int:
    root = repo_root()
    problems = scan_tree(root)
    event = os.environ.get("EVENT", "")
    if event == "pull_request":
        base = os.environ.get("BASE_SHA", "")
        head = os.environ.get("HEAD_SHA", "")
        if _zero(base) or _zero(head):
            problems.append("Pull request is missing a base or head revision.")
        else:
            districts = set(load_districts(root))
            problems.extend(diff_problems(root, base, head, districts))
    if problems:
        print("This push is outside the contributor door:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("door check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
