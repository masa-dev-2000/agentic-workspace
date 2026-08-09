#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache",
}
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".ps1",
    ".sh", ".bat", ".cmd", ".sql", ".csv",
}
DEFAULT_ROLES = {
    "source": ["apps", "scripts", "src"],
    "documentation": ["docs"],
    "planning": ["planning"],
    "tests": ["tests"],
    "fixtures": ["test_data", "fixtures"],
    "generated_working": ["output"],
    "deliverables": ["deliverables"],
    "cache_temp": [".pytest_cache", ".tmp", "tmp"],
    "archive": ["output/_archive"],
}


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def valid_git_root(candidate: Path) -> Path | None:
    result = run_git(candidate, "rev-parse", "--show-toplevel")
    if result.returncode:
        return None
    resolved = Path(result.stdout.strip()).resolve()
    return resolved if resolved.exists() else None


def find_git_roots(root: Path, max_depth: int = 4) -> list[Path]:
    found: set[Path] = set()
    direct = valid_git_root(root)
    if direct and (direct == root or direct in root.parents):
        found.add(direct)
    for current, dirs, files in os.walk(root, onerror=lambda _: None):
        here = Path(current)
        depth = len(here.relative_to(root).parts)
        has_git_marker = ".git" in dirs or ".git" in files
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if depth >= max_depth:
            dirs[:] = []
        if has_git_marker:
            candidate = valid_git_root(here)
            if candidate:
                found.add(candidate)
    return sorted(found, key=lambda p: (len(p.parts), p.as_posix().lower()))


def choose_repo_root(root: Path, git_roots: list[Path]) -> Path | None:
    direct = valid_git_root(root)
    if direct == root:
        return root
    descendants = [p for p in git_roots if root == p or root in p.parents]
    return descendants[0] if len(descendants) == 1 else None


def normalize_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix() or "."
    except ValueError:
        return os.path.relpath(path.resolve(), base.resolve()).replace("\\", "/")


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {path}")
    return data


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def violation(code: str, path: str, message: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "path": path, "message": message, "severity": severity}


