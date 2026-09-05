from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import unittest

from dna_midi_studio.end_to_end_arranger import (
    STAGES, ArrangerWorkflowError, apply_project_action, build_project_checkpoint,
    build_song_to_style_project, execute_end_to_end_api, execute_end_to_end_batch,
    execute_end_to_end_gui, invalidate_downstream, partial_regenerate_fragment,
    redo_project_action, resume_project, serialize_end_to_end_chain,
    undo_project_action, validate_song_to_style_project,
)
from dna_midi_studio.session35_fixture import build_session35_chain

ROOT = Path(__file__).resolve().parents[1]


class Session35Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = build_session35_chain(ROOT)
        cls.project = cls.chain["project"]
        cls.bundle = serialize_end_to_end_chain(cls.chain)


BASE = [
    lambda s: s.project["schema"] == "dna-song-to-style-project",
    lambda s: s.project["version"] == "1.0",
    lambda s: s.project["projectId"].startswith("project-"),
    lambda s: len(s.project["projectHash"]) == 64,
    lambda s: len(s.project["stages"]) == 10,
    lambda s: [x["stageId"] for x in s.project["stages"]] == list(STAGES),
    lambda s: all(x["status"] == "COMPLETE" for x in s.project["stages"]),
    lambda s: s.project["workflow"]["progressPercent"] == 100,
    lambda s: s.project["workflow"]["standardTaskWithoutTerminal"],
    lambda s: s.project["workflow"]["downstreamOnlyInvalidation"],
    lambda s: s.project["workflow"]["batchIsolation"],
    lambda s: s.project["publication"]["previewDownloadAllowed"],
    lambda s: not s.project["publication"]["finalCertifiedExportAllowed"],
    lambda s: s.project["publication"]["allowedProductName"] == "AI PREMIUM ARRANGER PREVIEW",
    lambda s: s.project["publication"]["humanListening"] == "PENDING_0_OF_2",
    lambda s: s.project["publication"]["physicalPa800"] == "WAITING_FOR_DEVICE",
    lambda s: len(s.project["errors"]) == 8,
    lambda s: all(x["failClosed"] for x in s.project["errors"]),
    lambda s: not any(s.project["safety"].values()),
    lambda s: s.project["source"]["midiSha256"] == sha256(s.chain["sourceBytes"]).hexdigest(),
    lambda s: s.project["hashChain"]["renderManifestHash"] == s.chain["renderManifest"]["renderManifestHash"],
    lambda s: s.project["hashChain"]["coherencePlanHash"] == s.chain["coherencePlan"]["coherencePlanHash"],
    lambda s: s.project["hashChain"]["previewMidiSha256"] == sha256(s.chain["coherentVariants"]["C"]).hexdigest(),
    lambda s: s.project["history"]["cursor"] == 0,
    lambda s: not s.project["history"]["undoAvailable"],
    lambda s: s.project["recovery"]["checkpointAvailable"],
    lambda s: s.project["recovery"]["lastConfirmedStage"] == "PUBLISH",
    lambda s: execute_end_to_end_api({"action":"build","sourceMidiBase64":base64.b64encode(s.chain["sourceBytes"]).decode(),"chain":s.bundle,"controls":{"selectedVariantId":"C","lockedMarkers":[],"projectSeed":3535,"previewTier":"PREVIEW_ONLY"}}) == s.project,
    lambda s: execute_end_to_end_gui({"action":"build","sourceMidiBase64":base64.b64encode(s.chain["sourceBytes"]).decode(),"chain":s.bundle,"controls":{"selectedVariantId":"C","lockedMarkers":[],"projectSeed":3535,"previewTier":"PREVIEW_ONLY"}}) == s.project,
    lambda s: validate_song_to_style_project(s.project) is None,
]

for i, callback in enumerate(BASE, 1):
    def test(self, callback=callback): self.assertTrue(callback(self))
    setattr(Session35Tests, f"test_{i:03d}_base", test)


def _stage_check(index, kind):
    def check(self):
        row = self.project["stages"][index]
        if kind == 0: self.assertEqual(row["index"], index)
        elif kind == 1: self.assertEqual(row["status"], "COMPLETE")
        elif kind == 2: self.assertEqual(len(row["artifactHash"]), 64)
        else: self.assertEqual(len(row["stageHash"]), 64)
    return check

counter = 30
for index in range(10):
    for kind in range(4):
        counter += 1; setattr(Session35Tests, f"test_{counter:03d}_stage_{index}_{kind}", _stage_check(index, kind))


def _project_check(seed, kind):
    def check(self):
        project = build_song_to_style_project(self.chain["sourceBytes"], self.chain,
            {"selectedVariantId":"C","lockedMarkers":[],"projectSeed":seed,"previewTier":"PREVIEW_ONLY"})
        if kind == 0:
            validate_song_to_style_project(project); self.assertEqual(len(project["projectHash"]), 64)
        else:
            self.assertEqual(project["workflow"]["completedStages"], list(STAGES)); self.assertEqual(project["workflow"]["progressPercent"], 100)
    return check

for seed in range(3500, 3550):
    for kind in range(2):
        counter += 1; setattr(Session35Tests, f"test_{counter:03d}_project_{seed}_{kind}", _project_check(seed, kind))


def _source_fail(self):
    with self.assertRaises(ArrangerWorkflowError): build_song_to_style_project(self.chain["sourceBytes"]+b"x",self.chain,{})
def _invalidation(self):
    x=invalidate_downstream(self.project,"BRIEF","1"*64); self.assertEqual([r["status"] for r in x["stages"][:3]],["COMPLETE"]*3); self.assertTrue(all(r["status"]=="INVALIDATED" for r in x["stages"][3:]))
def _undo_redo(self):
    a=apply_project_action(self.project,{"type":"LOCK_MARKER","value":"v1cv1"}); b=undo_project_action(a); c=redo_project_action(b); self.assertEqual(c["controls"]["lockedMarkers"],["v1cv1"])
def _checkpoint(self):
    cp=build_project_checkpoint(self.project,"RENDER"); r=resume_project(self.project,cp); self.assertEqual(r["lastConfirmedStageHash"],cp["lastConfirmedStageHash"])
def _partial(self):
    x=partial_regenerate_fragment(self.project,self.chain["coherentVariants"],self.chain["coherencePlan"],self.chain["renderManifest"],"v1cv1","guitar","B"); self.assertTrue(x["report"]["outsideFragmentUnchanged"]); self.assertGreater(x["report"]["changedMidiEvents"],0)
def _batch(self):
    good={"action":"build","sourceMidiBase64":base64.b64encode(self.chain["sourceBytes"]).decode(),"chain":self.bundle,"controls":{"selectedVariantId":"C","lockedMarkers":[],"projectSeed":3535,"previewTier":"PREVIEW_ONLY"}}; result=execute_end_to_end_batch([good,{}]); self.assertEqual([x["status"] for x in result],["PASS","BLOCKED"]); self.assertTrue(result[1]["error"]["failClosed"])

for name, callback in (("source_fail",_source_fail),("invalidation",_invalidation),("undo_redo",_undo_redo),("checkpoint",_checkpoint),("partial",_partial),("batch",_batch)):
    counter += 1; setattr(Session35Tests,f"test_{counter:03d}_{name}",callback)

assert counter == 176

if __name__ == "__main__": unittest.main()