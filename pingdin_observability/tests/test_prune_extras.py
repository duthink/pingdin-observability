"""
Run: python3 tests/test_prune_extras.py   (or pytest)

BUILD_LOG Journey 20 / 399. scripts/prune_extras.py runs against real temp directory trees. What is pinned: it removes
only files that left the repo; it never replaces a directory (inode kept); it never touches server-owned files; it
refuses rather than wipe the server when the local folder is partial; and the incident it replaces (delete-and-copy
orphaning directory mounts and deleting version locks) cannot be reproduced through it.
"""
import io
import os
import subprocess
import sys
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import prune_extras as pe  # noqa: E402


def _tree(files, extra=()):
    d = tempfile.mkdtemp()
    for rel in list(files) + list(extra):
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("x")
    return d


_REPO = [f"pkg/m{i}.py" for i in range(12)] + ["README.md", "grafana/provisioning/dashboards/a.json"]


def _run(root, manifest, *args):
    out = io.StringIO()
    rc = pe.main(["--manifest", "-", "--root", root, *args], stdin=io.StringIO("\n".join(manifest) + "\n"), out=out)
    return rc, out.getvalue()


def test_files_that_left_the_repo_are_removed_and_everything_else_stays():
    root = _tree(_REPO, extra=["pkg/old_test.py", "TESTING_GUIDE_OLD.md"])
    rc, out = _run(root, _REPO)
    assert rc == 0 and "removed pkg/old_test.py" in out and "removed TESTING_GUIDE_OLD.md" in out
    assert not os.path.exists(os.path.join(root, "pkg/old_test.py")) and not os.path.exists(os.path.join(root, "TESTING_GUIDE_OLD.md"))
    assert all(os.path.exists(os.path.join(root, f)) for f in _REPO)


def test_no_directory_is_ever_replaced_so_container_mounts_stay_valid():
    """The incident: rm -rf + copy gave grafana/provisioning a new inode and orphaned Grafana's bind mount."""
    root = _tree(_REPO, extra=["grafana/provisioning/dashboards/removed.json"])
    before = {d: os.stat(os.path.join(root, d)).st_ino for d in ("grafana", "grafana/provisioning", "grafana/provisioning/dashboards")}
    rc, _ = _run(root, _REPO)
    after = {d: os.stat(os.path.join(root, d)).st_ino for d in before}
    assert rc == 0 and before == after, "a directory inode changed"
    assert not os.path.exists(os.path.join(root, "grafana/provisioning/dashboards/removed.json"))


def test_server_owned_files_are_never_touched():
    owned = [".env", ".venv/bin/python", ".fingerprints/a.json", "backups/pingdin_1.sql.gz", "backups/backup.log", "ops-check.log",
             "core_service/constraints.txt", "edge_service/constraints.txt", "pkg/__pycache__/x.pyc", ".git/HEAD",
             ".pytest_cache/v", "nested/deeper/something.log"]
    root = _tree(_REPO, extra=owned)
    rc, out = _run(root, _REPO)
    assert rc == 0 and "nothing to remove" in out, out
    assert all(os.path.exists(os.path.join(root, f)) for f in owned), [f for f in owned if not os.path.exists(os.path.join(root, f))]


def test_the_version_locks_survive_even_though_the_laptop_may_not_have_them():
    """The other half of the incident: a delete-and-copy removed core_service/constraints.txt (server-generated)."""
    root = _tree(_REPO, extra=["compute_service/constraints.txt", "core_service/constraints.txt"])
    _run(root, _REPO)
    assert os.path.exists(os.path.join(root, "compute_service/constraints.txt")) and os.path.exists(os.path.join(root, "core_service/constraints.txt"))


def test_empty_directories_the_repo_no_longer_has_are_removed_but_repo_directories_never():
    root = _tree(_REPO, extra=["gone/sub/leftover.txt"])
    os.makedirs(os.path.join(root, "pkg", "emptied_but_in_repo_elsewhere"), exist_ok=True)
    rc, out = _run(root, _REPO)
    assert rc == 0
    assert not os.path.exists(os.path.join(root, "gone")), "the whole emptied tree should go"
    assert "removed empty directory gone/sub/" in out and "removed empty directory gone/" in out
    assert os.path.isdir(os.path.join(root, "pkg")) and os.path.isdir(os.path.join(root, "grafana/provisioning/dashboards"))


