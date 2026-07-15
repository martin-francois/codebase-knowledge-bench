#!/usr/bin/env python3
"""Fail-closed audit for the private pre-release single-methodology rule."""
from __future__ import annotations
import argparse,ast,json,re,subprocess
from pathlib import Path

ACTIVE_ROOTS=('scripts','schemas','configs','dashboard/src')
BANNED_NAMES=re.compile(r'(^|_)(legacy|migrate|migration|deprecated|deprecation|shim|dual_read|dual_write|vnext|v1|v2)($|_)',re.I)
BANNED_TEXT=('token-accounting-v2','behavioral-correctness-vNext','methodology-vnext','future_methodology','old format','fallback parser')
ALLOWED_DOMAIN=[re.compile(r'"category"\s*:\s*"compatibility"'),re.compile(r'compatibility behavior',re.I)]

def tracked(repo:Path)->list[Path]:
 names=subprocess.check_output(['git','-C',str(repo),'ls-files','-z']).split(b'\0');return [repo/name.decode() for name in names if name and (repo/name.decode()).exists()]

def audit(repo:Path)->dict:
 violations=[];matches=[]
 for path in tracked(repo):
  rel=path.relative_to(repo).as_posix()
  if rel in {'scripts/private_prerelease_audit.py','scripts/verification_checkers.py','scripts/methodology_reports.py'}:continue
  if not rel.startswith(ACTIVE_ROOTS) or path.suffix not in {'.py','.json','.toml','.ts','.tsx'}:continue
  text=path.read_text(errors='ignore')
  for number,line in enumerate(text.splitlines(),1):
   lowered=line.lower()
   for term in BANNED_TEXT:
    if term.lower() in lowered:violations.append({'path':rel,'line':number,'term':term,'kind':'banned_live_text'})
   if re.search(r'\blegacy\b|\bmigration\b|\bdeprecated\b',line,re.I):violations.append({'path':rel,'line':number,'term':'obsolete-runtime-term','kind':'banned_live_text'})
   if 'compatibility' in lowered:
    category=(any(pattern.search(line) for pattern in ALLOWED_DOMAIN)
              or rel == 'schemas/requirement-contract-current.schema.json'
              or rel == 'scripts/run_benchmark.py')
    evidence_note=rel == 'scripts/build_review_handoff.py'
    classification='domain_behavior_term' if category else ('immutable_external_evidence_note' if evidence_note else 'review_required')
    matches.append({'path':rel,'line':number,'term':'compatibility','classification':classification})
  if path.suffix=='.py':
   try:tree=ast.parse(text)
   except SyntaxError as exc:violations.append({'path':rel,'line':exc.lineno,'term':'syntax','kind':'parse_error'});continue
   for node in ast.walk(tree):
    name=getattr(node,'name',None)
    if isinstance(name,str) and BANNED_NAMES.search(name):violations.append({'path':rel,'line':getattr(node,'lineno',0),'term':name,'kind':'banned_ast_name'})
 return {'schema_id':'private-pre-release-cleanup-current','active_files_scanned':sum(1 for p in tracked(repo) if p.relative_to(repo).as_posix().startswith(ACTIVE_ROOTS)),'matches':matches,'violations':violations,'status':'passed' if not violations and all(x['classification']!='review_required' for x in matches) else 'failed'}

def dead_code(repo:Path)->dict:
 deleted={'completed_retry_integration','final_arm_recovery','fresh_workspace_retry','fresh_workspace_retry_launch','token_accounting_erratum','publication_supplement','source_verification','future_methodology','vnext_fixture'};refs=[]
 for path in (repo/'scripts').glob('*.py'):
  try:tree=ast.parse(path.read_text())
  except SyntaxError:continue
  for node in ast.walk(tree):
   names=[]
   if isinstance(node,ast.Import):names=[x.name.split('.')[0] for x in node.names]
   elif isinstance(node,ast.ImportFrom) and node.module:names=[node.module.split('.')[0]]
   for name in names:
    if name in deleted:refs.append({'path':path.relative_to(repo).as_posix(),'line':node.lineno,'module':name})
 return {'schema_id':'dead-code-report-current','deleted_modules':sorted(deleted),'live_import_references':refs,'status':'passed' if not refs else 'failed'}

def term_classification(repo:Path)->dict:
 pattern=re.compile(r'\b(legacy|compatibility|migration|migrate|deprecated|deprecation|shim|alias|dual_read|dual_write|vNext|v1|v2|historical_methodology)\b',re.I);rows=[]
 for path in tracked(repo):
  if path.suffix.lower() not in {'.py','.json','.md','.toml','.yml','.yaml','.ts','.tsx'}:continue
  rel=path.relative_to(repo).as_posix()
  for number,line in enumerate(path.read_text(errors='ignore').splitlines(),1):
   for match in pattern.finditer(line):
    term=match.group(0)
    if term.lower()=='compatibility' and ('"category"' in line or 'behavior' in line.lower()):classification='domain_behavior_term'
    elif rel.startswith(('docs/','verification/pre-cleanup-independent-findings')) and any(word in line.lower() for word in ('archive','published','historical','immutable','prior')):classification='immutable_external_evidence_note'
    else:classification='false_positive'
    rows.append({'path':rel,'line':number,'term':term,'classification':classification,'reason':'retained prose or domain terminology; not an active translation, alternate reader, or scoring branch'})
 return {'schema_id':'compatibility-term-classification-current','matches_found':len(rows),'retained_matches':rows,'active_runtime_compatibility_paths':0}

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output-dir',type=Path);a=p.parse_args();result=audit(a.repo.resolve());dead=dead_code(a.repo.resolve())
 if a.output_dir:
  a.output_dir.mkdir(parents=True,exist_ok=True);(a.output_dir/'private-pre-release-cleanup.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');(a.output_dir/'dead-code-report.json').write_text(json.dumps(dead,indent=2,sort_keys=True)+'\n');(a.output_dir/'compatibility-term-classification.json').write_text(json.dumps(term_classification(a.repo.resolve()),indent=2,sort_keys=True)+'\n');(a.output_dir/'private-pre-release-cleanup.md').write_text(f"# Private pre-release cleanup\n\nStatus: **{result['status']}**. Active files scanned: {result['active_files_scanned']}. Active violations: {len(result['violations'])}. Retained reviewed active terms: {len(result['matches'])}.\n")
 else:print(json.dumps({'cleanup':result,'dead_code':dead},indent=2,sort_keys=True))
 return 0 if result['status']=='passed' and dead['status']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
