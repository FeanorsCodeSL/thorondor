"""Validation and evaluation for the pinned MCP conformance profile."""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).parents[1] / "tools" / "mcp-conformance" / "profile.json"
TOOLING_ROOT = PROFILE_PATH.parent
PACKAGE_PATH = TOOLING_ROOT / "package.json"
PACKAGE_LOCK_PATH = TOOLING_ROOT / "package-lock.json"
NVMRC_PATH = TOOLING_ROOT / ".nvmrc"
PROFILE_CLAIM = "2026-07-28 selected tool-server profile evidence"
CHECKS_FILENAME = "checks.json"
REQUIRED_SCENARIOS = frozenset(
    {
        "server-stateless",
        "completion-complete",
        "tools-list",
        "tools-call-simple-text",
        "tools-call-image",
        "tools-call-audio",
        "tools-call-embedded-resource",
        "tools-call-mixed-content",
        "tools-call-error",
        "tools-call-with-progress",
        "server-sse-multiple-streams",
        "resources-list",
        "resources-read-text",
        "resources-read-binary",
        "resources-templates-read",
        "sep-2164-resource-not-found",
        "prompts-list",
        "prompts-get-simple",
        "prompts-get-with-args",
        "prompts-get-embedded-resource",
        "prompts-get-with-image",
        "dns-rebinding-protection",
        "caching",
        "input-required-result-basic-elicitation",
        "input-required-result-basic-sampling",
        "input-required-result-basic-list-roots",
        "input-required-result-request-state",
        "input-required-result-multiple-input-requests",
        "input-required-result-multi-round",
        "input-required-result-missing-input-response",
        "input-required-result-non-tool-request",
        "input-required-result-result-type",
        "input-required-result-unsupported-methods",
        "input-required-result-tampered-state",
        "input-required-result-capability-check",
        "input-required-result-ignore-extra-params",
        "input-required-result-validate-input",
    }
)
NOT_SCORED_SCENARIOS = frozenset(
    {
        "json-schema-2020-12",
        "http-header-validation",
        "http-custom-header-server-validation",
        "tasks-lifecycle",
        "tasks-capability-negotiation",
        "tasks-wire-fields",
        "tasks-request-state-removal",
        "tasks-mrtr-input",
        "tasks-request-headers",
        "tasks-dispatch-and-envelope",
        "tasks-status-notifications",
        "tasks-required-task-error",
        "tasks-mrtr-composition",
    }
)
DIRECT_GATE_SCENARIOS = frozenset(
    {"tools-list", "server-sse-multiple-streams", "dns-rebinding-protection"}
)
MIXED_SCENARIOS = frozenset({"server-stateless", "caching"})
REQUIRED_CLASSIFICATIONS = {
    scenario: "direct_gate" if scenario in DIRECT_GATE_SCENARIOS else "mixed"
    if scenario in MIXED_SCENARIOS
    else "fixture_dependent"
    for scenario in REQUIRED_SCENARIOS
}
NOT_SCORED_CLASSIFICATIONS = {
    scenario: "informational"
    if scenario
    in {
        "json-schema-2020-12",
        "http-header-validation",
        "http-custom-header-server-validation",
    }
    else "outside_profile"
    for scenario in NOT_SCORED_SCENARIOS
}
GATE_REQUIREMENT_INVENTORY = {
    "server-stateless": (
        (("sep-2575-request-meta-invalid-missing-meta",), 1),
        (("sep-2575-request-meta-invalid-missing-protocol-version",), 1),
        (("sep-2575-request-meta-invalid-missing-client-capabilities",), 1),
        (("sep-2575-http-server-meta-invalid-400",), 3),
        (("sep-2575-request-meta-client-info-optional",), 1),
        (("sep-2575-server-implements-discover",), 1),
        (("sep-2575-discover-capabilities-match-handlers",), 1),
        (("sep-2575-server-unsupported-version-error",), 1),
        (("sep-2575-http-server-unsupported-version-400",), 1),
        (("sep-2575-http-server-header-mismatch-400",), 1),
        (("sep-2575-http-server-method-not-found-404-initialize",), 1),
        (("sep-2575-http-server-method-not-found-404-ping",), 1),
        (("sep-2575-http-server-method-not-found-404-logging-setlevel",), 1),
        (("sep-2575-http-server-method-not-found-404-resources-subscribe",), 1),
        (("sep-2575-http-server-method-not-found-404-resources-unsubscribe",), 1),
        (("sep-2575-http-server-method-not-found-404",), 1),
        (("sep-2575-http-server-error-jsonrpc-id",), 1),
    ),
    "tools-list": ((("tools-list",), 1),),
    "server-sse-multiple-streams": ((("server-accepts-multiple-post-streams",), 1),),
    "dns-rebinding-protection": (
        (("localhost-host-rebinding-rejected",), 1),
        (("localhost-host-valid-accepted",), 1),
    ),
    "caching": (
        (("sep-2549-caching-connection", "sep-2549-tools-list-caching-hints"), 1),
        (("sep-2549-ttl-non-negative",), 1),
        (("sep-2549-cache-scope-valid",), 1),
    ),
}
DISPOSITIONS = frozenset({"gate", "informational", "fixture_dependent", "unclassified"})
CLASSIFICATIONS = frozenset(
    {"direct_gate", "mixed", "fixture_dependent", "informational", "outside_profile"}
)
STATUSES = frozenset({"SUCCESS", "FAILURE", "WARNING", "SKIPPED", "INFO"})
RESULT_DIRECTORY_PATTERN = re.compile(r"^server-(?P<scenario>.+)-\d{4}-\d{2}-\d{2}T.+Z$")
DISPOSITION_RATIONALE_CHECKS = frozenset(
    {
        "sep-2575-server-sends-prompts-list-changed-on-subscription",
        "sep-2575-server-sends-tools-list-changed-on-subscription",
    }
)


