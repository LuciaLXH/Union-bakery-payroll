"""Fetch only pinned official runtime archives; no application or payroll credentials."""
from pathlib import Path
import urllib.request
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.build_windows_bundle import ROOT, INPUTS, digest

cache=ROOT/'.local/windows-build';cache.mkdir(parents=True,exist_ok=True)
for name,(url,expected) in INPUTS.items():
    path=cache/name
    if not path.exists() or digest(path)!=expected:
        with urllib.request.urlopen(url,timeout=120) as response,path.open('wb') as output:
            while data:=response.read(1024*1024): output.write(data)
    if digest(path)!=expected: raise ValueError('Runtime checksum mismatch: '+name)
    print('Verified runtime:',name)
