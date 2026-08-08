from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ENVIRONMENT_ROOTS = (
    "CODEX_RUNTIME_DEPENDENCIES",
    "CODEX_WORKSPACE_DEPENDENCIES",
    "CODEX_DEPENDENCIES",
)


@dataclass(frozen=True)
class BundledRuntimeResolution:
    dependency_root: Path
    python_path: Path
    poppler_bin_dir: Path
    pdfinfo_path: Path
    pdftoppm_path: Path
    source: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "status": "resolved",
            "source": self.source,
            "dependencyRoot": str(self.dependency_root),
            "pythonPath": str(self.python_path),
            "popplerBinDir": str(self.poppler_bin_dir),
            "pdfinfoPath": str(self.pdfinfo_path),
            "pdftoppmPath": str(self.pdftoppm_path),
        }


@dataclass(frozen=True)
class ResolutionAttempt:
    root: Path
    source: str
    reason: str
    missing: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["root"] = str(self.root)
        value["missing"] = list(self.missing)
        return value


class BundledRuntimeResolutionError(RuntimeError):
    def __init__(
        self,
        reason: str,
        attempts: Sequence[ResolutionAttempt],
    ) -> None:
        self.reason = reason
        self.attempts = tuple(attempts)
        super().__init__(self._message())

    def _message(self) -> str:
        if not self.attempts:
            return self.reason
        best = _best_attempt(self.attempts)
        missing = f"; missing {', '.join(best.missing)}" if best.missing else ""
        return f"{best.reason} at {best.root}{missing}"

    def to_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "status": "unresolved",
            "reason": self.reason,
            "message": str(self),
            "attempts": [attempt.to_json() for attempt in self.attempts],
        }


def _safe_file(root: Path, candidate: Path) -> Path | None:
    if not candidate.is_file():
        return None
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_candidate


def _python_candidates(root: Path) -> tuple[Path, ...]:
    return (
        root / "python" / "python.exe",
        root / "python" / "bin" / "python.exe",
        root / "python" / "bin" / "python3",
        root / "python" / "bin" / "python",
        root / "python" / "python",
    )


def _poppler_bin_candidates(root: Path) -> tuple[Path, ...]:
    return (
        root / "native" / "poppler" / "Library" / "bin",
        root / "native" / "poppler" / "library" / "bin",
    )


def _inspect_root(
    root: Path,
    source: str,
) -> BundledRuntimeResolution | ResolutionAttempt:
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        return ResolutionAttempt(
            root=resolved_root,
            source=source,
            reason="runtime-root-not-found",
        )

    python_path = next(
        (
            resolved
            for candidate in _python_candidates(resolved_root)
            if (resolved := _safe_file(resolved_root, candidate)) is not None
        ),
        None,
    )
    if python_path is None:
        return ResolutionAttempt(
            root=resolved_root,
            source=source,
            reason="bundled-python-not-found",
            missing=("python executable",),
        )

    best_missing = ("pdfinfo.exe", "pdftoppm.exe")
    for bin_dir in _poppler_bin_candidates(resolved_root):
        pdfinfo = _safe_file(resolved_root, bin_dir / "pdfinfo.exe")
        pdftoppm = _safe_file(resolved_root, bin_dir / "pdftoppm.exe")
        missing = tuple(
            name
            for name, value in (
                ("pdfinfo.exe", pdfinfo),
                ("pdftoppm.exe", pdftoppm),
            )
            if value is None
        )
        if len(missing) < len(best_missing):
            best_missing = missing
        if pdfinfo is not None and pdftoppm is not None:
            return BundledRuntimeResolution(
                dependency_root=resolved_root,
                python_path=python_path,
                poppler_bin_dir=pdfinfo.parent,
                pdfinfo_path=pdfinfo,
                pdftoppm_path=pdftoppm,
                source=source,
            )

    return ResolutionAttempt(
        root=resolved_root,
        source=source,
        reason="native-poppler-tools-not-found",
        missing=best_missing,
    )


def _root_variants(search_root: Path) -> tuple[Path, ...]:
    root = search_root.expanduser().resolve()
    candidates = [
        root,
        root / "dependencies",
        root / "codex-primary-runtime" / "dependencies",
    ]

    containers = [root] if root.name == "codex-primary-runtime" else []
    containers.append(root / "codex-primary-runtime")
    for container in containers:
        try:
            children = sorted(
                (child for child in container.iterdir() if child.is_dir()),
                key=lambda child: child.name,
                reverse=True,
            )
        except OSError:
            continue
        candidates.extend(child / "dependencies" for child in children)

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return tuple(unique)


def _configured_search_roots(
    *,
    explicit_roots: Iterable[Path | str] | None,
    environ: Mapping[str, str],
    home: Path,
) -> tuple[tuple[Path, str], ...]:
    configured: list[tuple[Path, str]] = []
    if explicit_roots is not None:
        configured.extend((Path(root), "explicit") for root in explicit_roots)
    else:
        for name in ENVIRONMENT_ROOTS:
            value = environ.get(name)
            if value and value.strip():
                configured.append((Path(value), f"environment:{name}"))
        configured.append(
            (
                home
                / ".cache"
                / "codex-runtimes"
                / "codex-primary-runtime"
                / "dependencies",
                "default",
            )
        )

    unique: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for root, source in configured:
        resolved = root.expanduser().resolve()
        if resolved not in seen:
            unique.append((resolved, source))
            seen.add(resolved)
    return tuple(unique)


def _best_attempt(attempts: Sequence[ResolutionAttempt]) -> ResolutionAttempt:
    priority = {
        "native-poppler-tools-not-found": 3,
        "bundled-python-not-found": 2,
        "runtime-root-not-found": 1,
    }
    return max(attempts, key=lambda item: priority.get(item.reason, 0))


def resolve_bundled_runtime(
    roots: Iterable[Path | str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | str | None = None,
) -> BundledRuntimeResolution:
    environment = os.environ if environ is None else environ
    home_path = Path.home() if home is None else Path(home)
    configured = _configured_search_roots(
        explicit_roots=roots,
        environ=environment,
        home=home_path,
    )
    attempts: list[ResolutionAttempt] = []
    visited: set[Path] = set()

    for search_root, source in configured:
        for dependency_root in _root_variants(search_root):
            if dependency_root in visited:
                continue
            visited.add(dependency_root)
            result = _inspect_root(dependency_root, source)
            if isinstance(result, BundledRuntimeResolution):
                return result
            attempts.append(result)

    if not attempts:
        raise BundledRuntimeResolutionError("no-runtime-roots-configured", ())
    best = _best_attempt(attempts)
    raise BundledRuntimeResolutionError(best.reason, attempts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve the bundled Python and native Windows Poppler tools without "
            "executing them."
        )
    )
    parser.add_argument(
        "--root",
        "--fixture-root",
        action="append",
        dest="roots",
        type=Path,
        help=(
            "Candidate dependency root, codex-primary-runtime directory, or cache "
            "parent. Repeat to provide fallbacks."
        ),
    )
    args = parser.parse_args(argv)
    try:
        result = resolve_bundled_runtime(args.roots)
    except BundledRuntimeResolutionError as exc:
        print(json.dumps(exc.to_json(), ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result.to_json(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
