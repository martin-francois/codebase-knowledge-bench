from __future__ import annotations
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from verification_registry import load,validate,report
from verification_checkers import CHECKERS
class VerificationRegistryTest(unittest.TestCase):
 def test_registry_is_complete(self):self.assertEqual([],validate(ROOT))
 def test_kind_counts_are_honest(self):
  r=report(ROOT);self.assertEqual({'automated':50,'llm_manual':15,'external_capability':1,'total':66},r['counts'])
 def test_every_automated_id_has_checker(self):self.assertEqual({x['id'] for x in load(ROOT) if x['kind']=='automated'},set(CHECKERS))
 def test_every_checker_executes(self):self.assertEqual('passed',report(ROOT)['status'])
 def test_missing_checker_fails(self):
  key=next(iter(CHECKERS));value=CHECKERS.pop(key)
  try:self.assertIn('coverage mismatch',' '.join(validate(ROOT)))
  finally:CHECKERS[key]=value
 def test_registry_schema(self):
  from jsonschema import Draft202012Validator
  Draft202012Validator(json.loads((ROOT/'schemas/verification-registry.schema.json').read_text())).validate(json.loads((ROOT/'verification/verification-registry.json').read_text()))
if __name__=='__main__':unittest.main()
