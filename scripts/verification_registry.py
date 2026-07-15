#!/usr/bin/env python3
"""Validate and execute the private pre-release verification registry."""
from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
from verification_checkers import CHECKERS,run

def load(repo:Path):return json.loads((repo/'verification/verification-registry.json').read_text())['entries']
def validate(repo:Path)->list[str]:
 entries=load(repo);errors=[];ids=[x['id'] for x in entries]
 if len(ids)!=len(set(ids)):errors.append('duplicate IDs')
 automated={x['id'] for x in entries if x['kind']=='automated'}
 if automated!=set(CHECKERS):errors.append(f'checker coverage mismatch: missing={sorted(automated-set(CHECKERS))} extra={sorted(set(CHECKERS)-automated)}')
 for row in entries:
  for field in ('id','title','area','invariant','why','kind','implementation','positive_fixture','negative_fixture','commands','output_artifacts','failure_severity','status'):
   if field not in row:errors.append(f'{row.get("id")}: missing {field}')
  for path in row.get('implementation',[])+row.get('positive_fixture',[])+row.get('negative_fixture',[]):
   if not (repo/path).exists():errors.append(f'{row["id"]}: missing path {path}')
 return errors
def report(repo:Path)->dict:
 entries=load(repo);errors=validate(repo);automated=[x['id'] for x in entries if x['kind']=='automated'];checks=run(repo,automated) if not errors else [];counts=Counter(x['kind'] for x in entries);fail=[x['id'] for x in checks if x['status']!='passed'];return {'schema_id':'current-verification-report','source_commit':__import__('subprocess').check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),'counts':{'automated':counts['automated'],'llm_manual':counts['llm_manual'],'external_capability':counts['external_capability'],'total':len(entries)},'checks':checks,'registry_errors':errors,'failures':fail,'status':'passed' if not errors and not fail else 'failed','model_calls':0,'child_processes':0}
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('command',choices=['validate','run']);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output',type=Path);a=p.parse_args();d={'status':'passed' if not validate(a.repo.resolve()) else 'failed','errors':validate(a.repo.resolve())} if a.command=='validate' else report(a.repo.resolve());text=json.dumps(d,indent=2,sort_keys=True)+'\n';a.output.write_text(text) if a.output else print(text,end='');return 0 if d['status']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