@dataclass(frozen=True)
class EvaluatedCheck:
    scenario: str
    check_id: str
    status: str
    disposition: str
    error_message: str | None = None
    rationale: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "scenario": self.scenario,
            "check_id": self.check_id,
            "status": self.status,
            "disposition": self.disposition,
            "error_message": self.error_message,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ConformanceEvaluation:
    claim: str
    expected_scenarios: tuple[str, ...]
    observed_scenarios: tuple[str, ...]
    missing_scenarios: tuple[str, ...]
    gate_passes: tuple[EvaluatedCheck, ...]
    gate_failures: tuple[EvaluatedCheck, ...]
    gate_warnings: tuple[EvaluatedCheck, ...]
    fixture_observations: tuple[EvaluatedCheck, ...]
    informational_observations: tuple[EvaluatedCheck, ...]
    informational_warnings: tuple[EvaluatedCheck, ...]
    unexplained_warnings: tuple[EvaluatedCheck, ...]
    unclassified_checks: tuple[EvaluatedCheck, ...]
    require_all: bool
    disposition_rationales: tuple[tuple[str, str], ...]

    @property
    def clean(self) -> bool:
        return not (
            (self.require_all and self.missing_scenarios)
            or not self.gate_passes
            or self.gate_failures
            or self.unexplained_warnings
            or self.unclassified_checks
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "clean": self.clean,
            "expected_scenarios": list(self.expected_scenarios),
            "observed_scenarios": list(self.observed_scenarios),
            "missing_scenarios": list(self.missing_scenarios),
            "gate_passes": [item.as_dict() for item in self.gate_passes],
            "gate_failures": [item.as_dict() for item in self.gate_failures],
            "gate_warnings": [item.as_dict() for item in self.gate_warnings],
            "fixture_observations": [item.as_dict() for item in self.fixture_observations],
            "informational_observations": [
                item.as_dict() for item in self.informational_observations
            ],
            "informational_warnings": [
                item.as_dict() for item in self.informational_warnings
            ],
            "unexplained_warnings": [
                item.as_dict() for item in self.unexplained_warnings
            ],
            "unclassified_checks": [item.as_dict() for item in self.unclassified_checks],
            "require_all": self.require_all,
            "disposition_rationales": dict(self.disposition_rationales),
        }


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any]:
    profile = json.loads(path.read_text(encoding="utf-8"))
    validate_profile(profile)
    if path.resolve() == PROFILE_PATH.resolve():
        validate_tooling_files(profile)
    return profile


