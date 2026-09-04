"""Local, advisory-only agent orchestration for DNA MIDI Studio.

The runtime records bounded work and approvals.  It never accepts MIDI bytes,
never writes an export and cannot turn an agent recommendation into a validator
verdict.  Optional cloud calls are metadata-only, explicit-consent operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping


_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TASK_STATUSES = {"PLANNED", "IN_PROGRESS", "HANDOFF_READY", "AWAITING_APPROVAL", "APPROVED", "BLOCKED", "COMPLETE"}
_FINAL_ACTIONS = {"export-to-device", "overwrite-style", "change-factory-dynamics-policy"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _contains_midi(value: Any, key: str = "") -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if key.lower() in {"midi", "midibytes", "filebytes", "content", "body"}:
        return True
    if isinstance(value, str):
        return value.lower().endswith((".mid", ".midi"))
    if isinstance(value, Mapping):
        return any(_contains_midi(item, str(name)) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_midi(item) for item in value)
    return False


def _contains_secret(value: Any, key: str = "") -> bool:
    normalized = key.lower().replace("-", "").replace("_", "")
    if normalized in {"apikey", "authorization", "accesstoken", "secrettoken"}:
        return True
    if isinstance(value, Mapping):
        return any(_contains_secret(item, str(name)) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    owner: str
    exclusive_files: tuple[str, ...]
    input_hashes: Mapping[str, str]
    dependencies: tuple[str, ...]
    requested_behavior: str
    exclusions: tuple[str, ...]
    acceptance_tests: tuple[str, ...]
    acceptance_command: str
    action: str = "advisory"

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclusive_files", tuple(self.exclusive_files))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "exclusions", tuple(self.exclusions))
        object.__setattr__(self, "acceptance_tests", tuple(self.acceptance_tests))
        if not _ID.fullmatch(self.task_id) or not _ID.fullmatch(self.owner):
            raise ValueError("Task and owner require stable lowercase IDs")
        if not self.exclusive_files or any(not _safe_relative(item) for item in self.exclusive_files):
            raise ValueError("Exclusive ownership requires safe relative files")
        if len(set(self.exclusive_files)) != len(self.exclusive_files):
            raise ValueError("Exclusive files must be unique")
        if not self.input_hashes or any(not _HASH.fullmatch(value) for value in self.input_hashes.values()):
            raise ValueError("Input hashes must be lowercase SHA-256")
        if not self.requested_behavior.strip() or not self.exclusions:
            raise ValueError("Requested behavior and exclusions are required")
        if not self.acceptance_tests or not self.acceptance_command.strip():
            raise ValueError("Acceptance tests and command are required")

    def brief(self) -> dict[str, Any]:
        """Return the complete read-only brief; raw source data is never included."""
        return {
            "taskId": self.task_id, "owner": self.owner,
            "exclusiveFiles": list(self.exclusive_files),
            "inputHashes": dict(self.input_hashes), "dependencies": list(self.dependencies),
            "requestedBehavior": self.requested_behavior, "exclusions": list(self.exclusions),
            "acceptanceTests": list(self.acceptance_tests),
            "acceptanceCommand": self.acceptance_command, "action": self.action,
            "access": "read-only-brief", "mayWriteFinalMidi": False,
        }


@dataclass(frozen=True)
class AgentSubmission:
    produced_files: tuple[str, ...]
    diff_summary: str
    observed_test_output: str
    unresolved_risks: tuple[str, ...]
    status: str
    recommendation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "produced_files", tuple(self.produced_files))
        object.__setattr__(self, "unresolved_risks", tuple(self.unresolved_risks))
        if self.status not in {"HANDOFF_READY", "BLOCKED"}:
            raise ValueError("Agent submissions may only be HANDOFF_READY or BLOCKED")
        if any(not _safe_relative(item) for item in self.produced_files):
            raise ValueError("Produced files must be safe relative paths")
        if any(item.lower().endswith((".mid", ".midi")) for item in self.produced_files):
            raise ValueError("Agents cannot produce final MIDI")
        if not self.diff_summary.strip() or not self.observed_test_output.strip():
            raise ValueError("Diff and observed test output are required")


@dataclass(frozen=True)
class CloudPolicy:
    enabled: bool = False
    explicit_consent: bool = False
    metadata_only: bool = True


@dataclass
class RuntimeJob:
    spec: TaskSpec
    current_owner: str
    status: str = "PLANNED"
    submission: AgentSubmission | None = None
    human_approved: bool = False
    validator_passed: bool = False
    trace: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)


class AgentRuntime:
    def __init__(self, team: Mapping[str, Any]):
        agents = team.get("agents", [])
        self.roles = {item["id"]: item for item in agents}
        self.handoffs = {tuple(item) for item in team.get("handoffs", [])}
        self.manager = team.get("orchestration", {}).get("manager")
        self.approval_actions = set(team.get("orchestration", {}).get("humanApprovalRequiredFor", _FINAL_ACTIONS))
        self.jobs: dict[str, RuntimeJob] = {}

    @classmethod
    def from_file(cls, path: Path) -> "AgentRuntime":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def _record(self, job: RuntimeJob, event: str, actor: str, details: Mapping[str, Any] | None = None) -> None:
        previous = job.trace[-1]["eventHash"] if job.trace else "0" * 64
        payload = {"sequence": len(job.trace) + 1, "event": event, "actor": actor,
                   "details": dict(details or {}), "previousHash": previous}
        payload["eventHash"] = sha256(_canonical(payload)).hexdigest()
        job.trace.append(payload)

    def create(self, spec: TaskSpec) -> RuntimeJob:
        if spec.owner not in self.roles or spec.task_id in self.jobs:
            raise ValueError("Unknown owner or duplicate task")
        occupied = {path for job in self.jobs.values() if job.status not in {"BLOCKED", "COMPLETE"}
                    for path in job.spec.exclusive_files}
        if occupied.intersection(spec.exclusive_files):
            raise ValueError("Exclusive file ownership conflict")
        job = RuntimeJob(spec=spec, current_owner=spec.owner)
        self.jobs[spec.task_id] = job
        self._record(job, "task-created", spec.owner, {"briefHash": sha256(_canonical(spec.brief())).hexdigest()})
        return job

    def start(self, task_id: str, actor: str) -> None:
        job = self.jobs[task_id]
        if actor != job.current_owner or job.status != "PLANNED":
            raise ValueError("Only the owning agent can start a planned task")
        job.status = "IN_PROGRESS"
        self._record(job, "task-started", actor)

    def submit(self, task_id: str, actor: str, submission: AgentSubmission) -> None:
        job = self.jobs[task_id]
        if actor != job.current_owner or job.status != "IN_PROGRESS":
            raise ValueError("Submission requires the active owner")
        job.submission, job.status = submission, submission.status
        self._record(job, "submission-recorded", actor, {
            "producedFiles": list(submission.produced_files),
            "diffSummary": submission.diff_summary,
            "observedTestOutput": submission.observed_test_output,
            "unresolvedRisks": list(submission.unresolved_risks),
            "status": submission.status,
            "recommendation": submission.recommendation,
        })

    def handoff(self, task_id: str, actor: str, target: str) -> None:
        job = self.jobs[task_id]
        if actor != job.current_owner or target not in self.roles or (actor, target) not in self.handoffs:
            raise ValueError("Handoff is not allowed by the team contract")
        if job.status != "HANDOFF_READY":
            raise ValueError("Handoff requires a recorded HANDOFF_READY submission")
        job.current_owner, job.status, job.submission = target, "PLANNED", None
        self._record(job, "task-handed-off", actor, {"target": target})

    def request_approval(self, task_id: str, actor: str) -> None:
        job = self.jobs[task_id]
        if actor != self.manager or job.status != "HANDOFF_READY":
            raise ValueError("Manager may request approval only after a completed handoff")
        job.status = "AWAITING_APPROVAL"
        self._record(job, "approval-requested", actor, {"action": job.spec.action})

    def record_evaluation(self, task_id: str, actor: str, criteria: Mapping[str, bool], notes: str) -> None:
        job = self.jobs[task_id]
        if self.roles.get(actor, {}).get("role") not in {"musical-evaluation", "data-quality"}:
            raise ValueError("Evaluation requires a contracted evaluator role")
        if actor != job.current_owner or job.status != "HANDOFF_READY":
            raise ValueError("Evaluation requires the completed evaluator submission")
        if not criteria or any(not isinstance(value, bool) for value in criteria.values()) or not notes.strip():
            raise ValueError("Evaluation requires boolean criteria and notes")
        evaluation = {"actor": actor, "criteria": dict(criteria), "notes": notes,
                      "passed": all(criteria.values())}
        job.evaluations.append(evaluation)
        self._record(job, "evaluation-recorded", actor, evaluation)

    def approve(self, task_id: str, human_id: str) -> None:
        job = self.jobs[task_id]
        if job.status != "AWAITING_APPROVAL" or not human_id.strip():
            raise ValueError("Explicit human approval is required")
        job.human_approved, job.status = True, "APPROVED"
        self._record(job, "human-approved", human_id, {"action": job.spec.action})

    def record_validator(self, task_id: str, actor: str, passed: bool, report_hash: str) -> None:
        job = self.jobs[task_id]
        role = self.roles.get(actor, {}).get("role")
        if role != "pa800-validator" or actor == job.spec.owner or not _HASH.fullmatch(report_hash):
            raise ValueError("An independent validator and report SHA-256 are required")
        job.validator_passed = bool(passed)
        self._record(job, "validator-result", actor, {"passed": bool(passed), "reportHash": report_hash})

    def complete(self, task_id: str, actor: str) -> None:
        job = self.jobs[task_id]
        if actor != self.manager:
            raise ValueError("Only the manager can close a task")
        if job.status not in {"HANDOFF_READY", "APPROVED"} or job.submission is None:
            raise ValueError("A complete structured submission is required")
        if not job.validator_passed:
            raise ValueError("Validator failure or absence blocks completion")
        if job.spec.action in self.approval_actions and not job.human_approved:
            raise ValueError("Human approval is required for this action")
        job.status = "COMPLETE"
        self._record(job, "task-completed", actor)

    def manifest(self, task_id: str) -> dict[str, Any]:
        job = self.jobs[task_id]
        if job.status not in _TASK_STATUSES:
            raise ValueError("Invalid runtime status")
        return {"schema": "dna-agent-runtime-job", "version": "1.0", "task": job.spec.brief(),
                "currentOwner": job.current_owner, "status": job.status,
                "humanApproved": job.human_approved, "validatorPassed": job.validator_passed,
                "evaluations": list(job.evaluations),
                "trace": list(job.trace), "traceHash": job.trace[-1]["eventHash"]}


def dispatch_optional_cloud(policy: CloudPolicy, payload: Mapping[str, Any],
                            cloud_call: Callable[[Mapping[str, Any]], Any],
                            local_fallback: Callable[[Mapping[str, Any]], Any]) -> dict[str, Any]:
    """Use cloud only with consent and metadata; failures preserve offline behavior."""
    if not policy.enabled:
        return {"mode": "local", "reason": "cloud-disabled", "result": local_fallback(payload)}
    if not policy.explicit_consent:
        raise PermissionError("Cloud use requires explicit consent")
    if not policy.metadata_only or _contains_midi(payload) or _contains_secret(payload):
        raise ValueError("Cloud payload must be metadata-only and cannot contain MIDI or secrets")
    try:
        return {"mode": "cloud", "reason": "explicit-consent", "result": cloud_call(payload)}
    except (ConnectionError, TimeoutError, OSError) as exc:
        return {"mode": "local", "reason": f"cloud-unavailable:{type(exc).__name__}",
                "result": local_fallback(payload)}