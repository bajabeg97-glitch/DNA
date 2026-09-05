from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from dna_midi_studio import (
    AgentRuntime,
    AgentSubmission,
    CloudPolicy,
    TaskSpec,
    dispatch_optional_cloud,
)


ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64
REPORT_HASH = "b" * 64


class Session8AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = AgentRuntime.from_file(ROOT / "agents" / "agent-team.json")

    def spec(self, **changes) -> TaskSpec:
        values = {
            "task_id": "session8.demo", "owner": "chatgpt-orchestrator",
            "exclusive_files": ("artifacts/session8-plan.json",),
            "input_hashes": {"project": HASH}, "dependencies": ("offline-core",),
            "requested_behavior": "Evaluate a bounded plan without writing MIDI.",
            "exclusions": ("write-midi", "bypass-validator"),
            "acceptance_tests": ("trace-valid", "validator-pass"),
            "acceptance_command": "py session8_release_check.py", "action": "advisory",
        }
        values.update(changes)
        return TaskSpec(**values)

    def submission(self, **changes) -> AgentSubmission:
        values = {"produced_files": ("artifacts/session8-advice.json",),
                  "diff_summary": "Recorded advisory evaluation only.",
                  "observed_test_output": "local checks PASS", "unresolved_risks": (),
                  "status": "HANDOFF_READY", "recommendation": "Proceed to validator."}
        values.update(changes)
        return AgentSubmission(**values)

    def ready(self, spec=None):
        spec = spec or self.spec()
        job = self.runtime.create(spec)
        self.runtime.start(spec.task_id, spec.owner)
        self.runtime.submit(spec.task_id, spec.owner, self.submission())
        return job

    def test_task_requires_sha256_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.spec(input_hashes={"project": "bad"})

    def test_task_rejects_unsafe_owned_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe relative"):
            self.spec(exclusive_files=("../outside.json",))

    def test_brief_is_read_only_and_contains_required_handoff_fields(self) -> None:
        brief = self.spec().brief()
        self.assertEqual(brief["access"], "read-only-brief")
        self.assertFalse(brief["mayWriteFinalMidi"])
        self.assertTrue({"taskId", "owner", "exclusiveFiles", "inputHashes", "dependencies",
                         "requestedBehavior", "exclusions", "acceptanceTests", "acceptanceCommand"} <= set(brief))

    def test_unknown_owner_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown owner"):
            self.runtime.create(self.spec(owner="unknown-agent"))

    def test_exclusive_file_conflict_is_rejected(self) -> None:
        self.runtime.create(self.spec())
        with self.assertRaisesRegex(ValueError, "ownership conflict"):
            self.runtime.create(self.spec(task_id="session8.other"))

    def test_only_owner_can_start(self) -> None:
        self.runtime.create(self.spec())
        with self.assertRaisesRegex(ValueError, "owning agent"):
            self.runtime.start("session8.demo", "codex-lead-engineer")

    def test_submission_requires_observed_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "observed test output"):
            self.submission(observed_test_output="")

    def test_agent_submission_cannot_produce_midi(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot produce final MIDI"):
            self.submission(produced_files=("artifacts/final.mid",))

    def test_allowed_handoff_changes_owner(self) -> None:
        spec = self.spec(owner="codex-lead-engineer")
        self.ready(spec)
        self.runtime.handoff(spec.task_id, spec.owner, "codex-midi-compliance")
        self.assertEqual(self.runtime.jobs[spec.task_id].current_owner, "codex-midi-compliance")
        self.runtime.start(spec.task_id, "codex-midi-compliance")
        self.runtime.submit(spec.task_id, "codex-midi-compliance", self.submission())
        self.runtime.handoff(spec.task_id, "codex-midi-compliance", "chatgpt-style-evaluator")
        self.runtime.start(spec.task_id, "chatgpt-style-evaluator")
        self.runtime.submit(spec.task_id, "chatgpt-style-evaluator", self.submission())
        self.runtime.record_evaluation(spec.task_id, "chatgpt-style-evaluator",
                                       {"bounded": True, "validatorIndependent": True}, "All criteria passed.")
        self.assertTrue(self.runtime.jobs[spec.task_id].evaluations[0]["passed"])

    def test_unlisted_handoff_is_rejected(self) -> None:
        spec = self.spec(owner="codex-lead-engineer")
        self.ready(spec)
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.runtime.handoff(spec.task_id, spec.owner, "chatgpt-style-evaluator")

    def test_trace_is_hash_chained(self) -> None:
        job = self.ready()
        self.assertEqual(job.trace[1]["previousHash"], job.trace[0]["eventHash"])
        self.assertEqual(job.trace[2]["previousHash"], job.trace[1]["eventHash"])

    def test_only_manager_can_request_approval(self) -> None:
        self.ready()
        with self.assertRaisesRegex(ValueError, "Manager"):
            self.runtime.request_approval("session8.demo", "codex-lead-engineer")

    def test_human_approval_is_explicitly_recorded(self) -> None:
        job = self.ready()
        self.runtime.request_approval("session8.demo", "chatgpt-orchestrator")
        self.runtime.approve("session8.demo", "local-user")
        self.assertTrue(job.human_approved)
        self.assertEqual(job.status, "APPROVED")

    def test_sensitive_action_cannot_complete_without_approval(self) -> None:
        spec = self.spec(action="export-to-device")
        self.ready(spec)
        self.runtime.record_validator(spec.task_id, "codex-midi-compliance", True, REPORT_HASH)
        with self.assertRaisesRegex(ValueError, "Human approval"):
            self.runtime.complete(spec.task_id, "chatgpt-orchestrator")

    def test_validator_must_be_independent(self) -> None:
        spec = self.spec(owner="codex-midi-compliance")
        self.ready(spec)
        with self.assertRaisesRegex(ValueError, "independent validator"):
            self.runtime.record_validator(spec.task_id, "codex-midi-compliance", True, REPORT_HASH)

    def test_validator_failure_blocks_completion(self) -> None:
        self.ready()
        self.runtime.record_validator("session8.demo", "codex-midi-compliance", False, REPORT_HASH)
        with self.assertRaisesRegex(ValueError, "blocks completion"):
            self.runtime.complete("session8.demo", "chatgpt-orchestrator")

    def test_validated_advisory_job_can_complete(self) -> None:
        job = self.ready()
        self.runtime.record_validator("session8.demo", "codex-midi-compliance", True, REPORT_HASH)
        self.runtime.complete("session8.demo", "chatgpt-orchestrator")
        self.assertEqual(job.status, "COMPLETE")

    def test_cloud_is_off_by_default_and_call_is_not_made(self) -> None:
        calls = []
        result = dispatch_optional_cloud(CloudPolicy(), {"taskId": "x"},
                                         lambda payload: calls.append(payload), lambda payload: "offline")
        self.assertEqual((result["mode"], result["result"], calls), ("local", "offline", []))

    def test_cloud_requires_explicit_consent(self) -> None:
        with self.assertRaises(PermissionError):
            dispatch_optional_cloud(CloudPolicy(enabled=True), {"taskId": "x"}, lambda p: "cloud", lambda p: "local")

    def test_cloud_rejects_midi_and_secrets(self) -> None:
        policy = CloudPolicy(enabled=True, explicit_consent=True)
        for payload in ({"file": "song.mid"}, {"midiBytes": b"MThd"}, {"apiKey": "secret"}):
            with self.subTest(payload=tuple(payload)):
                with self.assertRaisesRegex(ValueError, "MIDI or secrets"):
                    dispatch_optional_cloud(policy, payload, lambda p: "cloud", lambda p: "local")

    def test_cloud_network_failure_falls_back_to_identical_local_core(self) -> None:
        payload = {"taskId": "x", "inputHash": HASH}
        result = dispatch_optional_cloud(CloudPolicy(True, True), payload,
                                         lambda p: (_ for _ in ()).throw(ConnectionError()),
                                         lambda p: {"decision": "KEEP", "hash": p["inputHash"]})
        self.assertEqual(result["mode"], "local")
        self.assertEqual(result["result"], {"decision": "KEEP", "hash": HASH})


if __name__ == "__main__":
    unittest.main()