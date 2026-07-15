from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
from future_methodology import derive_token_usage, modeled_token_load, pricing_cost, requirement_contract_diagnostics, score_requirement_contract, calibrate_mutants, issue_diversity_preflight
from mutation_calibration import execute_all
from safe_archive import safe_extract_tar
from source_verification import build_envelope, subject_manifest, validate_envelope
from verification_checkers import AUTOMATED_IDS, CHECKER_MAP, evaluate_checker, validate_changes_table, write_changes_table
from vnext_fixture import run_fixture
from jsonschema import Draft202012Validator


class TokenAccountingV2Test(unittest.TestCase):
    def test_reasoning_is_subset_and_not_double_counted(self):
        usage=derive_token_usage({"input_tokens":100,"cached_input_tokens":60,"output_tokens":20,"reasoning_output_tokens":7})
        self.assertEqual(66,modeled_token_load(usage,.1)); self.assertEqual(13,usage["non_reasoning_output_tokens_observed"])
        self.assertTrue(usage["reasoning_is_subset_of_output"])
    def test_reasoning_above_output_fails(self):
        with self.assertRaisesRegex(ValueError,"subset"):derive_token_usage({"input_tokens":1,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":3})
    def test_pricing_output_is_charged_once(self):
        usage=derive_token_usage({"input_tokens":100,"cached_input_tokens":60,"cache_write_tokens":10,"output_tokens":20,"reasoning_output_tokens":7})
        self.assertEqual(30*2+10*3+60*1+20*4,pricing_cost(usage,uncached_input_price=2,cache_write_price=3,cached_input_price=1,output_price=4))
    def test_missing_cache_write_blocks_price_and_cross_arm_claim(self):
        usage=derive_token_usage({"input_tokens":1,"cached_input_tokens":0,"output_tokens":0,"reasoning_output_tokens":0})
        self.assertIsNone(pricing_cost(usage,uncached_input_price=1,cache_write_price=1,cached_input_price=1,output_price=1)); self.assertFalse(usage["cross_arm_cache_reuse_identifiable"])


class CorrectnessVNextHardeningTest(unittest.TestCase):
    def setUp(self):self.contract=json.loads((ROOT/"verification/vnext/contracts/issue-488.json").read_text())
    def cases(self):return {case:True for req in self.contract["requirements"] for case in req["protected_test_cases"]}
    def test_static_custom_score_is_rejected(self):
        contract=copy.deepcopy(self.contract); contract["requirements"][0]["pass_rule"]="custom"; contract["requirements"][0]["custom_score"]=1
        with self.assertRaises(ValueError):score_requirement_contract(contract,self.cases(),common_regression_score=100,common_regression_full_pass=True,trust_valid=True)
    def test_ranges_are_closed(self):
        for kwargs in ({"common_regression_score":101},{"patch_quality_score":-1},{"candidate_test_quality":101}):
            values={"common_regression_score":100,"common_regression_full_pass":True,"trust_valid":True}; values.update(kwargs)
            with self.assertRaises(ValueError):score_requirement_contract(self.contract,self.cases(),**values)
    def test_duplicate_and_unknown_evidence_fail(self):
        contract=copy.deepcopy(self.contract); contract["requirements"][1]["protected_test_cases"]=[contract["requirements"][0]["protected_test_cases"][0]]
        with self.assertRaisesRegex(ValueError,"multiple requirements"):score_requirement_contract(contract,self.cases(),common_regression_score=100,common_regression_full_pass=True,trust_valid=True)
        cases=self.cases(); cases["unknown"]=True
        with self.assertRaisesRegex(ValueError,"unknown protected"):score_requirement_contract(self.contract,cases,common_regression_score=100,common_regression_full_pass=True,trust_valid=True)
    def test_attainable_scores_not_requirement_count_heuristic(self):
        diagnostics=requirement_contract_diagnostics(self.contract); self.assertIn("attainable_requested_behavior_scores",diagnostics); self.assertNotEqual(100/3,diagnostics["score_granularity"])
    def test_unknown_mutant_and_not_calibrated_fail_closed(self):
        outcomes={mutant:{"materialized":True,"status":"killed"} for req in self.contract["requirements"] for mutant in req["mutants"]}; outcomes["unknown"]={"materialized":True,"status":"killed"}
        with self.assertRaisesRegex(ValueError,"unknown mutant"):calibrate_mutants(self.contract,outcomes)
        outcomes.pop("unknown"); first=next(iter(outcomes)); outcomes[first]={"materialized":False,"status":"planned_not_executable"}
        self.assertFalse(calibrate_mutants(self.contract,outcomes)["calibration_passed"])
    def test_executable_mutants_and_end_to_end_fixture(self):
        mutation=execute_all(ROOT); self.assertTrue(mutation["all_calibrated"]); self.assertEqual(9,mutation["materialized_mutants"])
        fixture=run_fixture(ROOT); self.assertEqual("passed",fixture["status"]); self.assertFalse(fixture["live_benchmark_authorized"])
    def test_five_uncalibrated_issues_cannot_support_broad_claim(self):
        skills=["localized_parsing","cross_file_behavior","dependency_call_chain","architecture_sensitive","test_diagnosis","configuration_build","negative_side_effect_safety"]
        issues=[{"issue_id":str(i),"historical_scores":[0,100],"expected_skill_dimensions":skills,"independent_behavior_case_count":3,"base_reference_discrimination":True,"mutant_detection":0,"cross_file_scope":True,"architecture_scope":True,"tool_relevance_scope":"fixture"} for i in range(5)]
        self.assertFalse(issue_diversity_preflight(issues)["broad_comparative_claims_supported"])


class SourceEnvelopeTest(unittest.TestCase):
    def make_repo(self):
        directory=tempfile.TemporaryDirectory(); repo=Path(directory.name); subprocess.run(["git","init","-q",str(repo)],check=True); subprocess.run(["git","-C",str(repo),"config","user.email","fixture@example.invalid"],check=True); subprocess.run(["git","-C",str(repo),"config","user.name","Fixture"],check=True)
        (repo/"runtime.py").write_text("VALUE=1\n"); subprocess.run(["git","-C",str(repo),"add","."],check=True); subprocess.run(["git","-C",str(repo),"commit","-qm","implementation"],check=True); return directory,repo
    def test_generated_report_delta_passes(self):
        holder,repo=self.make_repo(); self.addCleanup(holder.cleanup); reviewed=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip(); (repo/"verification").mkdir(); (repo/"verification/current-canonical-verification-report.json").write_text("{}\n"); subprocess.run(["git","-C",str(repo),"add","."],check=True); subprocess.run(["git","-C",str(repo),"commit","-qm","report"],check=True); report=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip(); envelope=build_envelope(repo,reviewed,report); self.assertTrue(envelope["subject_unchanged"]); self.assertFalse(validate_envelope(repo,envelope))
    def test_runtime_delta_fails(self):
        holder,repo=self.make_repo(); self.addCleanup(holder.cleanup); reviewed=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip(); (repo/"runtime.py").write_text("VALUE=2\n"); subprocess.run(["git","-C",str(repo),"commit","-qam","runtime"],check=True); report=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip();
        with self.assertRaisesRegex(ValueError,"unreviewed"):build_envelope(repo,reviewed,report)
    def test_manifest_mismatch_and_missing_commit_fail(self):
        holder,repo=self.make_repo(); self.addCleanup(holder.cleanup); commit=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip(); envelope=build_envelope(repo,commit,commit); envelope["verification_subject_tree_sha256"]="0"*64; self.assertTrue(validate_envelope(repo,envelope)); envelope["report_envelope_commit"]="f"*40; self.assertTrue(validate_envelope(repo,envelope))


class SafeTarTest(unittest.TestCase):
    def archive(self,name,type_=tarfile.REGTYPE,link=""):
        stream=io.BytesIO();
        with tarfile.open(fileobj=stream,mode="w") as archive:
            info=tarfile.TarInfo(name); info.type=type_; info.linkname=link; payload=b"ok"; info.size=len(payload) if type_==tarfile.REGTYPE else 0; archive.addfile(info,io.BytesIO(payload) if info.size else None)
        stream.seek(0); return stream
    def test_valid_and_malicious_tar_members(self):
        with tempfile.TemporaryDirectory() as directory:
            with tarfile.open(fileobj=self.archive("safe/file.txt")) as archive:safe_extract_tar(archive,Path(directory))
            self.assertEqual("ok",(Path(directory)/"safe/file.txt").read_text())
        for stream in (self.archive("../escape"),self.archive("/absolute"),self.archive("safe/link",tarfile.SYMTYPE,"../../escape"),self.archive("device",tarfile.CHRTYPE)):
            with tempfile.TemporaryDirectory() as directory, tarfile.open(fileobj=stream) as archive:
                with self.assertRaises(ValueError):safe_extract_tar(archive,Path(directory))


class CheckerCoverageTest(unittest.TestCase):
    def test_all_automated_registry_ids_have_unique_invoked_checkers(self):
        registry=json.loads((ROOT/"verification/verification-registry.json").read_text())["entries"]; automated={row["id"] for row in registry if row["kind"]=="automated"}; self.assertEqual(automated,set(CHECKER_MAP)); self.assertEqual(len(CHECKER_MAP),len({spec.checker_id for spec in CHECKER_MAP.values()}))
    def test_family_mutation_fails_only_selected_fact(self):
        for identifier in ("PUB-001","TOK-018","COR-017","COR-ISSUE-001","SRC-001","VER-001","SEC-002"):
            facts={key:True for key in AUTOMATED_IDS}; evidence={key:["fixture"] for key in AUTOMATED_IDS}; facts[identifier]=False
            self.assertEqual("failed",evaluate_checker(identifier,facts,evidence)["status"]); other=next(key for key in AUTOMATED_IDS if key!=identifier); self.assertEqual("passed",evaluate_checker(other,facts,evidence)["status"])
    def test_generated_changes_table_rejects_manual_area_change(self):
        registry=[{"id":"TOK-018","area":"tokens","why":"reason","kind":"automated","checker_id":"checker-tok-018","implementation":["x"],"test_files":["y"]}]; results=[{"verification_id":"TOK-018","status":"passed"}]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); write_changes_table(ROOT,registry,results,root); md=root/"verification-changes-table.md"; md.write_text(md.read_text().replace("| tokens |","| publication |")); self.assertTrue(validate_changes_table(registry,results,root/"verification-changes-table.json",md))


class StrictSchemaTest(unittest.TestCase):
    def test_every_schema_is_meta_valid(self):
        for path in sorted((ROOT/"schemas").glob("*.json")):
            with self.subTest(path=path.name): Draft202012Validator.check_schema(json.loads(path.read_text()))
    def test_vnext_contracts_validate(self):
        schema=json.loads((ROOT/"schemas/requirement-contract-vnext.schema.json").read_text()); validator=Draft202012Validator(schema)
        for path in sorted((ROOT/"verification/vnext/contracts").glob("*.json")):
            with self.subTest(path=path.name): self.assertEqual([],list(validator.iter_errors(json.loads(path.read_text()))))


if __name__=="__main__":unittest.main()