def test_a_directory_the_repo_has_survives_even_if_it_is_momentarily_empty_on_the_server():
    """e.g. after a push that failed part-way: the directory (and any container mount of it) must not be removed."""
    manifest = _REPO + ["mounted/config/real.yml"]
    root = _tree(_REPO)
    os.makedirs(os.path.join(root, "mounted", "config"))
    ino = os.stat(os.path.join(root, "mounted", "config")).st_ino
    rc, _ = _run(root, manifest)
    assert rc == 0 and os.path.isdir(os.path.join(root, "mounted", "config")) and os.stat(os.path.join(root, "mounted", "config")).st_ino == ino


def test_it_refuses_rather_than_wipe_the_server_when_the_local_folder_is_partial():
    server = _REPO + [f"big/f{i}.py" for i in range(60)]
    root = _tree(server)
    rc, out = _run(root, _REPO)                      # laptop copy is missing the whole big/ directory
    assert rc == 3 and "REFUSING" in out and "nothing was deleted" in out
    assert all(os.path.exists(os.path.join(root, f)) for f in server)
    rc, out = _run(root, _REPO, "--max", "100")      # a deliberate override works
    assert rc == 0 and not os.path.exists(os.path.join(root, "big"))


def test_a_tiny_or_unsafe_manifest_is_rejected():
    root = _tree(_REPO)
    rc, out = _run(root, ["README.md"])
    assert rc == 2 and "refusing to prune against it" in out
    rc, out = _run(root, _REPO + ["../../etc/passwd"])
    assert rc == 2 and "unsafe path" in out
    rc, out = _run(root, _REPO + ["/etc/passwd"])
    assert rc == 2
    assert all(os.path.exists(os.path.join(root, f)) for f in _REPO)


def test_dry_run_deletes_nothing_and_says_what_it_would():
    root = _tree(_REPO, extra=["pkg/old.py", "gone/x.txt"])
    rc, out = _run(root, _REPO, "--dry-run")
    assert rc == 0 and "would remove pkg/old.py" in out
    assert os.path.exists(os.path.join(root, "pkg/old.py")) and os.path.exists(os.path.join(root, "gone/x.txt"))


def test_symlinks_are_never_removed_or_followed():
    root = _tree(_REPO)
    outside = tempfile.mkdtemp()
    open(os.path.join(outside, "precious.txt"), "w").write("keep me")
    os.symlink(outside, os.path.join(root, "link_to_outside"))
    os.symlink(os.path.join(root, "README.md"), os.path.join(root, "file_link"))
    rc, _ = _run(root, _REPO)
    assert rc == 0 and os.path.islink(os.path.join(root, "link_to_outside")) and os.path.islink(os.path.join(root, "file_link"))
    assert os.path.exists(os.path.join(outside, "precious.txt"))


def test_manifest_lines_with_dot_slash_and_crlf_are_understood():
    root = _tree(_REPO, extra=["pkg/old.py"])
    rc, out = _run(root, ["./" + f + "\r" for f in _REPO])
    assert rc == 0 and "removed pkg/old.py" in out and os.path.exists(os.path.join(root, "README.md"))


def test_runs_as_a_script_over_stdin_exactly_as_the_sync_script_calls_it():
    root = _tree(_REPO, extra=["pkg/old.py"])
    script = os.path.join(_ROOT, "scripts", "prune_extras.py")
    r = subprocess.run([sys.executable, script, "--manifest", "-", "--max", "40"], input="\n".join(_REPO) + "\n", capture_output=True, text=True, cwd=root)
    assert r.returncode == 0 and "removed pkg/old.py" in r.stdout, r.stdout + r.stderr


if __name__ == "__main__":
    import traceback

    tests = [o for n, o in list(globals().items()) if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAILED: {t.__name__}")
            traceback.print_exc()
    print(f"{passed} passed, {failed} failed (of {len(tests)})")