def fingerprint(item: dict[str, Any]) -> str:
    raw = "|".join(str(item.get(k, "")) for k in ("code", "path", "message"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tracked_files(repo: Path) -> list[Path]:
    result = run_git(repo, "ls-files")
    if result.returncode:
        return []
    return [repo / line for line in result.stdout.splitlines() if line]


def fixed_references(repo: Path, names: Iterable[str]) -> list[dict[str, Any]]:
    needles = {name: (f"{name}/", f"{name}\\") for name in names}
    matches: list[dict[str, Any]] = []
    for path in tracked_files(repo):
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            hit = sorted(name for name, patterns in needles.items() if any(p in line for p in patterns))
            if hit:
                matches.append({
                    "file": normalize_rel(path, repo),
                    "line": number,
                    "deprecated_paths": hit,
                })
    return matches


def inspect_top_level(repo: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(repo.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return entries
    for child in children:
        entries.append({
            "name": child.name,
            "kind": "directory" if child.is_dir() else "file",
            "tracked": run_git(repo, "ls-files", "--error-unmatch", child.name).returncode == 0,
        })
    return entries


def audit(root: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root.resolve()
    git_roots = find_git_roots(root)
    repo = choose_repo_root(root, git_roots)
    violations: list[dict[str, str]] = []
    if repo is None:
        violations.append(violation(
            "GIT_ROOT_AMBIGUOUS", ".", "Workspace does not resolve to exactly one Git repository.", "error"
        ))
        deprecated: dict[str, Any] = {}
        protected: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        dirty = None
        top_level: list[dict[str, Any]] = []
    else:
        deprecated = (manifest or {}).get("deprecated_paths", {})
        if not deprecated and (repo / "outputs").exists() and (repo / "output").exists():
            deprecated = {"outputs": {"replacement": "output", "policy": "migration-pending"}}
        for path_name, settings in sorted(deprecated.items()):
            target = repo / path_name
            if target.exists():
                replacement = settings.get("replacement", "") if isinstance(settings, dict) else ""
                violations.append(violation(
                    "DEPRECATED_PATH_PRESENT", path_name,
                    f"Deprecated path exists; preferred replacement is {replacement or 'not specified'}."
                ))
        protected = []
        for item in (manifest or {}).get("protected_external", []):
            if not isinstance(item, dict) or "path" not in item:
                continue
            target = (repo / item["path"]).resolve()
            protected.append({
                "path": item["path"],
                "access": item.get("access", "read-only"),
                "exists": target.exists(),
                "resolved_outside_repo": repo not in target.parents and target != repo,
            })
            if not target.exists():
                violations.append(violation(
                    "PROTECTED_EXTERNAL_MISSING", item["path"], "Protected external path is missing."
                ))
        refs = fixed_references(repo, deprecated.keys())
        dirty_result = run_git(repo, "status", "--porcelain")
        dirty = bool(dirty_result.stdout.strip()) if dirty_result.returncode == 0 else None
        top_level = inspect_top_level(repo)
        if root != repo:
            violations.append(violation(
                "OUTER_WORKSPACE", normalize_rel(repo, root),
                "Requested root is an outer workspace; use the detected nested Git root for policy."
            ))
    for item in violations:
        item["fingerprint"] = fingerprint(item)
    report = {
        "schema_version": 1,
        "requested_root": root.as_posix(),
        "repo_root": repo.as_posix() if repo else None,
        "git_roots": [p.as_posix() for p in git_roots],
        "root_kind": "git-repository" if repo == root else ("workspace" if repo else "ambiguous-workspace"),
        "dirty_worktree": dirty,
        "top_level_entries": top_level,
        "protected_external": protected,
        "fixed_references": refs,
        "violations": sorted(violations, key=lambda x: (x["code"], x["path"])),
        "write_count": 0,
    }
    report["deterministic_digest"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return report


def proposed_manifest(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    roots = find_git_roots(root.resolve())
    repo = choose_repo_root(root.resolve(), roots)
    if repo is None:
        raise ValueError("root must resolve to exactly one Git repository")
    roles = {role: [p for p in paths if (repo / p).exists()] for role, paths in DEFAULT_ROLES.items()}
    roles = {k: v for k, v in roles.items() if v}
    protected = [{"path": p.replace("\\", "/"), "access": "read-only"} for p in args.protected_external]
    deprecated = {
        name: {"replacement": args.canonical_generated, "policy": "no-new-writes"}
        for name in args.deprecated
    }
    return {
        "version": 1,
        "repo_root": ".",
        "workspace_root": normalize_rel(root.resolve(), repo),
        "roles": roles,
        "protected_external": protected,
        "deprecated_paths": deprecated,
        "constraints": {
            "approval_required_for_moves": True,
            "prohibit_automatic_delete": True,
            "new_violation_policy": "error",
        },
    }


def load_baseline(path: Path | None) -> set[str]:
    if path is None:
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("fingerprints", [])
    return {str(value) for value in values}


def check(root: Path, manifest_path: Path, baseline_path: Path | None) -> tuple[dict[str, Any], int]:
    manifest = load_yaml(manifest_path)
    report = audit(root, manifest)
    baseline = load_baseline(baseline_path)
    new_items = [v for v in report["violations"] if v["fingerprint"] not in baseline]
    errors = [v for v in new_items if v["severity"] == "error"]
    if manifest.get("constraints", {}).get("new_violation_policy") == "error":
        errors.extend(new_items)
    result = {
        "schema_version": 1,
        "valid": not errors,
        "baseline_count": len(baseline),
        "violation_count": len(report["violations"]),
        "new_violation_count": len(new_items),
        "error_count": len({v["fingerprint"] for v in errors}),
        "new_violations": new_items,
        "audit_digest": report["deterministic_digest"],
        "write_count": 0,
    }
    return result, 0 if result["valid"] else 2


def migration_plan(audit_data: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        "# Repository layout migration plan",
        "",
        "## Safety boundary",
        "",
        "- No file is moved or deleted by this plan.",
        "- Obtain explicit approval before each migration batch.",
        "- Preserve hashes and verify fixed references before and after each batch.",
        "- Treat protected external paths and dirty worktree changes as out of scope.",
        "",
        "## Detected repository",
        "",
        f"- Repository: `{audit_data.get('repo_root')}`",
        f"- Requested root kind: `{audit_data.get('root_kind')}`",
        f"- Dirty worktree: `{audit_data.get('dirty_worktree')}`",
        "",
        "## Deprecated paths",
        "",
    ]
    deprecated = manifest.get("deprecated_paths", {})
    if not deprecated:
        lines.append("- None.")
    for name, settings in sorted(deprecated.items()):
        replacement = settings.get("replacement", "unspecified")
        count = sum(name in ref.get("deprecated_paths", []) for ref in audit_data.get("fixed_references", []))
        lines.append(f"- `{name}/` → `{replacement}/`: {count} tracked fixed-reference locations.")
    lines.extend(["", "## Proposed batches", ""])
    for name, settings in sorted(deprecated.items()):
        replacement = settings.get("replacement", "unspecified")
        lines.extend([
            f"1. Freeze new writes to `{name}/`.",
            f"2. Classify every artifact under `{name}/` as current, reproducible, supporting, or uncertain.",
            f"3. Update and test tracked references from `{name}/` to `{replacement}/`.",
            "4. Copy one approved batch, verify file count, bytes, and tree digest, then switch references.",
            "5. Retain the old batch until rollback is no longer required; do not delete automatically.",
        ])
        break
    if not deprecated:
        lines.append("1. No migration batch is currently required.")
    lines.extend(["", "## Protected external paths", ""])
    protected = manifest.get("protected_external", [])
    if protected:
        for item in protected:
            lines.append(f"- `{item.get('path')}`: `{item.get('access', 'read-only')}`; never auto-move.")
    else:
        lines.append("- None declared.")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and govern repository layout safely.")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--root", type=Path, required=True)
    scan.add_argument("--manifest", type=Path)
    scan.add_argument("--output", type=Path)
    init = sub.add_parser("init")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--canonical-generated", default="output")
    init.add_argument("--deprecated", action="append", default=[])
    init.add_argument("--protected-external", action="append", default=[])
    baseline = sub.add_parser("baseline")
    baseline.add_argument("--audit", type=Path, required=True)
    baseline.add_argument("--output", type=Path, required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--root", type=Path, required=True)
    check_parser.add_argument("--manifest", type=Path, required=True)
    check_parser.add_argument("--baseline", type=Path)
    check_parser.add_argument("--output", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("--audit", type=Path, required=True)
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "scan":
        manifest = load_yaml(args.manifest) if args.manifest else None
        data = audit(args.root, manifest)
        if args.output:
            write_json(args.output, data)
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "init":
        data = proposed_manifest(args.root, args)
        write_text(args.output, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        return 0
    if args.command == "baseline":
        data = json.loads(args.audit.read_text(encoding="utf-8"))
        result = {
            "schema_version": 1,
            "audit_digest": data.get("deterministic_digest"),
            "fingerprints": sorted(v["fingerprint"] for v in data.get("violations", [])),
        }
        write_json(args.output, result)
        return 0
    if args.command == "check":
        data, status = check(args.root, args.manifest, args.baseline)
        if args.output:
            write_json(args.output, data)
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return status
    if args.command == "plan":
        data = json.loads(args.audit.read_text(encoding="utf-8"))
        manifest = load_yaml(args.manifest)
        write_text(args.output, migration_plan(data, manifest))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
