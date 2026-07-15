#!/usr/bin/env python3
"""Bounded archive inspection and extraction used at every trust boundary."""
from __future__ import annotations
import os,shutil,stat,tarfile,zipfile
from collections.abc import Iterable
from pathlib import Path,PurePosixPath

MAX_MEMBERS=20000;MAX_TOTAL_BYTES=800_000_000;MAX_MEMBER_BYTES=250_000_000;MAX_COMPRESSION_RATIO=500

def _path(name:str)->PurePosixPath:
 p=PurePosixPath(name)
 if not name or p.is_absolute() or '..' in p.parts or '\\' in name: raise ValueError(f'unsafe archive path: {name}')
 return p

def _collisions(names:list[str])->None:
 seen=set();folded=set()
 for name in names:
  clean=str(_path(name)).rstrip('/')
  if clean in seen or clean.casefold() in folded: raise ValueError(f'duplicate or case-fold collision: {name}')
  parts=PurePosixPath(clean).parts
  if any('/'.join(parts[:i]) in seen for i in range(1,len(parts))): raise ValueError(f'file/directory collision: {name}')
  seen.add(clean);folded.add(clean.casefold())

def _safe_link(member:str,target:str)->None:
 base=PurePosixPath(member).parent; resolved=base/PurePosixPath(target)
 if PurePosixPath(target).is_absolute() or '..' in resolved.parts: raise ValueError(f'escaping archive link: {member}')

def safe_extract_tar(archive:tarfile.TarFile,destination:Path,members:Iterable[tarfile.TarInfo]|None=None)->None:
 members=list(archive.getmembers() if members is None else members)
 if len(members)>MAX_MEMBERS: raise ValueError('tar member limit exceeded')
 _collisions([m.name for m in members]);total=0
 for m in members:
  if m.size>MAX_MEMBER_BYTES: raise ValueError('tar member size limit exceeded')
  total+=m.size
  if total>MAX_TOTAL_BYTES: raise ValueError('tar expanded-size limit exceeded')
  if m.ischr() or m.isblk() or m.isfifo() or m.isdev(): raise ValueError('special tar member rejected')
  if m.issym() or m.islnk(): _safe_link(m.name,m.linkname)
 destination.mkdir(parents=True,exist_ok=True)
 for m in members:
  target=destination/_path(m.name)
  if m.isdir(): target.mkdir(parents=True,exist_ok=True);target.chmod(m.mode&0o755);continue
  target.parent.mkdir(parents=True,exist_ok=True)
  if m.issym(): os.symlink(m.linkname,target);continue
  if m.islnk():
   source=destination/_path(m.linkname);os.link(source,target);continue
  stream=archive.extractfile(m)
  if stream is None: raise ValueError(f'missing tar payload: {m.name}')
  with target.open('wb') as out: shutil.copyfileobj(stream,out)
  target.chmod(m.mode&0o755)

def safe_extract_zip(archive:zipfile.ZipFile,destination:Path)->None:
 infos=archive.infolist()
 if len(infos)>MAX_MEMBERS: raise ValueError('ZIP member limit exceeded')
 _collisions([i.filename for i in infos]);total=0
 for info in infos:
  mode=(info.external_attr>>16)&0o170000
  if mode==stat.S_IFLNK: raise ValueError('ZIP symlink rejected')
  if info.file_size>MAX_MEMBER_BYTES: raise ValueError('ZIP member size limit exceeded')
  total+=info.file_size
  if total>MAX_TOTAL_BYTES: raise ValueError('ZIP expanded-size limit exceeded')
  if info.compress_size and info.file_size/info.compress_size>MAX_COMPRESSION_RATIO: raise ValueError('ZIP compression-ratio limit exceeded')
 destination.mkdir(parents=True,exist_ok=True)
 for info in infos:
  target=destination/_path(info.filename)
  if info.is_dir():target.mkdir(parents=True,exist_ok=True);continue
  target.parent.mkdir(parents=True,exist_ok=True)
  with archive.open(info) as source,target.open('wb') as out:shutil.copyfileobj(source,out)
  target.chmod(0o644)
