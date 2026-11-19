"""Build an allowlisted, credential-free Windows trial ZIP from verified cached inputs.

Prepare Windows wheels with uv pip compile --python-platform windows --python-version
3.13 and pip download --platform win_amd64 --python-version 313 --only-binary=:all:.
Never copies a development venv, .env, .local data, private spreadsheets or screenshots.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT=Path(__file__).resolve().parents[1]
INPUTS={
 'python.zip':('https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip','9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'),
 'postgres.zip':('https://get.enterprisedb.com/postgresql/postgresql-18.7-1-windows-x64-binaries.zip','ead0bbc27fb678fa24dcf86b738981323042abd073c744103bf7f3aed23f9a33'),
}


def digest(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def extract_file(archive,member,target):
    relative=PurePosixPath(member)
    if relative.is_absolute() or '..' in relative.parts or '\\' in member:
        raise ValueError('Unsafe archive path')
    destination=target.joinpath(*relative.parts)
    if member.endswith('/'): destination.mkdir(parents=True,exist_ok=True)
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(archive.read(member))


def validate_wheels(wheels):
    packages={}
    for wheel in wheels:
        with ZipFile(wheel) as archive:
            metadata=next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            data=BytesParser().parsebytes(archive.read(metadata))
            packages[canonicalize_name(data['Name'])]=data
            tag=wheel.stem.rsplit('-',1)[1]
            if tag not in ('any','win_amd64'): raise ValueError('Non-Windows wheel: '+wheel.name)
    env=default_environment()
    env.update(sys_platform='win32',os_name='nt',platform_system='Windows',platform_machine='AMD64',
        python_version='3.13',python_full_version='3.13.17',implementation_version='3.13.17')
    missing=[]
    for name,metadata in packages.items():
        for raw in metadata.get_all('Requires-Dist',[]):
            requirement=Requirement(raw)
            extras=['','binary'] if name=='psycopg' else ['']
            if requirement.marker and not any(requirement.marker.evaluate(dict(env,extra=x)) for x in extras): continue
            other=packages.get(canonicalize_name(requirement.name))
            if other is None or Version(other['Version']) not in requirement.specifier:
                missing.append(name+' -> '+str(requirement))
    if missing: raise ValueError('Incomplete Windows dependency closure: '+ '; '.join(missing))
    return {name:metadata['Version'] for name,metadata in sorted(packages.items())}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--cache',type=Path,default=ROOT/'.local/windows-build')
    parser.add_argument('--output',type=Path,default=ROOT/'.local/releases')
    args=parser.parse_args()
    for name,(_,expected) in INPUTS.items():
        if digest(args.cache/name)!=expected: raise ValueError('Runtime checksum mismatch: '+name)
    wheels=sorted((args.cache/'wheels').glob('*.whl'))
    versions=validate_wheels(wheels)
    args.output.mkdir(parents=True,exist_ok=True)
    name='UnionBakery-Windows-x64-Trial-2026.11.19'
    bundle=args.output/name
    if bundle.exists(): raise ValueError('Output already exists; use a new output directory.')
    bundle.mkdir()
    with ZipFile(args.cache/'python.zip') as archive:
        for member in archive.namelist(): extract_file(archive,member,bundle/'runtime/python')
    (bundle/'runtime/python/python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../../app\nimport site\n',encoding='utf-8')
    site=bundle/'runtime/python/Lib/site-packages'
    for wheel in wheels:
        with ZipFile(wheel) as archive:
            for member in archive.namelist():
                parts=PurePosixPath(member).parts
                if parts and parts[0].endswith('.data'):
                    if len(parts)<3 or parts[1] not in ('purelib','platlib'): continue
                    target=site.joinpath(*parts[2:]);target.parent.mkdir(parents=True,exist_ok=True)
                    if not member.endswith('/'): target.write_bytes(archive.read(member))
                else: extract_file(archive,member,site)
    with ZipFile(args.cache/'postgres.zip') as archive:
        for member in archive.namelist():
            parts=PurePosixPath(member).parts
            if not parts or parts[0]!='pgsql': raise ValueError('Unexpected PostgreSQL archive layout')
            if len(parts)>1 and (parts[1] in ('bin','lib','share') or len(parts)==2 and not member.endswith('/')):
                target=bundle/'runtime/postgres'
                relative='/'.join(parts[1:]) + ('/' if member.endswith('/') else '')
                if member.endswith('/'): target.joinpath(*parts[1:]).mkdir(parents=True,exist_ok=True)
                else:
                    destination=target.joinpath(*parts[1:]);destination.parent.mkdir(parents=True,exist_ok=True)
                    destination.write_bytes(archive.read(member))
    app=bundle/'app';app.mkdir()
    for directory in ('core','ui','views','ai_entry'):
        for path in (ROOT/directory).rglob('*'):
            if path.suffix not in ('.py','.css') or '__pycache__' in path.parts: continue
            target=app/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for filename in ('app.py','config.py','requirements.txt','.streamlit/config.toml',
        'scripts/__init__.py','scripts/windows_local.py','scripts/production_database.py','scripts/backup_database.py',
        'database/001_schema_baseline.sql','database/002_add_attendance.sql','database/003_calculation_history.sql',
        'database/004_ai_entry.sql','database/005_production.sql'):
        target=app/filename;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/filename,target)
    for filename in ('preflight.py','install_runtime.ps1','使用说明.txt'):
        shutil.copy2(ROOT/'packaging/windows'/filename,bundle/filename)
    prefix='@echo off\nchcp 65001 >nul\nsetlocal DisableDelayedExpansion\ncd /d "%~dp0"\n'
    for filename,action in [('01-启动工资网站.cmd','start'),('02-停止工资网站.cmd','stop'),
                            ('03-备份工资数据.cmd','backup'),('04-设置千问AI.cmd','ai')]:
        content=prefix+'"runtime\\python\\python.exe" -X utf8 preflight.py\nif errorlevel 1 goto finish\n'
        content+='"runtime\\python\\python.exe" -X utf8 -m scripts.windows_local '+action+'\n:finish\npause\n'
        (bundle/filename).write_bytes(content.replace('\n','\r\n').encode('utf-8'))
    (bundle/'00-补充运行库.cmd').write_bytes((prefix+
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_runtime.ps1"\npause\n').replace('\n','\r\n').encode('utf-8'))
    (bundle/'05-打开备份目录.cmd').write_bytes((prefix+
        'explorer.exe "%LOCALAPPDATA%\\UnionBakeryPayrollTrial\\backups"\n').replace('\n','\r\n').encode('utf-8'))
    shutil.copy2(args.cache/'requirements.lock',bundle/'windows-requirements.lock')
    manifest=dict(release=name,qualification='pending-windows-machine-acceptance',schema_version=5,
        python='3.13.17',postgres='18.7',packages=versions,
        upstream={k:dict(url=v[0],sha256=v[1]) for k,v in INPUTS.items()},
        wheels={w.name:digest(w) for w in wheels},
        files={str(p.relative_to(bundle)).replace('\\','/'):digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()})
    (bundle/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with ZipFile(args.output/(name+'.zip'),'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(bundle.rglob('*')):
            if path.is_file(): archive.write(path,Path(name)/path.relative_to(bundle))
    artifact=args.output/(name+'.zip')
    (args.output/(name+'.sha256')).write_text(digest(artifact)+'  '+artifact.name+'\n')
    print('Built:',artifact)
    print('Size MB:',round(artifact.stat().st_size/1024/1024,1),'Windows packages:',len(wheels))


if __name__=='__main__': main()
