from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

AGENTS_START = "<!-- setup-codex-review:agents:start -->"
AGENTS_END = "<!-- setup-codex-review:agents:end -->"
PR_START = "<!-- setup-codex-review:pr:start -->"
PR_END = "<!-- setup-codex-review:pr:end -->"

MAX_COMMANDS = 8
MAX_RULES = 6


class SetupError(RuntimeError):
    pass


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise SetupError(f"git {' '.join(args)} failed: {detail}")
    return proc


def resolve_repo(path: Path) -> Path:
    proc = _run_git(path.resolve(), "rev-parse", "--show-toplevel")
    return Path(proc.stdout.strip()).resolve()


def _default_branch(repo: Path) -> str:
    proc = _run_git(
        repo,
        "symbolic-ref",
        "--quiet",
        "--short",
        "refs/remotes/origin/HEAD",
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip().startswith("origin/"):
        return proc.stdout.strip().split("/", 1)[1]
    for candidate in ("main", "master"):
        proc = _run_git(
            repo,
            "show-ref",
            "--verify",
            f"refs/heads/{candidate}",
            check=False,
        )
        if proc.returncode == 0:
            return candidate
    proc = _run_git(repo, "branch", "--show-current", check=False)
    return proc.stdout.strip() or "main"


def _remote_url(repo: Path) -> str | None:
    proc = _run_git(repo, "remote", "get-url", "origin", check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _workflow_commands(repo: Path) -> list[str]:
    commands: list[str] = []
    workflow_dir = repo / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return commands
    for path in sorted(workflow_dir.glob("*.y*ml")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in re.finditer(r"(?m)^\s*run:\s*([^\n|>][^\n]*)$", text):
            command = match.group(1).strip().strip("'\"")
            lowered = command.casefold()
            if any(
                token in lowered
                for token in ("test", "lint", "typecheck", "check", "validate", "build")
            ):
                if "${{" not in command and "\n" not in command:
                    commands.append(command)
    return commands


def _package_commands(repo: Path) -> list[str]:
    commands: list[str] = []
    package_json = repo / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        if isinstance(scripts, dict):
            if (repo / "pnpm-lock.yaml").exists():
                runner = "pnpm"
            elif (repo / "yarn.lock").exists():
                runner = "yarn"
            elif (repo / "bun.lock").exists() or (repo / "bun.lockb").exists():
                runner = "bun run"
            else:
                runner = "npm run"
            for name in ("lint", "typecheck", "test", "build"):
                value = scripts.get(name)
                if not isinstance(value, str):
                    continue
                if "no test specified" in value.casefold():
                    continue
                commands.append(f"{runner} {name}")

    makefile = next(
        (path for path in (repo / "Makefile", repo / "makefile") if path.is_file()),
        None,
    )
    if makefile:
        text = makefile.read_text(encoding="utf-8", errors="replace")
        for target in ("check", "lint", "test", "build"):
            if re.search(rf"(?m)^{re.escape(target)}\s*:", text):
                commands.append(f"make {target}")

    if (repo / "Cargo.toml").is_file():
        commands.append("cargo test")
    if (repo / "go.mod").is_file():
        commands.append("go test ./...")
    if (repo / "mvnw").is_file():
        commands.append("./mvnw test")
    elif (repo / "pom.xml").is_file():
        commands.append("mvn test")
    if (repo / "gradlew").is_file():
        commands.append("./gradlew test")
    if any(repo.glob("*.sln")) or any(repo.glob("*.csproj")):
        commands.append("dotnet test")

    pyproject = repo / "pyproject.toml"
    if pyproject.is_file() or (repo / "pytest.ini").is_file() or (repo / "tests").is_dir():
        workflow_text = "\n".join(_workflow_commands(repo)).casefold()
        if "pytest" in workflow_text or (repo / "pytest.ini").is_file():
            commands.append("python -m pytest")
        elif (repo / "tests").is_dir() and any((repo / "tests").glob("test_*.py")):
            commands.append("python -m unittest discover -s tests -v")

    return commands


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _find_pr_template(repo: Path) -> Path | None:
    candidates = [
        repo / ".github" / "pull_request_template.md",
        repo / ".github" / "PULL_REQUEST_TEMPLATE.md",
        repo / "pull_request_template.md",
        repo / "PULL_REQUEST_TEMPLATE.md",
    ]
    existing = [path for path in candidates if path.is_file()]
    if len(existing) > 1:
        joined = ", ".join(str(path.relative_to(repo)) for path in existing)
        raise SetupError(f"multiple pull-request templates found: {joined}")
    return existing[0] if existing else None


def scan(repo_path: Path) -> dict[str, Any]:
    repo = resolve_repo(repo_path)
    agents = repo / "AGENTS.md"
    template = _find_pr_template(repo)
    agents_text = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    template_text = template.read_text(encoding="utf-8") if template else ""
    status = _run_git(repo, "status", "--porcelain", check=False).stdout.strip()
    commands = _dedupe(_workflow_commands(repo) + _package_commands(repo))[:MAX_COMMANDS]
    return {
        "repo_root": str(repo),
        "remote_url": _remote_url(repo),
        "default_branch": _default_branch(repo),
        "clean_worktree": not bool(status),
        "agents_file": "AGENTS.md" if agents.is_file() else None,
        "pull_request_template": str(template.relative_to(repo)) if template else None,
        "managed_agents_block": AGENTS_START in agents_text and AGENTS_END in agents_text,
        "managed_pr_block": PR_START in template_text and PR_END in template_text,
        "unmanaged_code_review_rules": (
            "## Code Review Rules" in agents_text and AGENTS_START not in agents_text
        ),
        "candidate_validation_commands": commands,
        "manifest_files": [
            path.name
            for path in (
                repo / "package.json",
                repo / "pyproject.toml",
                repo / "Cargo.toml",
                repo / "go.mod",
                repo / "pom.xml",
            )
            if path.is_file()
        ],
        "workflow_files": [
            str(path.relative_to(repo))
            for path in sorted((repo / ".github" / "workflows").glob("*.y*ml"))
        ]
        if (repo / ".github" / "workflows").is_dir()
        else [],
    }


def _validate_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SetupError("config root must be an object")
    commands = data.get("validation_commands")
    if not isinstance(commands, list) or not commands:
        raise SetupError("validation_commands must be a non-empty list")
    if len(commands) > MAX_COMMANDS:
        raise SetupError(
            f"validation_commands may contain at most {MAX_COMMANDS} entries"
        )
    clean_commands: list[str] = []
    for command in commands:
        if not isinstance(command, str) or not command.strip() or "\n" in command:
            raise SetupError("each validation command must be one non-empty line")
        clean_commands.append(command.strip())

    rules = data.get("review_rules")
    if not isinstance(rules, list) or not 2 <= len(rules) <= MAX_RULES:
        raise SetupError(
            f"review_rules must contain between 2 and {MAX_RULES} entries"
        )
    clean_rules: list[dict[str, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise SetupError("each review rule must be an object")
        title = rule.get("title")
        text = rule.get("text")
        if (
            not isinstance(title, str)
            or not title.strip()
            or "\n" in title
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise SetupError("review rules require a one-line title and non-empty text")
        if any(
            marker in text for marker in (AGENTS_START, AGENTS_END, PR_START, PR_END)
        ):
            raise SetupError("review rule contains a reserved marker")
        clean_rules.append(
            {"title": title.strip(), "text": " ".join(text.split())}
        )

    return {
        "validation_commands": _dedupe(clean_commands),
        "review_rules": clean_rules,
    }


def _render_agents_block(config: dict[str, Any]) -> str:
    commands = "\n".join(
        f"- `{command}`" for command in config["validation_commands"]
    )
    rules = "\n\n".join(
        f"### {rule['title']}\n\n- {rule['text']}" for rule in config["review_rules"]
    )
    return (
        f"{AGENTS_START}\n"
        "## Codex review workflow\n\n"
        "Before opening or updating a non-trivial pull request, run:\n\n"
        f"{commands}\n\n"
        "Use a separate, read-only review pass before merge. GitHub Codex Code Review "
        "supplements deterministic CI; it does not replace tests or the owner's final "
        "decision. Never auto-merge because a review passed.\n\n"
        "## Code Review Rules\n\n"
        f"{rules}\n"
        f"{AGENTS_END}"
    )


def _render_pr_block(config: dict[str, Any]) -> str:
    commands = "\n".join(
        f"- [ ] `{command}`" for command in config["validation_commands"]
    )
    return (
        f"{PR_START}\n"
        "## Verification\n\n"
        f"{commands}\n"
        "- [ ] Change-specific verification was executed\n\n"
        "## Independent review\n\n"
        "- [ ] A separate read-only Codex review was run against the merge diff\n"
        "- [ ] GitHub Codex Code Review was triggered with `@codex review`\n"
        "- [ ] Every P0/P1 finding was fixed or explicitly accepted by the owner\n\n"
        "## Residual risk and rollback\n\n"
        "<!-- State remaining uncertainty and the concrete rollback path. -->\n"
        f"{PR_END}"
    )


def _replace_managed(text: str, start: str, end: str, block: str) -> str:
    if (start in text) != (end in text):
        raise SetupError(f"incomplete managed marker pair: {start}")
    if start in text:
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
        return pattern.sub(block, text, count=1)
    base = text.rstrip()
    return f"{base}\n\n{block}\n" if base else f"{block}\n"


def _expected_files(repo: Path, config: dict[str, Any]) -> dict[Path, str]:
    agents_path = repo / "AGENTS.md"
    existing_agents = (
        agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
    )
    if "## Code Review Rules" in existing_agents and AGENTS_START not in existing_agents:
        raise SetupError(
            "AGENTS.md already has an unmanaged '## Code Review Rules' section; "
            "merge it deliberately instead of creating a duplicate"
        )
    agents_text = _replace_managed(
        existing_agents,
        AGENTS_START,
        AGENTS_END,
        _render_agents_block(config),
    )

    template_path = _find_pr_template(repo) or (
        repo / ".github" / "pull_request_template.md"
    )
    existing_template = (
        template_path.read_text(encoding="utf-8") if template_path.is_file() else ""
    )
    template_text = _replace_managed(
        existing_template,
        PR_START,
        PR_END,
        _render_pr_block(config),
    )
    return {agents_path: agents_text, template_path: template_text}


def _diff(path: Path, before: str, after: str, repo: Path) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path.relative_to(repo)}",
            tofile=f"b/{path.relative_to(repo)}",
        )
    )


def apply(repo_path: Path, config_path: Path, write: bool) -> dict[str, Any]:
    repo = resolve_repo(repo_path)
    config = _validate_config(json.loads(config_path.read_text(encoding="utf-8")))
    expected = _expected_files(repo, config)
    changed: list[str] = []
    diffs: list[str] = []
    for path, content in expected.items():
        before = path.read_text(encoding="utf-8") if path.is_file() else ""
        if before == content:
            continue
        changed.append(str(path.relative_to(repo)))
        diffs.append(_diff(path, before, content, repo))
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return {
        "repo_root": str(repo),
        "changed": changed,
        "written": write,
        "diff": "\n".join(diffs),
    }


def check(repo_path: Path, config_path: Path) -> dict[str, Any]:
    result = apply(repo_path, config_path, write=False)
    result["valid"] = not bool(result["changed"])
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and idempotently scaffold a repository for native Codex review"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--repo", type=Path, default=Path.cwd())

    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--repo", type=Path, default=Path.cwd())
    apply_parser.add_argument("--config", type=Path, required=True)
    apply_parser.add_argument("--write", action="store_true")

    check_parser = sub.add_parser("check")
    check_parser.add_argument("--repo", type=Path, default=Path.cwd())
    check_parser.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            result = scan(args.repo)
            code = 0
        elif args.command == "apply":
            result = apply(args.repo, args.config, args.write)
            code = 0
        else:
            result = check(args.repo, args.config)
            code = 0 if result["valid"] else 1
    except (SetupError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
