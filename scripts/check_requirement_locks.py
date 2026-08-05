from __future__ import annotations

import re
import sys
from pathlib import Path


SERVICES = ("orchestrator", "semantic-chunking-service")
REQUIREMENT_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]+\])?(?:==(?P<version>[^;\s]+))?(?:\s*;.*)?$"
)


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _requirements(path: Path) -> dict[str, str | None]:
    requirements: dict[str, str | None] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = REQUIREMENT_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(f"{path}: unsupported requirement {line!r}")
        requirements[_normalize(match.group("name"))] = match.group("version")
    return requirements


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    for service in SERVICES:
        service_dir = root / service
        runtime_source = service_dir / "requirements.txt"
        development_source = service_dir / "requirements-dev.txt"
        runtime_lock = service_dir / "requirements.lock"
        development_lock = service_dir / "requirements-dev.lock"
        dockerfile = service_dir / "Dockerfile"

        missing_lock = False
        for path in (runtime_lock, development_lock):
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"{path.relative_to(root)} must exist and be non-empty")
                missing_lock = True

        if missing_lock:
            continue

        runtime_declared = _requirements(runtime_source)
        development_declared = runtime_declared | _requirements(development_source)
        runtime_locked = _requirements(runtime_lock)
        development_locked = _requirements(development_lock)

        for label, declared, locked in (
            (runtime_lock.name, runtime_declared, runtime_locked),
            (development_lock.name, development_declared, development_locked),
        ):
            for name, version in declared.items():
                if name not in locked:
                    errors.append(f"{service}/{label}: missing {name}")
                elif version is not None and locked[name] != version:
                    errors.append(
                        f"{service}/{label}: {name} is {locked[name]}, expected {version}"
                    )

        if "requirements.lock" not in dockerfile.read_text(encoding="utf-8"):
            errors.append(f"{service}/Dockerfile must install requirements.lock")

    return errors


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    errors = validate(root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
