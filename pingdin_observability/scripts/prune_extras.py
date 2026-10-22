#!/usr/bin/env python3
"""
scripts/prune_extras.py — BUILD_LOG Journey 20 (observability) / 399 (platform). Standard library only.

A SAFE MIRROR for scripts/sync_to_server.sh. The sync extracts over the existing files and never deletes, so a file you
removed or renamed in the repo would linger on the server (an obsolete test pytest still runs; an old dashboard Grafana
still provisions). The old habit — `sudo rm -rf <every repo directory>` then copy — handled that, but it also (a) gives each
directory a new inode, orphaning every container that bind-mounts one (Grafana applied no dashboard change for hours on
3 Oct 2026) and (b) deletes server-generated files (the platform's `<service>/constraints.txt` version locks vanished).

This does what the habit was FOR, without either side effect:
  * removes FILES that are not in the manifest (the list of files the laptop just pushed);
  * never replaces a directory — only files go, then directories left EMPTY that the repo no longer has;
  * never touches server-owned things: .env, .venv/, .fingerprints/, backups/, *.log, __pycache__, .git, version locks
    (*/constraints.txt), the manifest itself; never follows or removes symlinks;
  * REFUSES (exit 3, deletes nothing) if it would remove more than --max files (default 40): a partial or wrong local folder
    would otherwise wipe the server. Re-run with a larger --max only after reading the list it prints.

  python3 scripts/prune_extras.py --manifest FILE|-  [--root DIR] [--max N] [--dry-run]
Exit: 0 done / nothing to do · 2 bad input · 3 refused (too many) .
"""
import argparse
import fnmatch
import os
import sys

PROTECTED_DIRS = {".venv", ".fingerprints", "backups", ".git", "__pycache__", ".pytest_cache", "node_modules"}
PROTECTED_NAMES = {".env", ".sync_manifest"}
PROTECTED_GLOBS = ("*.log", "*.pyc", "*constraints.txt")
MIN_MANIFEST = 10   # a manifest smaller than this is a mistake, not a repo


def is_protected(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in PROTECTED_DIRS for p in parts):
        return True
    if parts[-1] in PROTECTED_NAMES:
        return True
    return any(fnmatch.fnmatch(rel, g) for g in PROTECTED_GLOBS)


def read_manifest(stream) -> set:
    keep = set()
    for line in stream:
        rel = line.strip().replace("\\", "/")
        if not rel:
            continue
        if rel.startswith("/") or ".." in rel.split("/"):
            raise ValueError(f"unsafe path in manifest: {rel!r}")
        keep.add(rel[2:] if rel.startswith("./") else rel)
    return keep


def candidates(root: str, keep: set):
    """Regular files under root that are neither in the manifest nor protected. Symlinks are never candidates."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames[:] = [d for d in dirnames if d not in PROTECTED_DIRS]
        for f in filenames:
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            p = os.path.join(dirpath, f)
            if rel in keep or is_protected(rel) or os.path.islink(p) or not os.path.isfile(p):
                continue
            out.append(rel)
    return sorted(out)


def empty_dirs_to_remove(root: str, keep: set):
    keep_dirs = set()
    for rel in keep:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            keep_dirs.add("/".join(parts[:i]))
    out, marked = [], set()
    for dirpath, dirnames, _ in os.walk(root, topdown=False, followlinks=False):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel == "." or rel in keep_dirs or is_protected(rel + "/x") or os.path.islink(dirpath):
            continue
        # removable if EVERYTHING inside is itself a directory already marked for removal (a chain of emptied dirs)
        if all(os.path.join(dirpath, e) in marked for e in os.listdir(dirpath)):
            marked.add(dirpath)
            out.append(rel)
    return out


def main(argv=None, stdin=sys.stdin, out=sys.stdout):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        keep = read_manifest(stdin if a.manifest == "-" else open(a.manifest, encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"prune: {e}", file=out)
        return 2
    if len(keep) < MIN_MANIFEST:
        print(f"prune: manifest has only {len(keep)} file(s) — refusing to prune against it", file=out)
        return 2
    root = os.path.realpath(a.root)
    files = candidates(root, keep)
    if len(files) > a.max:
        print(f"prune: REFUSING — {len(files)} file(s) would be removed (limit {a.max}); nothing was deleted. "
              f"A partial or wrong local folder looks like this. First 50:", file=out)
        for rel in files[:50]:
            print(f"  would remove {rel}", file=out)
        print("prune: if this really is intended, re-run with --prune-max N (sync_to_server.sh) after reading the list", file=out)
        return 3
    verb = "would remove" if a.dry_run else "removed"
    for rel in files:
        if not a.dry_run:
            os.remove(os.path.join(root, rel))
        print(f"  {verb} {rel}", file=out)
    dirs = empty_dirs_to_remove(root, keep) if not a.dry_run else []
    # deepest first (topdown=False already yields that), re-check emptiness as parents empty out
    removed_dirs = 0
    for rel in dirs:
        p = os.path.join(root, rel)
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
            removed_dirs += 1
            print(f"  removed empty directory {rel}/", file=out)
    if not files and not removed_dirs:
        print("prune: nothing to remove — the server matches the repo", file=out)
    else:
        print(f"prune: {len(files)} file(s) {'would be ' if a.dry_run else ''}removed, {removed_dirs} empty director(ies) removed", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