def validate_profile(profile: Mapping[str, Any]) -> None:
    _validate_profile_metadata(profile)
    _validate_runner(profile.get("runner"))
    _validate_disposition_rationales(profile.get("disposition_rationales"))
    required = _validate_scenario_collection(
        profile.get("required_scenarios"), REQUIRED_SCENARIOS, "required"
    )
    not_scored = _validate_scenario_collection(
        profile.get("not_scored_scenarios"), NOT_SCORED_SCENARIOS, "not_scored"
    )
    for entry in required:
        _validate_required_scenario(entry)
    for entry in not_scored:
        _validate_not_scored_scenario(entry)


def _validate_profile_metadata(profile: Mapping[str, Any]) -> None:
    if profile.get("schema_version") != "thorondor.mcp.conformance.profile.v1":
        raise ValueError("unsupported conformance profile schema")
    if profile.get("protocol_version") != "2026-07-28":
        raise ValueError("conformance profile must target 2026-07-28")
    if profile.get("claim") != PROFILE_CLAIM:
        raise ValueError("conformance profile claim drift")
    requirements_source = profile.get("requirements_source")
    if not isinstance(requirements_source, Mapping):
        raise ValueError("requirements source metadata is required")
    if requirements_source != {
        "repository": "https://github.com/modelcontextprotocol/conformance",
        "git_head": "a9896553900a2ef61787b57adfcbbe936a8ab1f9",
        "requirements_blob": "b0c4f8560429e8f4b6c89833cc0b35405bc004ff",
    }:
        raise ValueError("requirements source pin drift")


def _validate_required_scenario(entry: Mapping[str, Any]) -> None:
    classification = entry["classification"]
    if classification != REQUIRED_CLASSIFICATIONS[entry["id"]]:
        raise ValueError(f"required classification drift: {entry['id']}")
    if classification == "mixed" and not entry["checks"].get("rules"):
        raise ValueError(f"mixed scenario has no explicit check rules: {entry['id']}")
    if classification in {"direct_gate", "mixed"}:
        _validate_gate_requirements(entry)
    expected_default = {
        "direct_gate": "gate",
        "mixed": "unclassified",
        "fixture_dependent": "fixture_dependent",
    }[classification]
    if entry["checks"]["default"] != expected_default:
        raise ValueError(f"invalid default disposition for {entry['id']}")


def _validate_not_scored_scenario(entry: Mapping[str, Any]) -> None:
    if entry["classification"] != NOT_SCORED_CLASSIFICATIONS[entry["id"]]:
        raise ValueError(f"not_scored classification drift: {entry['id']}")
    if entry["checks"]["default"] != "informational":
        raise ValueError(f"not_scored scenario cannot be a gate: {entry['id']}")


