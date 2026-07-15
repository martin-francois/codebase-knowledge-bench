from __future__ import annotations
import json,sys,unittest
from pathlib import Path
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from current_methodology import derive_token_usage,modeled_token_load,pricing_cost,score_requirement_contract,validate_requirement_contract,assess_mutation_readiness,issue_diversity_preflight
from methodology_fixture import run_fixture
from scorer_simulation import run_simulations

class CurrentMethodologyTest(unittest.TestCase):
 def setUp(self):self.contract=json.loads((ROOT/'verification/methodology-current/contracts/issue-488.json').read_text());self.cases={c:True for r in self.contract['requirements'] for c in r['protected_test_cases']}
 def score(self,cases=None,**kw):return score_requirement_contract(self.contract,cases or self.cases,common_regression_score=kw.get('common',100),common_regression_full_pass=kw.get('common_full',True),trust_valid=kw.get('trust',True),candidate_test_quality=kw.get('candidate',0),patch_quality_score=kw.get('patch',0))
 def test_reasoning_is_not_double_counted(self):
  u=derive_token_usage({'input_tokens':100,'cached_input_tokens':40,'cache_write_tokens':None,'output_tokens_including_reasoning':20,'reasoning_output_tokens':5});self.assertEqual(84,modeled_token_load(u,.1));self.assertEqual(120,u['total_reported_tokens']);self.assertEqual(15,u['non_reasoning_output_tokens'])
 def test_old_token_shape_rejected(self):
  with self.assertRaises(ValueError):derive_token_usage({'input_tokens':1,'cached_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0})
 def test_reasoning_subset_enforced(self):
  with self.assertRaises(ValueError):derive_token_usage({'input_tokens':1,'cached_input_tokens':0,'output_tokens_including_reasoning':1,'reasoning_output_tokens':2})
 def test_cache_null_and_pricing(self):
  u=derive_token_usage({'input_tokens':1,'cached_input_tokens':0,'cache_write_tokens':None,'output_tokens_including_reasoning':1,'reasoning_output_tokens':0});self.assertIsNone(u['cache_write_tokens']);self.assertIsNone(pricing_cost(u,uncached_input_price=1,cache_write_price=1,cached_input_price=1,output_price=1))
 def test_duplicate_case_rejected(self):
  self.contract['requirements'][1]['protected_test_cases']=self.contract['requirements'][0]['protected_test_cases']
  with self.assertRaises(ValueError):validate_requirement_contract(self.contract)
 def test_threshold_controls_pass(self):
  c=json.loads((ROOT/'verification/methodology-current/contracts/issue-486.json').read_text());cases={x:True for r in c['requirements'] for x in r['protected_test_cases']};cases['486-repeated-last']=False;s=score_requirement_contract(c,cases,common_regression_score=100,common_regression_full_pass=True,trust_valid=True);row=next(x for x in s['requirement_vector'] if x['id']=='repeated-role-options');self.assertEqual(.5,row['observed_fraction']);self.assertTrue(row['requirement_passed']);self.assertTrue(s['task_success'])
 def test_critical_failure_blocks(self):
  c=dict(self.cases);c['488-ambiguous-no-write']=False;self.assertFalse(self.score(c,patch=100)['task_success'])
 def test_candidate_tests_cannot_control(self):self.assertTrue(self.score(candidate=0)['task_success'])
 def test_no_scalar_composite(self):self.assertNotIn('composite_quality_score',self.score())
 def test_unknown_case_rejected(self):
  with self.assertRaises(ValueError):self.score({**self.cases,'unknown':True})
 def test_simulations_do_not_calibrate(self):self.assertFalse(run_simulations(ROOT)['real_mutation_calibration_available'])
 def test_simulation_outcome_rejected_by_mutation_readiness(self):
  outcomes={m:{'execution_kind':'scorer_simulation','status':'killed'} for r in self.contract['requirements'] for m in r['mutants']}
  with self.assertRaises(ValueError):assess_mutation_readiness(self.contract,outcomes)
 def test_infrastructure_is_not_killed(self):
  outcomes={m:{'execution_kind':'target_code','status':'infrastructure_error'} for r in self.contract['requirements'] for m in r['mutants']};self.assertFalse(assess_mutation_readiness(self.contract,outcomes)['ready'])
 def test_current_contract_schema(self):Draft202012Validator(json.loads((ROOT/'schemas/requirement-contract-current.schema.json').read_text())).validate(self.contract)
 def test_behavioral_fixture_and_readiness_block(self):
  d=run_fixture(ROOT);self.assertEqual('passed',d['status']);self.assertFalse(d['methodology_ready']);self.assertEqual('failed',run_fixture(ROOT,'protected_case')['status'])
 def test_diversity_requires_mutation(self):
  issues=[{'expected_skill_dimensions':list({'localized_parsing','cross_file_behavior','dependency_call_chain','architecture_sensitive','test_diagnosis','configuration_build','negative_side_effect_safety'}),'independent_behavior_case_count':3,'base_reference_discrimination':True,'mutant_detection':0,'unresolved_critical_contract_gap':False} for _ in range(5)];self.assertFalse(issue_diversity_preflight(issues)['broad_comparative_claims_supported'])
if __name__=='__main__':unittest.main()
