import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.mcp_conformance_profile import (
    NOT_SCORED_SCENARIOS,
    PROFILE_PATH,
    REQUIRED_SCENARIOS,
    _build_evaluation,
    evaluate_checks,
    evaluate_result_directory,
    load_profile,
    validate_profile,
    validate_run_manifest,
    validate_tooling_files,
)

ROOT = Path(__file__).parents[2]


def test_profile_has_frozen_scenario_sets_and_runner_pins():
    profile = load_profile()

    assert len(profile["required_scenarios"]) == 37
    assert len(profile["not_scored_scenarios"]) == 13
    assert {entry["id"] for entry in profile["required_scenarios"]} == REQUIRED_SCENARIOS
    assert {entry["id"] for entry in profile["not_scored_scenarios"]} == NOT_SCORED_SCENARIOS
    assert profile["runner"]["version"] == "0.2.0-alpha.10"
    assert profile["runner"]["integrity"].startswith("sha512-")
    assert profile["runner"]["node_version"] == "24.19.0"

    lock = json.loads(
        (PROFILE_PATH.parent / "package-lock.json").read_text(encoding="utf-8")
    )
    package = lock["packages"]["node_modules/@modelcontextprotocol/conformance"]
    assert package["version"] == profile["runner"]["version"]
    assert package["integrity"] == profile["runner"]["integrity"]
    workflow = Path(".github/workflows/release-guard.yml").read_text(encoding="utf-8")
    assert f"actions/setup-node@{profile['runner']['setup_node_action'].split('@')[-1]}" in workflow
    assert "actions/upload-artifact@v7.0.1" in workflow