def validate_tooling_files(
    profile: Mapping[str, Any],
    *,
    package_path: Path = PACKAGE_PATH,
    package_lock_path: Path = PACKAGE_LOCK_PATH,
    nvmrc_path: Path = NVMRC_PATH,
) -> None:
    runner = profile["runner"]
    package = json.loads(package_path.read_text(encoding="utf-8"))
    lock = json.loads(package_lock_path.read_text(encoding="utf-8"))
    expected_dependency = {runner["package"]: runner["version"]}
    if package.get("dependencies") != expected_dependency:
        raise ValueError("runner package dependency drift")
    if package.get("engines") != {"node": runner["node_version"]}:
        raise ValueError("runner package Node version drift")
    packages = lock.get("packages")
    if not isinstance(packages, Mapping):
        raise ValueError("runner package lock is invalid")
    root_package = packages.get("")
    locked_runner = packages.get(f"node_modules/{runner['package']}")
    if not isinstance(root_package, Mapping) or not isinstance(locked_runner, Mapping):
        raise ValueError("runner package lock entries are missing")
    if root_package.get("dependencies") != expected_dependency:
        raise ValueError("runner package lock dependency drift")
    if root_package.get("engines") != {"node": runner["node_version"]}:
        raise ValueError("runner package lock Node version drift")
    if locked_runner.get("version") != runner["version"]:
        raise ValueError("runner package lock version drift")
    if locked_runner.get("integrity") != runner["integrity"]:
        raise ValueError("runner package lock integrity drift")
    if nvmrc_path.read_text(encoding="utf-8").strip() != runner["node_version"]:
        raise ValueError("runner .nvmrc version drift")


def validate_run_manifest(
    profile: Mapping[str, Any], manifest: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    if manifest.get("claim") != profile["claim"]:
        raise ValueError("manifest claim differs from profile")
    if manifest.get("protocol_version") != profile["protocol_version"]:
        raise ValueError("manifest protocol version differs from profile")
    entries = manifest.get("scenarios")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest scenarios must be a non-empty list")
    classifications = {
        entry["id"]: entry["classification"] for entry in profile["required_scenarios"]
    }
    scenarios: list[str] = []
    result_directories: list[str] = []
    for entry in entries:
        scenario = _validate_manifest_scenario(entry, classifications, scenarios)
        result_directories.extend(
            _validate_manifest_result_directories(entry, scenario, result_directories)
        )
        scenarios.append(scenario)
    return scenarios, result_directories


def _validate_manifest_scenario(
    entry: Any, classifications: Mapping[str, str], scenarios: Collection[str]
) -> str:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("scenario"), str):
        raise ValueError("manifest scenario entry is invalid")
    scenario = entry["scenario"]
    if scenario not in classifications:
        raise ValueError(f"manifest contains unknown required scenario: {scenario}")
    if scenario in scenarios:
        raise ValueError(f"manifest contains duplicate scenario: {scenario}")
    timed_out = entry.get("timed_out", False)
    if not isinstance(timed_out, bool):
        raise ValueError(f"manifest timeout flag is invalid: {scenario}")
    if timed_out:
        raise ValueError(f"manifest scenario timed out: {scenario}")
    returncode = entry.get("returncode")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise ValueError(f"manifest return code is invalid: {scenario}")
    if returncode not in {0, 1}:
        raise ValueError(f"manifest runner status is invalid: {scenario}")
    if classifications[scenario] == "direct_gate" and returncode != 0:
        raise ValueError(f"direct-gate runner exited nonzero: {scenario}")
    return scenario


def _validate_manifest_result_directories(
    entry: Mapping[str, Any], scenario: str, existing: Collection[str]
) -> list[str]:
    directories = entry.get("result_directories", [])
    if not isinstance(directories, list) or not all(
        isinstance(directory, str) for directory in directories
    ):
        raise ValueError(f"manifest result directories are invalid: {scenario}")
    validated: list[str] = []
    for directory in directories:
        path = Path(directory)
        if path.is_absolute() or len(path.parts) != 1:
            raise ValueError(f"manifest result directory is not portable: {directory}")
        checks_path = path / CHECKS_FILENAME
        if _scenario_from_result_path(checks_path) != scenario:
            raise ValueError(f"manifest result directory does not match scenario: {scenario}")
        if directory in existing or directory in validated:
            raise ValueError(f"manifest contains duplicate result directory: {directory}")
        validated.append(directory)
    return validated


