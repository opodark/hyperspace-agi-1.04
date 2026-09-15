"""Offline code workspace runner.

The runner has no network and no Docker socket. It consumes JSON jobs from a
shared volume, works only in disposable copies of the sanitized image seed,
and never writes back to the source checkout.
"""
from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path


SEED = Path(os.getenv("SANDBOX_SEED_DIR", "/seed"))
EXCHANGE = Path(os.getenv("SANDBOX_EXCHANGE_DIR", "/exchange"))
WORKSPACES = Path(os.getenv("SANDBOX_WORKSPACE_DIR", "/workspaces"))
MAX_WORKSPACES = max(1, int(os.getenv("SANDBOX_MAX_WORKSPACES", "6")))
MAX_FILE_BYTES = max(1024, int(os.getenv("SANDBOX_MAX_FILE_BYTES", "1048576")))
MAX_OUTPUT_BYTES = max(4096, int(os.getenv("SANDBOX_MAX_OUTPUT_BYTES", "131072")))
MAX_DIFF_BYTES = max(4096, int(os.getenv("SANDBOX_MAX_DIFF_BYTES", "262144")))
MAX_TIMEOUT = max(1, int(os.getenv("SANDBOX_MAX_TIMEOUT", "120")))
WORKSPACE_RE = re.compile(r"^[a-f0-9-]{8,64}$")
ALLOWED_EXECUTABLES = {
    item.strip() for item in os.getenv(
        "SANDBOX_ALLOWED_EXECUTABLES",
        "python,python3,node,npm,npx,pytest,unittest,git",
    ).split(",") if item.strip()
}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _audit(job: dict, result: dict) -> None:
    record = {
        "ts": time.time(),
        "job_id": job.get("job_id"),
        "action": job.get("action"),
        "workspace_id": job.get("workspace_id"),
        "ok": result.get("ok", False),
    }
    with (EXCHANGE / "audit.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _workspace(workspace_id: str) -> Path:
    if not WORKSPACE_RE.fullmatch(str(workspace_id or "")):
        raise ValueError("invalid workspace_id")
    root = (WORKSPACES / workspace_id).resolve()
    if not root.is_dir():
        raise FileNotFoundError("workspace not found")
    return root


def _safe_path(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    relative = str(relative or ".")
    if len(relative) > 500 or Path(relative).is_absolute():
        raise ValueError("invalid relative path")
    # Resolve without strict mode first, so containment is checked before the
    # host filesystem can leak whether an escaped path exists.
    target = (root / relative).resolve(strict=False)
    if target != root and root not in target.parents:
        raise ValueError("path escapes workspace")
    if must_exist and not target.exists():
        raise FileNotFoundError("path not found")
    return target


def _ignore(_directory, names):
    ignored = set()
    for name in names:
        if name in {".git", ".env", ".worktrees", "data", "node_modules", "__pycache__"}:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _create(job: dict) -> dict:
    existing = [item for item in WORKSPACES.iterdir() if item.is_dir()]
    if len(existing) >= MAX_WORKSPACES:
        raise RuntimeError("workspace limit reached; discard an old workspace")
    workspace_id = str(uuid.uuid4())
    root = WORKSPACES / workspace_id
    base = root / "base"
    repo = root / "repo"
    shutil.copytree(SEED, base, ignore=_ignore, symlinks=True)
    shutil.copytree(base, repo, symlinks=True)
    metadata = {
        "workspace_id": workspace_id,
        "label": str(job.get("label", ""))[:120],
        "created_at": time.time(),
    }
    _atomic_json(root / "metadata.json", metadata)
    return {"ok": True, **metadata}


def _list(job: dict) -> dict:
    repo = _workspace(job.get("workspace_id")) / "repo"
    pattern = str(job.get("pattern") or "*")[:200]
    limit = max(1, min(int(job.get("limit", 200)), 1000))
    files = []
    for path in repo.rglob("*"):
        if path.is_file():
            relative = path.relative_to(repo).as_posix()
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
                files.append(relative)
                if len(files) >= limit:
                    break
    return {"ok": True, "files": files, "truncated": len(files) >= limit}


def _read(job: dict) -> dict:
    repo = _workspace(job.get("workspace_id")) / "repo"
    path = _safe_path(repo, job.get("path"), must_exist=True)
    if not path.is_file():
        raise ValueError("path is not a file")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file exceeds read limit")
    return {"ok": True, "path": path.relative_to(repo).as_posix(),
            "content": path.read_text(encoding="utf-8", errors="replace")}


def _write(job: dict) -> dict:
    repo = _workspace(job.get("workspace_id")) / "repo"
    path = _safe_path(repo, job.get("path"))
    content = str(job.get("content", ""))
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError("content exceeds write limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ValueError("refusing to write through symlink")
    temporary = path.with_suffix(path.suffix + ".sandbox-tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return {"ok": True, "path": path.relative_to(repo).as_posix(), "bytes": len(encoded)}


def _replace(job: dict) -> dict:
    repo = _workspace(job.get("workspace_id")) / "repo"
    path = _safe_path(repo, job.get("path"), must_exist=True)
    old = str(job.get("old", ""))
    new = str(job.get("new", ""))
    if not old:
        raise ValueError("old text is required")
    text = path.read_text(encoding="utf-8")
    occurrences = text.count(old)
    expected = int(job.get("expected_occurrences", 1))
    if occurrences != expected:
        raise ValueError(f"expected {expected} occurrence(s), found {occurrences}")
    job = dict(job, content=text.replace(old, new, expected))
    return {**_write(job), "replacements": expected}


def _run(job: dict) -> dict:
    root = _workspace(job.get("workspace_id"))
    repo = root / "repo"
    argv = job.get("argv")
    if not isinstance(argv, list) or not argv or len(argv) > 32:
        raise ValueError("argv must be a non-empty list of at most 32 items")
    argv = [str(item)[:1000] for item in argv]
    executable = Path(argv[0]).name
    if argv[0] != executable or executable not in ALLOWED_EXECUTABLES:
        raise ValueError(f"executable not allowed: {argv[0]}")
    cwd = _safe_path(repo, job.get("cwd", "."), must_exist=True)
    if not cwd.is_dir():
        raise ValueError("cwd is not a directory")
    timeout = max(1, min(int(job.get("timeout", 30)), MAX_TIMEOUT))
    env = {
        "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(root),
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "npm_config_cache": "/tmp/npm-cache",
        "NO_COLOR": "1",
    }
    started = time.monotonic()
    try:
        process = subprocess.run(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
        output = process.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return {
            "ok": process.returncode == 0,
            "exit_code": process.returncode,
            "output": output,
            "truncated": len(process.stdout) > MAX_OUTPUT_BYTES,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return {"ok": False, "error": "command timed out", "output": output,
                "duration_ms": round((time.monotonic() - started) * 1000)}


def _file_map(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()}


def _diff(job: dict) -> dict:
    root = _workspace(job.get("workspace_id"))
    base, repo = root / "base", root / "repo"
    base_files, repo_files = _file_map(base), _file_map(repo)
    chunks = []
    changed = []
    for relative in sorted(set(base_files) | set(repo_files)):
        before_path, after_path = base_files.get(relative), repo_files.get(relative)
        before = before_path.read_bytes() if before_path else b""
        after = after_path.read_bytes() if after_path else b""
        if before == after:
            continue
        changed.append(relative)
        try:
            before_lines = before.decode("utf-8").splitlines(keepends=True)
            after_lines = after.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            chunks.append(f"Binary files differ: {relative}\n")
            continue
        chunks.extend(difflib.unified_diff(
            before_lines, after_lines,
            fromfile=f"a/{relative}", tofile=f"b/{relative}", n=3,
        ))
        if sum(len(item.encode("utf-8")) for item in chunks) >= MAX_DIFF_BYTES:
            break
    text = "".join(chunks)
    encoded = text.encode("utf-8")
    return {"ok": True, "changed_files": changed, "diff": encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore"),
            "truncated": len(encoded) > MAX_DIFF_BYTES}


def _discard(job: dict) -> dict:
    root = _workspace(job.get("workspace_id"))
    shutil.rmtree(root)
    return {"ok": True, "discarded": job.get("workspace_id")}


def _status(_job: dict) -> dict:
    workspaces = []
    for root in WORKSPACES.iterdir():
        metadata = root / "metadata.json"
        if root.is_dir() and metadata.exists():
            try:
                workspaces.append(json.loads(metadata.read_text(encoding="utf-8")))
            except ValueError:
                pass
    return {"ok": True, "network": "disabled", "docker_socket": False,
            "workspace_count": len(workspaces), "workspace_limit": MAX_WORKSPACES,
            "allowed_executables": sorted(ALLOWED_EXECUTABLES), "workspaces": workspaces}


ACTIONS = {
    "create": _create,
    "list": _list,
    "read": _read,
    "write": _write,
    "replace": _replace,
    "run": _run,
    "diff": _diff,
    "discard": _discard,
    "status": _status,
}


def _process(path: Path) -> None:
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        action = str(job.get("action", ""))
        handler = ACTIONS.get(action)
        if not handler:
            raise ValueError(f"unknown action: {action}")
        result = handler(job)
    except Exception as error:
        result = {"ok": False, "error": str(error) or type(error).__name__}
        try:
            job
        except NameError:
            job = {"job_id": path.stem, "action": "invalid"}
    result["job_id"] = job.get("job_id", path.stem)
    _atomic_json(EXCHANGE / "results" / f"{result['job_id']}.json", result)
    _audit(job, result)
    path.unlink(missing_ok=True)


def main() -> None:
    jobs = EXCHANGE / "jobs"
    results = EXCHANGE / "results"
    jobs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    while True:
        (EXCHANGE / "heartbeat").write_text(str(time.time()), encoding="ascii")
        for path in sorted(jobs.glob("*.json")):
            _process(path)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
