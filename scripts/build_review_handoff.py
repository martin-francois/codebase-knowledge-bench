#!/usr/bin/env python3
"""Build and independently validate a portable external-review handoff ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from safe_archive import safe_extract_tar, safe_extract_zip
from source_verification import subject_manifest, validate_envelope

CANONICAL_SHA = "b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0"
SUPPLEMENT_SHA = "2b560a78410e47ee1cec4d9f000cfed4a0c633e6339cbc8c422ebee452bcb387"
REQUIRED_REPORTS = [
    "current-canonical-verification-report.json", "current-canonical-verification-report.md",
    "llm-verification-report.json", "llm-verification-report.md", "token-accounting-erratum.json",
    "token-accounting-erratum.md", "token-accounting-corrected-effects.csv",
    "verification-changes-table.json", "verification-changes-table.md", "vnext-readiness.json", "vnext-readiness.md",
]


def sha256_bytes(payload: bytes) -> str: return hashlib.sha256(payload).hexdigest()
def sha256_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):digest.update(block)
    return digest.hexdigest()


def canonical_root(entries: list[dict[str, Any]]) -> str:
    return sha256_bytes(json.dumps(entries,sort_keys=True,separators=(",",":")).encode())


def media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _zip_write(archive: zipfile.ZipFile, path: str, payload: bytes) -> None:
    info=zipfile.ZipInfo(path,date_time=(1980,1,1,0,0,0)); info.external_attr=(0o100644&0xFFFF)<<16
    info.compress_type=zipfile.ZIP_STORED if path.endswith(".zip") else zipfile.ZIP_DEFLATED
    archive.writestr(info,payload)


def _git(repo: Path,*args: str)->str:return subprocess.check_output(["git","-C",str(repo),*args],text=True).strip()


def build_handoff(repo: Path, canonical: Path, supplement: Path, output_dir: Path, agent_response: Path) -> tuple[Path,dict[str,Any]]:
    if sha256_file(canonical)!=CANONICAL_SHA or sha256_file(supplement)!=SUPPLEMENT_SHA: raise ValueError("immutable evidence hash mismatch")
    commit=_git(repo,"rev-parse","HEAD"); tree=_git(repo,"rev-parse","HEAD^{tree}"); short=commit[:8]
    envelope=json.loads((repo/"verification/source-verification-envelope.json").read_text())
    if validate_envelope(repo,envelope): raise ValueError("source verification envelope failed")
    required=[repo/"verification"/name for name in REQUIRED_REPORTS]
    missing=[str(path) for path in required if not path.is_file()]
    if missing: raise ValueError(f"required handoff reports missing: {missing}")
    zip_path=output_dir/f"codebase-knowledge-graph-benchmark-review-handoff-{short}.zip"
    output_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        temp=Path(directory); source=temp/"source"
        tar_path=temp/"source.tar"
        subprocess.run(["git","-C",str(repo),"archive","--format=tar","-o",str(tar_path),"HEAD"],check=True)
        with tarfile.open(tar_path) as archive:safe_extract_tar(archive,source)
        subject=subject_manifest(repo,commit)
        state={"commit":commit,"tree":tree,"branch":_git(repo,"branch","--show-current"),"verification_subject_tree_sha256":subject["verification_subject_tree_sha256"]}
        sources: list[tuple[str,bytes,str,str]]=[]
        for path in sorted(source.rglob("*")):
            if path.is_file():sources.append((f"source/git-archive/{path.relative_to(source).as_posix()}",path.read_bytes(),"tracked_source",f"git:{commit}"))
        generated={
            "source/source-state.json":json.dumps(state,indent=2,sort_keys=True).encode()+b"\n",
            "source/verification-subject-manifest.json":json.dumps(subject,indent=2,sort_keys=True).encode()+b"\n",
            "source/allowed-post-review-delta.patch":(repo/"verification/allowed-post-review-delta.patch").read_bytes(),
            "source/allowed-post-review-delta.json":(repo/"verification/allowed-post-review-delta.json").read_bytes(),
            "agent-response.md":agent_response.read_bytes(),
            "README.md":b"Portable deterministic review handoff. Validate with the detached receipt and scripts/build_review_handoff.py.\n",
        }
        payloads=sources+[(path,payload,"source_identity" if path.startswith("source/") else "review_response","repo-generated") for path,payload in generated.items()]
        payloads += [("immutable-evidence/canonical-suite-bundle.zip",canonical.read_bytes(),"immutable_evidence",f"sha256:{CANONICAL_SHA}"),("immutable-evidence/canonical-publication-supplement.zip",supplement.read_bytes(),"immutable_evidence",f"sha256:{SUPPLEMENT_SHA}")]
        payloads += [(f"reports/{path.name}",path.read_bytes(),"verification_report",f"repo://verification/{path.name}") for path in required]
        for name in ("test-results.json","test-results.md","ci-command-log.txt"):
            path=repo/"verification"/name; payloads.append((f"tests/{name}",path.read_bytes(),"test_evidence",f"repo://verification/{name}"))
        for name in ("verification-registry.json","review-findings-ledger.json"):
            path=repo/"verification"/name; payloads.append((f"registry/{name}",path.read_bytes(),"registry",f"repo://verification/{name}"))
        paths=[item[0] for item in payloads]
        if len(paths)!=len(set(paths)):raise ValueError("duplicate handoff path")
        entries=[{"path":path,"bytes":len(payload),"sha256":sha256_bytes(payload),"media_type":media_type(path),"role":role,"source":origin,"required":True} for path,payload,role,origin in sorted(payloads)]
        manifest={"schema_version":"review-handoff-manifest-v1","source_commit":commit,"source_tree":tree,"entries":entries,"manifest_root_sha256":canonical_root(entries)}
        with zipfile.ZipFile(zip_path,"w",allowZip64=True) as archive:
            for path,payload,_,_ in sorted(payloads):_zip_write(archive,path,payload)
            _zip_write(archive,"review-handoff-manifest.json",json.dumps(manifest,indent=2,sort_keys=True).encode()+b"\n")
    validation=validate_handoff(repo,zip_path)
    sha_path=Path(str(zip_path)+".sha256"); sha_path.write_text(f"{sha256_file(zip_path)}  {zip_path.name}\n")
    validation_path=Path(str(zip_path)+".validation.json"); validation_path.write_text(json.dumps(validation,indent=2,sort_keys=True)+"\n")
    if validation["status"]!="passed":raise ValueError(f"handoff validation failed: {validation['errors']}")
    return zip_path,validation


def _resolve_uri(extract: Path, uri: str) -> bool:
    if uri.startswith("repo://"):return (extract/"source/git-archive"/uri[7:]).is_file()
    if uri.startswith("zip://"):
        archive_name,separator,member=uri[6:].partition("!/")
        archive_path=extract/archive_name
        if not archive_path.is_file():return False
        if not separator:return True
        with zipfile.ZipFile(archive_path) as archive:return member in archive.namelist()
    return False


def validate_handoff(repo: Path, zip_path: Path) -> dict[str,Any]:
    errors=[]; secret_hits=[]; uri_count=0
    with tempfile.TemporaryDirectory() as directory:
        extract=Path(directory)/"extract"
        with zipfile.ZipFile(zip_path) as archive:safe_extract_zip(archive,extract)
        manifest=json.loads((extract/"review-handoff-manifest.json").read_text())
        expected={entry["path"] for entry in manifest["entries"]}|{"review-handoff-manifest.json"}
        actual={path.relative_to(extract).as_posix() for path in extract.rglob("*") if path.is_file()}
        if actual!=expected:errors.append("unexpected or missing ZIP members")
        for entry in manifest["entries"]:
            path=extract/entry["path"]
            if not path.is_file() or path.stat().st_size!=entry["bytes"] or sha256_file(path)!=entry["sha256"]:errors.append(f"manifest mismatch: {entry['path']}")
        if canonical_root(manifest["entries"])!=manifest["manifest_root_sha256"]:errors.append("handoff manifest root mismatch")
        state=json.loads((extract/"source/source-state.json").read_text())
        if state["tree"]!=manifest["source_tree"] or state["commit"]!=manifest["source_commit"]:errors.append("source identity mismatch")
        if sha256_file(extract/"immutable-evidence/canonical-suite-bundle.zip")!=CANONICAL_SHA:errors.append("canonical evidence mismatch")
        if sha256_file(extract/"immutable-evidence/canonical-publication-supplement.zip")!=SUPPLEMENT_SHA:errors.append("supplement evidence mismatch")
        for report in (extract/"reports").glob("*.json"):
            text=report.read_text(errors="replace")
            if "/home/" in text:errors.append(f"absolute host path in report: {report.name}")
            try:data=json.loads(text)
            except json.JSONDecodeError:continue
            stack=[data]
            while stack:
                value=stack.pop()
                if isinstance(value,dict):stack.extend(value.values())
                elif isinstance(value,list):stack.extend(value)
                elif isinstance(value,str) and value.startswith(("repo://","zip://")):
                    uri_count+=1
                    if not _resolve_uri(extract,value):errors.append(f"unresolved evidence URI: {value}")
        secret_pattern=re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|password|private[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{16,}")
        for root_name in ("reports","tests","registry"):
            for path in (extract/root_name).rglob("*"):
                if path.is_file() and path.stat().st_size<5_000_000:
                    if secret_pattern.search(path.read_text(errors="ignore")):secret_hits.append(path.relative_to(extract).as_posix())
        if secret_hits:errors.append("secret scan found credential-shaped values")
    return {"schema_version":"review-handoff-validation-v1","status":"passed" if not errors else "failed","zip_sha256":sha256_file(zip_path),"zip_bytes":zip_path.stat().st_size,"manifest_entry_count":len(manifest["entries"]),"manifest_root_sha256":manifest["manifest_root_sha256"],"evidence_uris_resolved":uri_count,"secret_scan":{"status":"passed" if not secret_hits else "failed","hits":secret_hits},"errors":errors}


def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[1]); parser.add_argument("--canonical",type=Path,required=True); parser.add_argument("--supplement",type=Path,required=True); parser.add_argument("--output-dir",type=Path,required=True); parser.add_argument("--agent-response",type=Path,required=True)
    args=parser.parse_args(); path,validation=build_handoff(args.repo.resolve(),args.canonical,args.supplement,args.output_dir,args.agent_response); print(json.dumps({"path":str(path),**validation},indent=2,sort_keys=True)); return 0


if __name__=="__main__":raise SystemExit(main())