def test_tooling_files_match_profile_pins(tmp_path):
    profile = load_profile()
    package_path = tmp_path / "package.json"
    package_lock_path = tmp_path / "package-lock.json"
    nvmrc_path = tmp_path / ".nvmrc"
    package_path.write_text(
        (PROFILE_PATH.parent / "package.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    package_lock_path.write_text(
        (PROFILE_PATH.parent / "package-lock.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    nvmrc_path.write_text(profile["runner"]["node_version"] + "\n", encoding="utf-8")

    validate_tooling_files(
        profile,
        package_path=package_path,
        package_lock_path=package_lock_path,
        nvmrc_path=nvmrc_path,
    )

    nvmrc_path.write_text("24.18.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match=".nvmrc version drift"):
        validate_tooling_files(
            profile,
            package_path=package_path,
            package_lock_path=package_lock_path,
            nvmrc_path=nvmrc_path,
        )


def test_manifest_validation_fails_closed_on_timeout_and_direct_gate_exit():
    profile = load_profile()
    result_directory = "server-tools-list-2026-08-18T12-00-00-000Z"
    manifest = {
        "claim": profile["claim"],
        "protocol_version": profile["protocol_version"],
        "scenarios": [
            {
                "scenario": "tools-list",
                "returncode": None,
                "timed_out": True,
                "result_directories": [result_directory],
            }
        ],
    }

    with pytest.raises(ValueError, match="scenario timed out"):
        validate_run_manifest(profile, manifest)

    manifest["scenarios"][0] = {
        "scenario": "tools-list",
        "returncode": 1,
        "result_directories": [result_directory],
    }
    with pytest.raises(ValueError, match="direct-gate runner exited nonzero"):
        validate_run_manifest(profile, manifest)


def test_manifest_validation_binds_result_directory_to_scenario():
    profile = load_profile()
    manifest = {
        "claim": profile["claim"],
        "protocol_version": profile["protocol_version"],
        "scenarios": [
            {
                "scenario": "tools-list",
                "returncode": 0,
                "result_directories": [
                    "server-dns-rebinding-protection-2026-08-18T12-00-00-000Z"
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="does not match scenario"):
        validate_run_manifest(profile, manifest)


def test_manifest_validation_rejects_duplicate_scenarios_and_result_directories():
    profile = load_profile()
    result_directory = "server-tools-list-2026-08-18T12-00-00-000Z"
    scenario = {
        "scenario": "tools-list",
        "returncode": 0,
        "result_directories": [result_directory],
    }
    manifest = {
        "claim": profile["claim"],
        "protocol_version": profile["protocol_version"],
        "scenarios": [scenario, scenario],
    }

    with pytest.raises(ValueError, match="duplicate scenario"):
        validate_run_manifest(profile, manifest)

    manifest["scenarios"] = [scenario | {"result_directories": [result_directory] * 2}]
    with pytest.raises(ValueError, match="duplicate result directory"):
        validate_run_manifest(profile, manifest)


def test_mixed_scenario_check_ids_have_explicit_dispositions():
    profile = load_profile()
    samples = {
        "server-stateless": [
            "sep-2575-request-meta-invalid-missing-meta",
            "sep-2575-http-server-meta-invalid-400",
            "sep-2575-server-implements-discover",
            "sep-2575-server-identifies-in-result-meta",
            "sep-2575-server-unsupported-version-error",
            "sep-2575-server-rejects-undeclared-capability",
            "sep-2575-http-server-method-not-found-404",
            "sep-2575-http-server-no-independent-requests-on-stream",
            "sep-2575-server-sends-subscription-ack",
            "sep-2575-server-sends-prompts-list-changed-on-subscription",
            "sep-2575-http-server-error-jsonrpc-id",
        ],
        "caching": [
            "sep-2549-tools-list-caching-hints",
            "sep-2549-prompts-list-caching-hints",
            "sep-2549-resources-list-caching-hints",
            "sep-2549-resources-templates-list-caching-hints",
            "sep-2549-resources-read-caching-hints",
            "sep-2549-ttl-non-negative",
            "sep-2549-cache-scope-valid",
        ],
    }

    for scenario, check_ids in samples.items():
        evaluated = evaluate_checks(
            profile,
            scenario,
            [{"id": check_id, "status": "SUCCESS"} for check_id in check_ids],
        )
        assert all(item.disposition in {"gate", "informational"} for item in evaluated)


def test_unclassified_mixed_check_fails_closed():
    profile = load_profile()

    evaluated = evaluate_checks(
        profile,
        "server-stateless",
        [{"id": "future-check", "status": "SUCCESS"}],
    )

    assert evaluated[0].disposition == "unclassified"


def test_fixture_dependent_success_cannot_claim_production_conformance():
    profile = load_profile()

    evaluated = evaluate_checks(
        profile,
        "tools-call-error",
        [{"id": "tools-call-error", "status": "SUCCESS"}],
    )
    result = _evaluation_from_checks(profile, evaluated)

    assert result.clean is False
    assert result.fixture_observations[0].disposition == "fixture_dependent"


def test_gate_failures_and_warnings_are_distinguished():
    profile = load_profile()
    evaluated = evaluate_checks(
        profile,
        "tools-list",
        [
            {"id": "tools-list", "status": "SUCCESS"},
            {"id": "tools-list-extra", "status": "WARNING"},
        ],
    )
    result = _evaluation_from_checks(profile, evaluated)

    assert result.clean is True
    assert len(result.gate_passes) == 1
    assert len(result.gate_warnings) == 1


def test_result_directory_evaluation_requires_all_required_scenarios(tmp_path):
    profile = load_profile()
    result_dir = tmp_path / "server-tools-list-2026-08-18T12-00-00-000Z"
    result_dir.mkdir()
    (result_dir / "checks.json").write_text(
        json.dumps([{"id": "tools-list", "status": "SUCCESS"}]), encoding="utf-8"
    )

    result = evaluate_result_directory(profile, tmp_path)

    assert len(result.missing_scenarios) == 36
    assert result.clean is False

    partial = evaluate_result_directory(profile, tmp_path, require_all=False)
    assert len(partial.missing_scenarios) == 36
    assert partial.clean is True


def test_focused_result_directory_scope_is_explicit_and_clean(tmp_path):
    profile = load_profile()
    result_dir = tmp_path / "server-tools-list-2026-08-18T12-00-00-000Z"
    result_dir.mkdir()
    (result_dir / "checks.json").write_text(
        json.dumps([{"id": "tools-list", "status": "SUCCESS"}]), encoding="utf-8"
    )

    result = evaluate_result_directory(
        profile,
        tmp_path,
        scenario_scope=("tools-list",),
    )

    assert result.expected_scenarios == ("tools-list",)
    assert result.missing_scenarios == ()
    assert result.clean is True


def test_manifest_scope_uses_only_listed_result_directories(tmp_path):
    profile = load_profile()
    selected = tmp_path / "server-tools-list-2026-08-18T12-00-00-000Z"
    selected.mkdir()
    (selected / "checks.json").write_text(
        json.dumps([{"id": "tools-list", "status": "SUCCESS"}]), encoding="utf-8"
    )
    stale = tmp_path / "server-server-sse-multiple-streams-2026-08-18T12-00-00-000Z"
    stale.mkdir()
    (stale / "checks.json").write_text(
        json.dumps([{"id": "server-accepts-multiple-post-streams", "status": "SUCCESS"}]),
        encoding="utf-8",
    )

    result = evaluate_result_directory(
        profile,
        tmp_path,
        scenario_scope=("tools-list",),
        result_directories=(selected.relative_to(tmp_path),),
    )

    assert result.observed_scenarios == ("tools-list",)
    assert result.clean is True


def test_manifest_timeout_without_result_artifact_fails_closed(tmp_path):
    profile = load_profile()

    result = evaluate_result_directory(
        profile,
        tmp_path,
        scenario_scope=("tools-list",),
        result_directories=(),
    )

    assert result.missing_scenarios == ("tools-list",)
    assert result.clean is False


def test_informational_warnings_have_explicit_rationales():
    profile = load_profile()
    evaluated = evaluate_checks(
        profile,
        "server-stateless",
        [
            {
                "id": "sep-2575-server-sends-tools-list-changed-on-subscription",
                "status": "WARNING",
            }
        ],
    )

    result = _evaluation_from_checks(profile, evaluated)

    assert len(result.informational_warnings) == 1
    assert result.unexplained_warnings == ()
    assert result.informational_warnings[0].rationale


def test_complete_scenario_set_without_complete_gate_evidence_cannot_pass(tmp_path):
    profile = load_profile()
    for entry in profile["required_scenarios"]:
        result_dir = tmp_path / f"server-{entry['id']}-2026-08-18T12-00-00-000Z"
        result_dir.mkdir()
        checks = []
        if entry["id"] == "tools-list":
            checks = [{"id": "tools-list", "status": "SUCCESS"}]
        elif entry["classification"] == "fixture_dependent":
            checks = [{"id": entry["id"], "status": "SUCCESS"}]
        (result_dir / "checks.json").write_text(json.dumps(checks), encoding="utf-8")

    result = evaluate_result_directory(profile, tmp_path)

    assert result.missing_scenarios == ()
    assert result.clean is False
    assert result.gate_failures


def test_known_caching_connection_check_is_explicitly_gated():
    profile = load_profile()
    evaluated = evaluate_checks(
        profile,
        "caching",
        [{"id": "sep-2549-caching-connection", "status": "FAILURE"}],
    )

    assert evaluated[0].disposition == "gate"


def test_source_shaped_sse_success_is_a_clean_direct_gate():
    profile = load_profile()
    evaluated = evaluate_checks(
        profile,
        "server-sse-multiple-streams",
        [
            {"id": "server-accepts-multiple-post-streams", "status": "SUCCESS"},
            {"id": "server-sse-streams-functional", "status": "INFO"},
        ],
    )
    result = _build_evaluation(
        profile,
        ("server-sse-multiple-streams",),
        (),
        evaluated,
    )

    assert result.clean is True
    assert not result.gate_failures


def test_evaluator_script_is_directly_runnable():
    result = subprocess.run(
        [sys.executable, "scripts/evaluate-mcp-conformance.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Evaluate Thorondor MCP conformance results" in result.stdout


def test_runner_rejects_nonempty_output_directory(tmp_path):
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    (output_dir / "stale.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/run-mcp-conformance.py", "--output-dir", str(output_dir)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "output directory must be empty" in result.stderr


def test_runner_rejects_unknown_scenario_before_starting_server(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run-mcp-conformance.py",
            "--output-dir",
            str(tmp_path / "evidence"),
            "--scenario",
            "not-a-required-scenario",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unknown required scenario ids" in result.stderr


def test_profile_rejects_runner_drift():
    for key, value, message in (
        ("version", "0.2.0-alpha.11", "runner pin drift"),
        ("setup_node_action", "drift", "runner pin drift"),
        ("git_head", "drift", "requirements source pin drift"),
        ("requirements_blob", "drift", "requirements source pin drift"),
    ):
        profile = load_profile()
        if key in profile["runner"]:
            profile["runner"][key] = value
        else:
            profile["requirements_source"][key] = value

        with pytest.raises(ValueError, match=message):
            validate_profile(profile)


def test_profile_rejects_claim_drift():
    profile = load_profile()
    profile["claim"] = "broader claim"

    with pytest.raises(ValueError, match="profile claim drift"):
        validate_profile(profile)


def test_profile_rejects_disposition_rationale_drift():
    profile = load_profile()
    del profile["disposition_rationales"][
        "sep-2575-server-sends-tools-list-changed-on-subscription"
    ]

    with pytest.raises(ValueError, match="disposition rationale inventory drift"):
        validate_profile(profile)


def test_profile_rejects_gate_default_drift():
    profile = load_profile()
    profile["required_scenarios"][2]["checks"]["default"] = "informational"

    with pytest.raises(ValueError, match="invalid default disposition"):
        validate_profile(profile)


def test_profile_rejects_not_scored_gate_drift():
    profile = load_profile()
    profile["not_scored_scenarios"][0]["checks"]["default"] = "gate"

    with pytest.raises(ValueError, match="not_scored scenario cannot be a gate"):
        validate_profile(profile)


def test_profile_rejects_bounded_inventory_drift():
    profile = load_profile()
    sse = next(
        entry
        for entry in profile["required_scenarios"]
        if entry["id"] == "server-sse-multiple-streams"
    )
    sse["gate_requirements"][0]["patterns"] = ["server-sse-multiple-streams-error"]

    with pytest.raises(ValueError, match="gate requirement inventory drift"):
        validate_profile(profile)


def test_profile_rejects_question_and_class_wildcards():
    for wildcard in ("?", "[a]"):
        profile = load_profile()
        stateless = next(
            entry
            for entry in profile["required_scenarios"]
            if entry["id"] == "server-stateless"
        )
        stateless["checks"]["rules"][0]["pattern"] += wildcard

        with pytest.raises(ValueError, match="mixed check rules must be bounded"):
            validate_profile(profile)


def test_profile_rejects_required_classification_drift():
    profile = load_profile()
    entry = next(
        entry for entry in profile["required_scenarios"] if entry["id"] == "tools-list"
    )
    entry["classification"] = "fixture_dependent"

    with pytest.raises(ValueError, match="required classification drift"):
        validate_profile(profile)


def _evaluation_from_checks(profile, evaluated):
    return _build_evaluation(profile, ("test",), (), evaluated)