def evaluate_checks(
    profile: Mapping[str, Any], scenario: str, checks: list[Mapping[str, Any]]
) -> list[EvaluatedCheck]:
    entry = _scenario_entry(profile, scenario)
    evaluated: list[EvaluatedCheck] = []
    for check in checks:
        check_id = check.get("id")
        status = check.get("status")
        if not isinstance(check_id, str) or not check_id:
            raise ValueError(f"conformance check has no string id: {scenario}")
        if status not in STATUSES:
            raise ValueError(f"conformance check has invalid status: {scenario}/{check_id}")
        disposition = _check_disposition(entry["checks"], check_id)
        evaluated.append(
            EvaluatedCheck(
                scenario=scenario,
                check_id=check_id,
                status=status,
                disposition=disposition,
                error_message=_bounded_text(check.get("errorMessage")),
                rationale=_check_rationale(profile, check_id),
            )
        )
    return evaluated


def evaluate_result_directory(
    profile: Mapping[str, Any],
    results_root: Path,
    require_all: bool = True,
    scenario_scope: Collection[str] | None = None,
    result_directories: Collection[str | Path] | None = None,
) -> ConformanceEvaluation:
    validate_profile(profile)
    expected_scenarios = _expected_scenarios(profile, scenario_scope)
    result_paths = _result_check_paths(results_root, result_directories)
    checks_by_scenario: dict[str, list[Mapping[str, Any]]] = {}
    for checks_path in result_paths:
        scenario = _scenario_from_result_path(checks_path)
        if scenario_scope is not None and scenario not in expected_scenarios:
            raise ValueError(f"result scenario is outside requested scope: {scenario}")
        checks = json.loads(checks_path.read_text(encoding="utf-8"))
        if not isinstance(checks, list):
            raise ValueError(f"checks file must contain a list: {checks_path}")
        if scenario in checks_by_scenario:
            raise ValueError(f"duplicate result scenario: {scenario}")
        checks_by_scenario[scenario] = checks

    observed = tuple(sorted(checks_by_scenario))
    missing = tuple(sorted(set(expected_scenarios) - set(checks_by_scenario)))
    evaluated = [
        check
        for scenario in observed
        for check in evaluate_checks(profile, scenario, checks_by_scenario[scenario])
    ]
    return _build_evaluation(
        profile,
        observed,
        missing,
        evaluated,
        require_all=require_all,
        expected_scenarios=expected_scenarios,
    )


def _build_evaluation(
    profile: Mapping[str, Any],
    observed: tuple[str, ...],
    missing: tuple[str, ...],
    evaluated: list[EvaluatedCheck],
    *,
    require_all: bool = True,
    expected_scenarios: tuple[str, ...] | None = None,
) -> ConformanceEvaluation:
    evaluated = [*evaluated, *_missing_gate_evidence(profile, observed, evaluated)]
    informational_warnings = tuple(
        item
        for item in evaluated
        if item.disposition == "informational" and item.status == "WARNING"
    )
    return ConformanceEvaluation(
        claim=profile["claim"],
        expected_scenarios=expected_scenarios
        or tuple(sorted(entry["id"] for entry in profile["required_scenarios"])),
        observed_scenarios=observed,
        missing_scenarios=missing,
        gate_passes=tuple(
            item
            for item in evaluated
            if item.disposition == "gate" and item.status == "SUCCESS"
        ),
        gate_failures=tuple(
            item
            for item in evaluated
            if item.disposition == "gate" and item.status == "FAILURE"
        ),
        gate_warnings=tuple(
            item
            for item in evaluated
            if item.disposition == "gate" and item.status == "WARNING"
        ),
        fixture_observations=tuple(
            item for item in evaluated if item.disposition == "fixture_dependent"
        ),
        informational_observations=tuple(
            item
            for item in evaluated
            if (item.disposition == "informational" and item.status != "WARNING")
            or item.status in {"SKIPPED", "INFO"}
        ),
        informational_warnings=informational_warnings,
        unexplained_warnings=tuple(item for item in informational_warnings if not item.rationale),
        unclassified_checks=tuple(
            item for item in evaluated if item.disposition == "unclassified"
        ),
        require_all=require_all,
        disposition_rationales=tuple(
            sorted(
                (key, value)
                for key, value in profile["disposition_rationales"].items()
            )
        ),
    )


