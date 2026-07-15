#!/usr/bin/env python3
"""Build and independently validate a portable exact-tree review handoff."""
from __future__ import annotations
import argparse,hashlib,json,mimetypes,re,subprocess,tarfile,tempfile,zipfile
from pathlib import Path
from typing import Any
from safe_archive import safe_extract_tar,safe_extract_zip

CANONICAL_SHA='b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0'
SUPPLEMENT_SHA='2b560a78410e47ee1cec4d9f000cfed4a0c633e6339cbc8c422ebee452bcb387'
PRE_CLEANUP_COMMIT='6631618f961a8f44b5a4743e0c378177f986a34b'
SOURCE_SCAN_ALLOWLIST={
 'docs/prompt-history-traceability.md':{'host-only path':'immutable historical provenance paths'},
 'docs/variant-synthesis.md':{'host-only path':'documented benchmark sandbox mount'},
 'scripts/run_benchmark.py':{'host-only path':'enforced benchmark sandbox and cache paths'},
 'scripts/validate_benchmark_run.py':{'host-only path':'validation of enforced sandbox paths'},
 'scripts/validate_published_archive.py':{'host-only path':'host-path rejection pattern'},
 'scripts/verification_checkers.py':{'host-only path':'negative scanner fixture','secret-shaped value':'negative scanner fixture'},
 'tests/test_anti_leak_cache_probes.py':{'host-only path':'anti-leak negative fixture'},
 'tests/test_harness.py':{'host-only path':'isolated temporary fixture path'},
 'tests/test_review_handoff.py':{'host-only path':'negative scanner fixture','secret-shaped value':'negative scanner fixture'},
}

