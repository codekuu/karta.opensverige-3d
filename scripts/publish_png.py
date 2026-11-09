"""Commit and push only results/<district>.png.

Other files in the worktree are left unstaged. Environment files, keys, and
source renders are never added.
"""

from __future__ import annotations

import argparse
import sys

from repo import branch_name, git_out, has_origin, load_districts, repo_root, require_identity, run_git

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def publish_png(root, slug: str, push: bool = True) -> int:
    districts = load_districts(root)
    if slug not in districts:
        raise SystemExit(f"Unknown district {slug!r}.")
    claim = root / "claims" / f"{slug}.md"
    dest = root / "results" / f"{slug}.png"
    rel = f"results/{slug}.png"
    if not claim.exists():
        raise SystemExit(f"No claim for {slug}. Claim it before publishing.")
    if not dest.is_file():
        raise SystemExit(f"Missing {rel}. Run the redraw first.")
    header = dest.read_bytes()[:8]
    if header != PNG_MAGIC:
        raise SystemExit(f"{rel} is not a PNG. The redraw writes the PNG; do not replace it by hand.")
    if dest.stat().st_size < 64:
        raise SystemExit(f"{rel} is too small to be a finished tile.")
    require_identity(root)
    # Drop anything already staged so a secret cannot ride along in this commit.
    run_git(root, ["reset", "-q"])
    run_git(root, ["add", "--", rel])
    staged = [line for line in git_out(root, ["diff", "--cached", "--name-only"]).splitlines() if line]
    if staged != [rel]:
        run_git(root, ["reset", "-q"], check=False)
        raise SystemExit(f"Refusing to commit because the index was not exactly {rel}.")
    commit = run_git(root, ["commit", "-m", f"Add {slug} district tile"], check=False)
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout or "").strip()
        raise SystemExit(f"Could not commit {rel}.\n{detail}")
    if not push:
        print(f"Committed {rel}. Push it to publish the tile.")
        return 0
    if not has_origin(root):
        raise SystemExit("Committed locally, but this clone has no origin remote to push to.")
    branch = branch_name(root)
    pushed = run_git(root, ["push", "origin", f"HEAD:{branch}"], check=False)
    if pushed.returncode != 0:
        detail = (pushed.stderr or pushed.stdout or "").strip()
        raise SystemExit(f"The PNG commit is local only. Push failed.\n{detail}")
    print(f"Published {rel}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish one finished district PNG and nothing else.")
    parser.add_argument("--district", required=True)
    parser.add_argument("--no-push", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return publish_png(repo_root(), args.district, push=not args.no_push)


if __name__ == "__main__":
    sys.exit(main())
