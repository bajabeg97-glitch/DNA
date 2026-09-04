from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    AtomicMidiPublisher, CancelToken, TransactionIdentity, publish_batch, safe_output_stem,
)


CONTENT = b"MThd-session10-validated-fixture"
HASH_A, HASH_B, HASH_C, HASH_D = (char * 64 for char in "abcd")


class Session10AtomicExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.publisher = AtomicMidiPublisher(self.root)
        self.identity = TransactionIdentity(HASH_A, HASH_B, HASH_C)
        self.verifier = lambda data: {"passed": data == CONTENT, "checkedHash": sha256(data).hexdigest()}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_safe_name_removes_windows_forbidden_characters(self) -> None:
        self.assertEqual(safe_output_stem('bad<>:"/\\|?*name.mid'), "____name")

    def test_safe_name_preserves_unicode(self) -> None:
        self.assertEqual(safe_output_stem("Željko pjesma.mid"), "Željko pjesma")

    def test_safe_name_avoids_windows_reserved_names(self) -> None:
        self.assertEqual(safe_output_stem("CON.mid"), "_CON")

    def test_safe_name_limits_long_paths(self) -> None:
        self.assertLessEqual(len(safe_output_stem("a" * 400 + ".mid")), 120)

    def test_identity_requires_exact_sha256(self) -> None:
        with self.assertRaisesRegex(ValueError, "three SHA-256"):
            TransactionIdentity("bad", HASH_B, HASH_C)

    def test_empty_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.publisher.publish(b"", "song.mid", self.identity, self.verifier)

    def test_valid_candidate_is_atomically_committed(self) -> None:
        result = self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        self.assertEqual(result.status, "COMMITTED")
        self.assertEqual(result.output_path.read_bytes(), CONTENT)

    def test_success_leaves_no_temp_or_lock_file(self) -> None:
        self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        self.assertFalse(list(self.root.glob("*.tmp")))
        self.assertFalse(list(self.root.glob("*.lock")))

    def test_validator_failure_blocks_publication(self) -> None:
        result = self.publisher.publish(CONTENT, "song.mid", self.identity, lambda data: {"passed": False})
        self.assertEqual(result.status, "BLOCKED")
        self.assertFalse((self.root / "song_OPT.mid").exists())

    def test_validator_exception_rolls_back_temp(self) -> None:
        def broken(data):
            raise RuntimeError("verifier crashed")
        with self.assertRaisesRegex(RuntimeError, "verifier crashed"):
            self.publisher.publish(CONTENT, "song.mid", self.identity, broken)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_disk_full_simulation_leaves_no_partial_output(self) -> None:
        def disk_full(path, content):
            path.write_bytes(content[:4])
            raise OSError("disk full")
        publisher = AtomicMidiPublisher(self.root, writer=disk_full)
        with self.assertRaisesRegex(OSError, "disk full"):
            publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        self.assertFalse((self.root / "song_OPT.mid").exists())
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_existing_lock_blocks_second_writer(self) -> None:
        (self.root / "song_OPT.mid.lock").write_text("busy")
        with self.assertRaisesRegex(RuntimeError, "locked"):
            self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)

    def test_cancel_before_write_creates_no_output(self) -> None:
        token = CancelToken(); token.cancel("test-cancel")
        result = self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier, token)
        self.assertEqual(result.status, "CANCELLED")
        self.assertFalse((self.root / "song_OPT.mid").exists())

    def test_cancel_after_temp_write_rolls_back(self) -> None:
        token = CancelToken()
        def writer(path, content):
            path.write_bytes(content); token.cancel("after-write")
        result = AtomicMidiPublisher(self.root, writer=writer).publish(CONTENT, "song.mid", self.identity, self.verifier, token)
        self.assertEqual(result.status, "CANCELLED")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_matching_hashes_resume_completed_output(self) -> None:
        self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        resumed = self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        self.assertTrue(resumed.resumed)

    def test_changed_config_hash_forces_new_transaction(self) -> None:
        self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        changed = TransactionIdentity(HASH_A, HASH_D, HASH_C)
        result = self.publisher.publish(CONTENT, "song.mid", changed, self.verifier)
        self.assertFalse(result.resumed)

    def test_corrupt_existing_output_cannot_resume(self) -> None:
        first = self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        first.output_path.write_bytes(b"corrupt")
        result = self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        self.assertFalse(result.resumed)
        self.assertEqual(result.output_path.read_bytes(), CONTENT)

    def test_journal_records_all_resume_hashes(self) -> None:
        result = self.publisher.publish(CONTENT, "song.mid", self.identity, self.verifier)
        state = json.loads(result.journal_path.read_text())
        self.assertEqual((state["sourceHash"], state["configHash"], state["databaseHash"]),
                         (HASH_A, HASH_B, HASH_C))
        self.assertFalse(state["partialOutput"])

    def test_traversal_like_source_name_stays_inside_output_directory(self) -> None:
        result = self.publisher.publish(CONTENT, "../../escape.mid", self.identity, self.verifier)
        self.assertEqual(result.output_path.parent, self.root.resolve())

    def test_batch_reports_progress_for_every_file(self) -> None:
        snapshots = []
        jobs = [(CONTENT, "one.mid", self.identity, self.verifier),
                (CONTENT, "two.mid", TransactionIdentity(HASH_D, HASH_B, HASH_C), self.verifier)]
        progress = publish_batch(self.publisher, jobs, on_progress=lambda item: snapshots.append(item.completed))
        self.assertEqual((progress.total, progress.completed, progress.committed, snapshots), (2, 2, 2, [1, 2]))

    def test_batch_keeps_per_file_failure_and_continues(self) -> None:
        jobs = [(CONTENT, "one.mid", self.identity, lambda data: (_ for _ in ()).throw(OSError("locked"))),
                (CONTENT, "two.mid", TransactionIdentity(HASH_D, HASH_B, HASH_C), self.verifier)]
        progress = publish_batch(self.publisher, jobs)
        self.assertEqual((progress.failed, progress.committed, progress.completed), (1, 1, 2))

    def test_batch_cancel_marks_remaining_files_without_writing(self) -> None:
        token = CancelToken(); token.cancel("stop-batch")
        jobs = [(CONTENT, "one.mid", self.identity, self.verifier),
                (CONTENT, "two.mid", TransactionIdentity(HASH_D, HASH_B, HASH_C), self.verifier)]
        progress = publish_batch(self.publisher, jobs, token)
        self.assertEqual((progress.cancelled, progress.completed), (2, 2))
        self.assertFalse(list(self.root.glob("*.mid")))


if __name__ == "__main__":
    unittest.main()