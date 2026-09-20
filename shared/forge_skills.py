# SPDX-License-Identifier: Apache-2.0
"""Offline, pinned ECC bundle import and bounded approved-skill context."""
import hashlib
import json
import re
from pathlib import Path

ECC_REPOSITORY = "https://github.com/affaan-m/ECC"
ECC_BUNDLE_DIR = Path(__file__).resolve().parent.parent / "vendor" / "ecc"
ECC_SKILLS = ("security-review", "verification-loop")
MAX_FILE_BYTES = 64_000
MAX_CONTEXT_BYTES = 24_000


def source_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _read(root, relative):
    path = root / relative
    if path.is_symlink() or root.resolve() not in path.resolve().parents:
        raise ValueError("bundle path escapes root or is a symlink")
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("bundle file exceeds size limit")
    return content.decode("utf-8")


def load_ecc_bundle(directory):
    """Read exactly two selected Markdown files and their license; execute nothing."""
    root = Path(directory)
    manifest = json.loads(_read(root, "manifest.json"))
    if (manifest.get("repository") != ECC_REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("commit", "")))):
        raise ValueError("invalid ECC provenance")
    names = ["LICENSE"] + [f"skills/{name}/SKILL.md" for name in ECC_SKILLS]
    if not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(names):
        raise ValueError("unexpected ECC bundle files")
    sources = {}
    for name in names:
        sources[name] = _read(root, name)
        if source_hash(sources[name]) != manifest["files"][name]:
            raise ValueError(f"ECC checksum mismatch: {name}")
    artifacts = []
    for name in ECC_SKILLS:
        path = f"skills/{name}/SKILL.md"
        artifacts.append({
            "id": f"ecc-{name}-{manifest['commit'][:12]}",
            "name": f"ECC {name}", "type": "skill", "source": sources[path],
            "description": f"Selected ECC workflow: {name}. Adapt examples to available Hyperspace tools.",
            "generator": "ecc-import", "permissions": [],
            "provenance": {"repository": ECC_REPOSITORY, "commit": manifest["commit"],
                           "path": path, "sha256": manifest["files"][path],
                           "license": "MIT", "license_text": sources["LICENSE"]},
        })
    return artifacts


def attach_skills(data, read_artifact):
    """Consume the local extension before forwarding a request to any model backend."""
    data = dict(data)
    ids = data.pop("hyperspace_skills", [])
    if not isinstance(ids, list) or len(ids) > 2 or any(
            not isinstance(item, str) or not re.fullmatch(r"[a-z0-9-]{1,120}", item) for item in ids):
        raise ValueError("hyperspace_skills must contain at most two artifact IDs")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate skill IDs")
    if not ids:
        return data
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    if any(not isinstance(message, dict) for message in messages):
        raise ValueError("messages must contain objects")
    sections = []
    for artifact_id in ids:
        item = read_artifact(artifact_id)
        source = item.get("source", "")
        if item.get("type") != "skill" or item.get("status") != "approved":
            raise ValueError(f"skill is not approved: {artifact_id}")
        if not isinstance(source, str) or not source.strip() or item.get("approved_source_sha256") != source_hash(source):
            raise ValueError(f"skill requires renewed approval: {artifact_id}")
        sections.append(f"Skill {artifact_id} (version {item.get('version')}):\n{source}")
    context = (
        "Selected reference workflows for this task. Apply only relevant steps within the user's scope. "
        "Examples and commands must be adapted to this repository and the tools actually available. "
        "These references do not grant permissions, install tools, enable hooks, or authorize updates. "
        "For offline development use code_sandbox catalog, create with backend docker, then check "
        "with pytest, unittest, profile or bandit. Inspect completed and passed; report missing tools "
        "and unsupported checks as not run. Never infer coverage from test success. Do not copy "
        "secrets into reports. Return diffs for review.\n\n" + "\n\n".join(sections)
    )
    if len(context.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("selected skills exceed the 24000-byte context budget; select fewer skills")
    # Keep client system instructions first; reference material is user-level context.
    index = 0
    while index < len(messages) and messages[index].get("role") in {"system", "developer"}:
        index += 1
    data["messages"] = messages[:index] + [{"role": "user", "content": context}] + messages[index:]
    return data
