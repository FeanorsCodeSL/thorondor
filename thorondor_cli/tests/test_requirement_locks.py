from pathlib import Path

from scripts.check_requirement_locks import validate


def _write_service(root: Path, service: str, runtime_lock: str) -> None:
    service_dir = root / service
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("example==1.0\n", encoding="utf-8")
    (service_dir / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    (service_dir / "requirements.lock").write_text(runtime_lock, encoding="utf-8")
    (service_dir / "requirements-dev.lock").write_text(
        "example==1.0\npytest==9.1.1\n", encoding="utf-8"
    )
    (service_dir / "Dockerfile").write_text(
        "COPY requirements.lock .\nRUN pip install -r requirements.lock\n", encoding="utf-8"
    )


def test_repository_requirement_locks_match_sources_and_dockerfiles():
    assert validate(Path(__file__).resolve().parents[2]) == []


def test_requirement_lock_validation_reports_direct_version_drift(tmp_path):
    _write_service(tmp_path, "orchestrator", "example==0.9\n")
    _write_service(tmp_path, "semantic-chunking-service", "example==1.0\n")

    assert validate(tmp_path) == [
        "orchestrator/requirements.lock: example is 0.9, expected 1.0"
    ]
