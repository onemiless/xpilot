"""Artifact preparation: hostile paths, digest mismatch and descriptor cleanup.

Pure temporary test; no physical CAN, privileged routing or live VPN service.
"""
import hashlib,os,pathlib,shutil,tempfile
from openpilot.tesla.network_artifacts import _prepare

with tempfile.TemporaryDirectory() as t:
 root=pathlib.Path(t); source=root/'source'; control=root/'control'; binary=root/'binary'
 for p in (source,control,binary): p.mkdir(mode=0o700)
 conf=source/'config.yaml'; conf.write_text('proxies: [{name: test, type: vmess, server: 192.0.2.1, port: 443, uuid: fixture, cipher: auto, alterId: 0}]\n'); conf.chmod(0o600)
 secret=source/'secret'; secret.write_text('fixture'); secret.chmod(0o600)
 core=binary/'mihomo'; shutil.copyfile('/bin/true',core); core.chmod(0o700)
 expected=hashlib.sha256(core.read_bytes()).hexdigest(); uid=os.getuid()
 corefd,runfd=_prepare(source,control,binary,expected,uid,uid,uid)
 try:
  fd=os.open('config.yaml',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=runfd)
  try:
   output=os.read(fd,200000).decode(); assert '127.0.0.1:17891' in output and '127.0.0.1:17893' in output
  finally: os.close(fd)
 finally: os.close(corefd); os.close(runfd)
 conf.unlink(); conf.symlink_to('/etc/passwd')
 try: _prepare(source,control,binary,expected,uid,uid,uid)
 except (OSError,ValueError,PermissionError): pass
 else: raise AssertionError('symlinked config admitted')
 conf.unlink(); conf.write_text('proxies: [{name: test, type: vmess, server: 192.0.2.1, port: 443, uuid: fixture, cipher: auto, alterId: 0}]\n'); conf.chmod(0o600)
 secret.write_bytes(b'\xffprivate-secret'); secret.chmod(0o600)
 try: _prepare(source,control,binary,expected,uid,uid,uid)
 except ValueError as error:
  assert type(error) is ValueError and str(error)=='invalid VPN secret', 'unsafe error detail'
 else: raise AssertionError('invalid secret admitted')
 secret.write_text('fixture'); secret.chmod(0o600)
 conf.unlink(); conf.write_text('proxies: []\n'); conf.chmod(0o600)
 try: _prepare(source,control,binary,'0'*64,uid,uid,uid)
 except (OSError,ValueError,PermissionError): pass
 else: raise AssertionError('unapproved core admitted')
print('fixed artifact preparation and invalid input rejection: PASS')