def _validate_runner(runner: Any) -> None:
    if not isinstance(runner, Mapping):
        raise ValueError("runner metadata is required")
    expected = {
        "package": "@modelcontextprotocol/conformance",
        "version": "0.2.0-alpha.10",
        "integrity": (
            "sha512-0V/HZDdWHcg6j0zVBzBsXcPZ571IVi6umKgTpnBhtTx/"
            "jm/LONmGF6cIWL2k4Xjyps0OiHV6B37nj2s0pUg0nQ=="
        ),
        "node_version": "24.19.0",
        "setup_node_action": "actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e",
    }
    for key, value in expected.items():
        if runner.get(key) != value:
            raise ValueError(f"runner pin drift: {key}")


def _validate_disposition_rationales(rationales: Any) -> None:
    if not isinstance(rationales, Mapping):
        raise ValueError("disposition rationales are required")
    if set(rationales) != DISPOSITION_RATIONALE_CHECKS:
        raise ValueError("disposition rationale inventory drift")
    if any(not isinstance(value, str) or not value.strip() for value in rationales.values()):
        raise ValueError("disposition rationales must be non-empty strings")


def _validate_gate_requirements(entry: Mapping[str, Any]) -> None:
    requirements = entry.get("gate_requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError(f"gate requirements are required: {entry['id']}")
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            raise ValueError(f"invalid gate requirement: {entry['id']}")
        patterns = requirement.get("patterns")
        minimum = requirement.get("minimum")
        if (
            not isinstance(patterns, list)
            or not patterns
            or not all(isinstance(pattern, str) and pattern for pattern in patterns)
            or isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or minimum < 1
        ):
            raise ValueError(f"invalid gate requirement: {entry['id']}")
    actual_inventory = tuple(
        (tuple(requirement["patterns"]), requirement["minimum"])
        for requirement in requirements
    )
    if actual_inventory != GATE_REQUIREMENT_INVENTORY[entry["id"]]:
        raise ValueError(f"gate requirement inventory drift: {entry['id']}")


def _check_rationale(profile: Mapping[str, Any], check_id: str) -> str | None:
    rationale = profile["disposition_rationales"].get(check_id)
    return rationale if isinstance(rationale, str) else None


def _expected_scenarios(
    profile: Mapping[str, Any], scenario_scope: Collection[str] | None
) -> tuple[str, ...]:
    available = {entry["id"] for entry in profile["required_scenarios"]}
    if scenario_scope is None:
        return tuple(sorted(available))
    scope = set(scenario_scope)
    if not scope or not all(isinstance(scenario, str) for scenario in scope):
        raise ValueError("scenario scope must contain at least one scenario id")
    unknown = scope - available
    if unknown:
        raise ValueError("scenario scope contains unknown ids: " + ", ".join(sorted(unknown)))
    return tuple(sorted(scope))


def _result_check_paths(
    results_root: Path, result_directories: Collection[str | Path] | None
) -> list[Path]:
    if result_directories is None:
        return sorted(results_root.rglob(CHECKS_FILENAME))
    root = results_root.resolve()
    paths: list[Path] = []
    for raw_directory in result_directories:
        directory = Path(raw_directory)
        if not directory.is_absolute():
            directory = root / directory
        directory = directory.resolve()
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"result directory is outside results root: {directory}") from exc
        checks_path = directory / CHECKS_FILENAME
        if not checks_path.is_file():
            raise ValueError(f"manifest result directory has no checks.json: {directory}")
        paths.append(checks_path)
    return sorted(paths)


