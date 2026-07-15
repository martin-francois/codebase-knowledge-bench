from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
import build_review_handoff as handoff


class ReviewHandoffTest(unittest.TestCase):
    def test_portable_manifest_extracts_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); canonical=root/"canonical.zip"; supplement=root/"supplement.zip"
            with zipfile.ZipFile(canonical,"w") as archive:archive.writestr("suite-results.json","{}")
            with zipfile.ZipFile(supplement,"w") as archive:archive.writestr("operator-summary.json","{}")
            original=(handoff.CANONICAL_SHA,handoff.SUPPLEMENT_SHA); handoff.CANONICAL_SHA=handoff.sha256_file(canonical); handoff.SUPPLEMENT_SHA=handoff.sha256_file(supplement); self.addCleanup(lambda:setattr(handoff,"CANONICAL_SHA",original[0])); self.addCleanup(lambda:setattr(handoff,"SUPPLEMENT_SHA",original[1]))
            payloads={
                "source/source-state.json":json.dumps({"commit":"a"*40,"tree":"b"*40}).encode(),
                "source/git-archive/AGENTS.md":b"instructions\n", "agent-response.md":b"exact response\n",
                "immutable-evidence/canonical-suite-bundle.zip":canonical.read_bytes(),
                "immutable-evidence/canonical-publication-supplement.zip":supplement.read_bytes(),
                "reports/report.json":b'{"evidence":"repo://AGENTS.md"}\n', "tests/test-results.json":b"{}\n", "registry/verification-registry.json":b"{}\n",
            }
            entries=[{"path":path,"bytes":len(value),"sha256":hashlib.sha256(value).hexdigest(),"media_type":"application/octet-stream","role":"fixture","source":"fixture","required":True} for path,value in sorted(payloads.items())]
            manifest={"schema_version":"review-handoff-manifest-v1","source_commit":"a"*40,"source_tree":"b"*40,"entries":entries,"manifest_root_sha256":handoff.canonical_root(entries)}
            target=root/"handoff.zip"
            with zipfile.ZipFile(target,"w") as archive:
                for path,value in payloads.items():handoff._zip_write(archive,path,value)
                handoff._zip_write(archive,"review-handoff-manifest.json",json.dumps(manifest).encode())
            validation=handoff.validate_handoff(ROOT,target); self.assertEqual("passed",validation["status"],validation); self.assertEqual(len(entries),validation["manifest_entry_count"])

    def test_absolute_host_report_path_is_rejected(self):
        text=Path(ROOT/"scripts/build_review_handoff.py").read_text(); self.assertIn('if "/home/" in text',text)

    def test_agent_response_is_required_member(self):
        text=Path(ROOT/"scripts/build_review_handoff.py").read_text(); self.assertIn('"agent-response.md":agent_response.read_bytes()',text)

    def test_published_repo_evidence_uris_resolve_to_files(self):
        report=json.loads((ROOT/"verification/current-canonical-verification-report.json").read_text())
        for check in report["checks"]:
            for uri in check["evidence"]:
                if uri.startswith("repo://"):
                    self.assertTrue((ROOT/uri[7:]).is_file(), f"non-file evidence URI: {uri}")


if __name__=="__main__":unittest.main()
