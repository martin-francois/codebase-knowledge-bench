from __future__ import annotations
import io,sys,tarfile,tempfile,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from private_prerelease_audit import audit,dead_code
from safe_archive import safe_extract_tar,safe_extract_zip
class PrivateCleanupTest(unittest.TestCase):
 def test_no_live_compatibility_paths(self):self.assertEqual('passed',audit(ROOT)['status'],audit(ROOT))
 def test_deleted_modules_have_no_imports(self):self.assertEqual('passed',dead_code(ROOT)['status'])
 def test_zip_traversal_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.zip'
   with zipfile.ZipFile(p,'w') as z:z.writestr('../x','x')
   with zipfile.ZipFile(p) as z:
    with self.assertRaises(ValueError):safe_extract_zip(z,Path(d)/'out')
 def test_zip_duplicate_casefold_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.zip'
   with zipfile.ZipFile(p,'w') as z:z.writestr('A','x');z.writestr('a','x')
   with zipfile.ZipFile(p) as z:
    with self.assertRaises(ValueError):safe_extract_zip(z,Path(d)/'out')
 def test_tar_escape_link_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.tar'
   with tarfile.open(p,'w') as t:
    i=tarfile.TarInfo('link');i.type=tarfile.SYMTYPE;i.linkname='../../x';t.addfile(i)
   with tarfile.open(p) as t:
    with self.assertRaises(ValueError):safe_extract_tar(t,Path(d)/'out')
 def test_tar_special_file_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.tar'
   with tarfile.open(p,'w') as t:i=tarfile.TarInfo('device');i.type=tarfile.CHRTYPE;t.addfile(i)
   with tarfile.open(p) as t:
    with self.assertRaises(ValueError):safe_extract_tar(t,Path(d)/'out')
if __name__=='__main__':unittest.main()