def _missing_gate_evidence(
    profile: Mapping[str, Any], observed: tuple[str, ...], evaluated: list[EvaluatedCheck]
) -> list[EvaluatedCheck]:
    missing: list[EvaluatedCheck] = []
    scenarios = set(observed)
    entries = profile["required_scenarios"]
    for entry in entries:
        scenario = entry["id"]
        if scenario not in scenarios or entry["classification"] not in {"direct_gate", "mixed"}:
            continue
        for index, requirement in enumerate(entry["gate_requirements"], start=1):
            matching_successes = [
                item
                for item in evaluated
                if item.scenario == scenario
                and item.disposition == "gate"
                and item.status == "SUCCESS"
                and any(
                    fnmatch.fnmatchcase(item.check_id, pattern)
                    for pattern in requirement["patterns"]
                )
            ]
            if len(matching_successes) >= requirement["minimum"]:
                continue
            missing.append(
                EvaluatedCheck(
                    scenario=scenario,
                    check_id=f"profile-gate-evidence-{scenario}-{index}",
                    status="FAILURE",
                    disposition="gate",
                    error_message=(
                        "required gate evidence missing: "
                        + ", ".join(requirement["patterns"])
                    ),
                )
            )
    return missing


def _validate_scenario_collection(
    collection: Any, expected_ids: frozenset[str], label: str
) -> list[Mapping[str, Any]]:
    if not isinstance(collection, list):
        raise ValueError(f"{label} scenarios must be a list")
    entries = [entry for entry in collection if isinstance(entry, Mapping)]
    if len(entries) != len(collection):
        raise ValueError(f"{label} scenarios must contain objects")
    actual_ids = [entry.get("id") for entry in entries]
    if set(actual_ids) != expected_ids or len(actual_ids) != len(expected_ids):
        raise ValueError(f"{label} scenario set drift")
    for entry in entries:
        _validate_scenario_entry(entry, label)
    return entries


def _validate_scenario_entry(entry: Mapping[str, Any], label: str) -> None:
    if entry.get("classification") not in CLASSIFICATIONS:
        raise ValueError(f"invalid {label} classification: {entry.get('id')}")
    checks = entry.get("checks")
    if not isinstance(checks, Mapping) or checks.get("default") not in DISPOSITIONS:
        raise ValueError(f"invalid check policy: {entry.get('id')}")
    patterns = _validate_check_rules(checks.get("rules", []), entry.get("id"))
    if len(patterns) != len(set(patterns)):
        raise ValueError(f"duplicate check rule: {entry.get('id')}")
    if entry.get("classification") == "mixed":
        _validate_bounded_check_patterns(patterns, entry.get("id"))


def _validate_check_rules(rules: Any, scenario: Any) -> list[str]:
    patterns: list[str] = []
    for rule in rules:
        if (
            not isinstance(rule, Mapping)
            or not isinstance(rule.get("pattern"), str)
            or rule.get("disposition") not in DISPOSITIONS - {"unclassified"}
        ):
            raise ValueError(f"invalid check rule: {scenario}")
        patterns.append(rule["pattern"])
    return patterns


def _validate_bounded_check_patterns(patterns: Collection[str], scenario: Any) -> None:
    if any(any(character in pattern for character in "*?[]") for pattern in patterns):
        raise ValueError(f"mixed check rules must be bounded: {scenario}")


def _scenario_entry(profile: Mapping[str, Any], scenario: str) -> Mapping[str, Any]:
    for entry in profile["required_scenarios"] + profile["not_scored_scenarios"]:
        if entry["id"] == scenario:
            return entry
    raise ValueError(f"scenario is absent from profile: {scenario}")


def _check_disposition(check_policy: Mapping[str, Any], check_id: str) -> str:
    for rule in check_policy.get("rules", []):
        if fnmatch.fnmatchcase(check_id, rule["pattern"]):
            return rule["disposition"]
    return check_policy["default"]


def _scenario_from_result_path(checks_path: Path) -> str:
    match = RESULT_DIRECTORY_PATTERN.match(checks_path.parent.name)
    if match is None:
        raise ValueError(f"cannot infer scenario from result directory: {checks_path}")
    return match.group("scenario")


def _bounded_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:1024]
