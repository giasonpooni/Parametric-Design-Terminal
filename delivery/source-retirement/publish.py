"""Publish one checksum-bound native retirement branch without rewriting ancestry."""
from pathlib import Path
import hashlib,json,os,subprocess,sys
EXPECTED_TARGET='c7762d8ad632ac5e5ff4d021c3911fd29a3451ab'
EXPECTED_BASE='d49e6215db7bbce86b0ea0896afc679f0ed7b728'
EXPECTED_BUNDLE='7f00ab031d16ea6b08a01302f1c2e55aa38e2ad22191577364eb59146ded3f7a'
TARGET_BRANCH='feat/source-retirement-20261003'
PREFIX='delivery/source-retirement/'
def git(*args,capture=False):
 result=subprocess.run(['git','--no-replace-objects',*args],check=True,capture_output=capture,text=True)
 return result.stdout.strip() if capture else None
bundle=Path(PREFIX+'native-publication.bundle')
assert hashlib.sha256(bundle.read_bytes()).hexdigest()==EXPECTED_BUNDLE
catalog=json.loads(Path(PREFIX+'sources.json').read_text())
assert catalog['schema']=='notations.source-retirement.v1' and len(catalog['modules'])==23
# Capture read-only transport input before checkout removes its tracked files.
for module in catalog['modules']:
 repository='atomtrapping/'+module['repository'].split('/',1)[1]
 assert repository.startswith('atomtrapping/') and all(c.isalnum() or c in '/-_.' for c in repository)
 name=module['id'];prefix='refs/tags/retired/'+name
 git('fetch','--no-write-fetch-head','--no-tags','https://github.com/'+repository+'.git',
     '+refs/heads/*:'+prefix+'/heads/*','+refs/tags/*:'+prefix+'/tags/*','+refs/pull/*/head:'+prefix+'/pull/*/head')
 for ref in module['refs']:
  assert ref['retained_ref']=='refs/tags/retired/'+name+'/'+ref['ref'].removeprefix('refs/')
  object_id=ref['object'];assert len(object_id)==40 and all(c in '0123456789abcdef' for c in object_id)
  if subprocess.run(['git','cat-file','-e',object_id],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
   git('fetch','--no-write-fetch-head','--no-tags','https://github.com/'+repository+'.git',object_id)
  git('update-ref',ref['retained_ref'],object_id)
# Do not accidentally publish newly observed refs outside the qualified capture.
expected={ref['retained_ref'] for module in catalog['modules'] for ref in module['refs']}
for ref in git('for-each-ref','--format=%(refname)','refs/tags/retired',capture=True).splitlines():
 if ref not in expected:git('update-ref','-d',ref)
git('bundle','verify',str(bundle))
git('fetch',str(bundle),'refs/heads/'+TARGET_BRANCH+':refs/heads/native-retirement')
assert git('rev-parse','native-retirement',capture=True)==EXPECTED_TARGET
git('merge-base','--is-ancestor',EXPECTED_BASE,EXPECTED_TARGET)
git('checkout','--detach',EXPECTED_TARGET)
subprocess.run([sys.executable,'scripts/superrepo.py','audit'],check=True)
subprocess.run([sys.executable,'scripts/check_source_retirement.py','--require-retained-refs'],check=True)
subprocess.run([sys.executable,'-m','pytest','-q','tests/test_source_retirement.py','tests/test_local_provider_sources.py','tests/test_monorepo.py','tests/test_monorepo_schematics.py','scripts/tests/test_ci_policy.py'],check=True)
remote=subprocess.run(['git','ls-remote','--exit-code','origin','refs/heads/'+TARGET_BRANCH],capture_output=True,text=True)
assert remote.returncode in (0,2)
assert remote.returncode==2 or remote.stdout.split()[0]==EXPECTED_TARGET
# Native target and captured namespaced refs are pushed atomically, without force.
refspecs=[EXPECTED_TARGET+':refs/heads/'+TARGET_BRANCH,*sorted(expected)]
git('push','--atomic','origin',*refspecs)
assert git('ls-remote','origin','refs/heads/'+TARGET_BRANCH,capture=True).split()[0]==EXPECTED_TARGET
print('Published verified native retirement branch:',EXPECTED_TARGET)