def sha256_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def sha256_file(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def git(repo:Path,*args:str,raw:bool=False):return subprocess.check_output(['git','-C',str(repo),*args],text=not raw)
def canonical_root(entries:list[dict[str,Any]])->str:return sha256_bytes(json.dumps(entries,sort_keys=True,separators=(',',':')).encode())
def write_zip(z:zipfile.ZipFile,name:str,data:bytes)->None:
 info=zipfile.ZipInfo(name,date_time=(1980,1,1));info.external_attr=(0o100644&0xffff)<<16;info.compress_type=zipfile.ZIP_STORED if name.endswith(('.zip','.tar')) else zipfile.ZIP_DEFLATED;z.writestr(info,data)
def media(name:str)->str:return mimetypes.guess_type(name)[0] or 'application/octet-stream'

def ls_tree(repo:Path,commit:str)->list[dict[str,str]]:
 raw=git(repo,'ls-tree','-rz','--full-tree',commit,raw=True);rows=[]
 for record in raw.split(b'\0'):
  if not record:continue
  head,path=record.split(b'\t',1);mode,kind,oid=head.decode().split();rows.append({'mode':mode,'type':kind,'object_id':oid,'path':path.decode()})
 return rows

def reconstruct_tree(tar_bytes:bytes,expected:str)->dict[str,Any]:
 with tempfile.TemporaryDirectory() as td:
  root=Path(td)/'source';tar_path=Path(td)/'source.tar';tar_path.write_bytes(tar_bytes)
  with tarfile.open(tar_path) as archive:safe_extract_tar(archive,root)
  subprocess.run(['git','-C',str(root),'init','-q'],check=True);subprocess.run(['git','-C',str(root),'add','-A'],check=True)
  actual=git(root,'write-tree').strip()
 return {'expected_tree':expected,'reconstructed_tree':actual,'exact_match':actual==expected}

def scan_text(name:str,data:bytes)->list[str]:
 if b'\0' in data:return []
 text=data.decode('utf-8','ignore');errors=[]
 secret=re.compile(r'(?i)(?:api[_-]?key|access[_-]?token|password|private[_-]?key)\s*[:=]\s*[\'\"]?[A-Za-z0-9_\-/+=]{16,}')
 if secret.search(text):errors.append(f'secret-shaped value: {name}')
 if re.search(r'/(?:home|Users)/[^/\s]+/',text):errors.append(f'host-only path: {name}')
 return errors

def scan_source_text(name:str,data:bytes)->tuple[list[str],list[dict[str,str]]]:
 findings=scan_text(name,data);allowed=SOURCE_SCAN_ALLOWLIST.get(name,{})
 retained=[];exceptions=[]
 for finding in findings:
  category=finding.split(':',1)[0]
  if category in allowed:exceptions.append({'path':name,'category':category,'reason':allowed[category]})
  else:retained.append(finding)
 return retained,exceptions

def build(repo:Path,canonical:Path,supplement:Path,reports:Path,agent_response:Path,output:Path)->tuple[Path,dict[str,Any]]:
 if sha256_file(canonical)!=CANONICAL_SHA or sha256_file(supplement)!=SUPPLEMENT_SHA:raise ValueError('immutable evidence hash mismatch')
 commit=git(repo,'rev-parse','HEAD').strip();tree=git(repo,'rev-parse','HEAD^{tree}').strip();output.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as td:
  tar_path=Path(td)/'git-archive.tar';subprocess.run(['git','-C',str(repo),'archive','--format=tar','-o',str(tar_path),commit],check=True);tar_bytes=tar_path.read_bytes()
  tree_rows=ls_tree(repo,commit);reconstruction=reconstruct_tree(tar_bytes,tree)
  if not reconstruction['exact_match']:raise ValueError('Git tree reconstruction failed')
  required_reports=['current-verification-report.json','current-verification-report.md','llm-verification-report.json','llm-verification-report.md','checker-negative-coverage.json','test-results.json','test-results.md','command-log.txt','token-accounting-current.json','correctness-current.json','mutation-calibration.json','end-to-end-fixture.json','private-pre-release-cleanup.json','private-pre-release-cleanup.md','compatibility-term-classification.json','dead-code-report.json']
  missing=[name for name in required_reports if not (reports/name).is_file()]
  if missing:raise ValueError(f'missing generated reports: {missing}')
  payloads={
   'agent-response.md':agent_response.read_bytes(),'source/git-archive.tar':tar_bytes,
   'source/git-ls-tree.json':(json.dumps(tree_rows,indent=2,sort_keys=True)+'\n').encode(),
   'source/source-state.json':(json.dumps({'commit':commit,'tree':tree,'branch':git(repo,'branch','--show-current').strip()},indent=2,sort_keys=True)+'\n').encode(),
   'source/source-tree-reconstruction.json':(json.dumps(reconstruction,indent=2,sort_keys=True)+'\n').encode(),
   'source/full-diff.patch':git(repo,'diff','--binary',f'{PRE_CLEANUP_COMMIT}..{commit}',raw=True),
   'audit/pre-cleanup-independent-findings.json':(repo/'verification/pre-cleanup-independent-findings.json').read_bytes(),
   'audit/pre-cleanup-independent-findings.md':(repo/'verification/pre-cleanup-independent-findings.md').read_bytes(),
   'verification/verification-registry.json':(repo/'verification/verification-registry.json').read_bytes(),
   'verification/review-findings-ledger.json':(repo/'verification/review-findings-ledger.json').read_bytes(),
   'immutable-evidence/canonical-suite-bundle.zip':canonical.read_bytes(),
   'immutable-evidence/canonical-publication-supplement.zip':supplement.read_bytes(),
   'README.md':b'Private pre-release deterministic review handoff. Validate with the detached receipt and scripts/build_review_handoff.py.\n',
  }
  mapping={'private-pre-release-cleanup.json':'audit/private-pre-release-cleanup.json','private-pre-release-cleanup.md':'audit/private-pre-release-cleanup.md','compatibility-term-classification.json':'audit/compatibility-term-classification.json','dead-code-report.json':'audit/dead-code-report.json','token-accounting-current.json':'methodology/token-accounting-current.json','correctness-current.json':'methodology/correctness-current.json','mutation-calibration.json':'methodology/mutation-calibration.json','end-to-end-fixture.json':'methodology/end-to-end-fixture.json','test-results.json':'tests/test-results.json','test-results.md':'tests/test-results.md','command-log.txt':'tests/command-log.txt'}
  for name in required_reports:
   target=mapping.get(name,f'verification/{name}');payloads[target]=(reports/name).read_bytes()
  # Preserve the static published erratum from the prior supplement; it is never parsed by live runtime.
  with zipfile.ZipFile(supplement) as z:
   for name in ('token-accounting-erratum.json','token-accounting-erratum.md','token-accounting-corrected-effects.csv'):
    if name in z.namelist():payloads[f'immutable-evidence/canonical-{name}']=z.read(name)
  errors=[];source_scan_exceptions=[]
  for name,data in payloads.items():
   if name.endswith('.tar'):
    with tempfile.TemporaryDirectory() as scan_dir:
     t=Path(scan_dir)/'a.tar';t.write_bytes(data)
     with tarfile.open(t) as archive:
      for member in archive.getmembers():
       if member.isfile():
        stream=archive.extractfile(member);found,exceptions=scan_source_text(member.name,stream.read() if stream else b'');errors+=found;source_scan_exceptions+=exceptions
   elif not name.endswith('.zip'):errors+=scan_text(name,data)
  if errors:raise ValueError(f'handoff content scan failed: {errors[:10]}')
  entries=[{'path':name,'bytes':len(data),'sha256':sha256_bytes(data),'media_type':media(name),'role':name.split('/',1)[0],'source':'generated-or-content-addressed','required':True} for name,data in sorted(payloads.items())]
  manifest={'schema_id':'review-handoff-current','source_commit':commit,'source_tree':tree,'entries':entries,'manifest_root':canonical_root(entries),'source_scan_exceptions':source_scan_exceptions}
  zip_path=output/f'codebase-knowledge-graph-benchmark-private-review-{commit[:8]}.zip'
  with zipfile.ZipFile(zip_path,'w',allowZip64=True) as z:
   for name,data in sorted(payloads.items()):write_zip(z,name,data)
   write_zip(z,'review-handoff-manifest.json',(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode())
 validation=validate(zip_path)
 Path(str(zip_path)+'.sha256').write_text(f'{sha256_file(zip_path)}  {zip_path.name}\n')
 Path(str(zip_path)+'.validation.json').write_text(json.dumps(validation,indent=2,sort_keys=True)+'\n')
 if validation['status']!='passed':raise ValueError(validation['errors'])
 return zip_path,validation

def validate(zip_path:Path)->dict[str,Any]:
 errors=[]
 with tempfile.TemporaryDirectory() as td:
  root=Path(td)/'extract'
  with zipfile.ZipFile(zip_path) as z:safe_extract_zip(z,root)
  manifest=json.loads((root/'review-handoff-manifest.json').read_text());expected={x['path'] for x in manifest['entries']}|{'review-handoff-manifest.json'};actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
  if expected!=actual:errors.append('member set mismatch')
  for row in manifest['entries']:
   p=root/row['path']
   if not p.is_file() or p.stat().st_size!=row['bytes'] or sha256_file(p)!=row['sha256']:errors.append(f'manifest mismatch: {row["path"]}')
  if canonical_root(manifest['entries'])!=manifest['manifest_root']:errors.append('manifest root mismatch')
  if sha256_file(root/'immutable-evidence/canonical-suite-bundle.zip')!=CANONICAL_SHA:errors.append('canonical hash mismatch')
  if sha256_file(root/'immutable-evidence/canonical-publication-supplement.zip')!=SUPPLEMENT_SHA:errors.append('supplement hash mismatch')
  reconstruction=reconstruct_tree((root/'source/git-archive.tar').read_bytes(),manifest['source_tree'])
  if not reconstruction['exact_match']:errors.append('source tree mismatch')
  for p in root.rglob('*'):
   if p.is_file() and not p.name.endswith(('.zip','.tar')):errors+=scan_text(p.relative_to(root).as_posix(),p.read_bytes())
  mandatory={'agent-response.md','audit/pre-cleanup-independent-findings.json','audit/private-pre-release-cleanup.json','audit/compatibility-term-classification.json','audit/dead-code-report.json','methodology/token-accounting-current.json','methodology/correctness-current.json','methodology/mutation-calibration.json','methodology/end-to-end-fixture.json'}
  if not mandatory<=actual:errors.append('mandatory artifact missing')
 return {'schema_id':'review-handoff-validation-current','status':'passed' if not errors else 'failed','errors':errors,'zip_bytes':zip_path.stat().st_size,'zip_sha256':sha256_file(zip_path),'manifest_entry_count':len(manifest['entries']),'manifest_root':manifest['manifest_root'],'source_tree_reconstruction':reconstruction,'secret_and_host_path_scan':'passed' if not errors else 'failed'}

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--canonical',type=Path,required=True);p.add_argument('--supplement',type=Path,required=True);p.add_argument('--reports',type=Path,required=True);p.add_argument('--agent-response',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();path,result=build(a.repo.resolve(),a.canonical,a.supplement,a.reports,a.agent_response,a.output);print(json.dumps({'path':str(path),**result},indent=2,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
