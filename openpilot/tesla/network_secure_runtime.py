"""Experimental dirfd-based staging; caller owns returned directory descriptor."""
import os
import secrets
import stat


def stage_config(control, data, *, control_owner=0, runtime_owner=61400):
  if not isinstance(data, bytes) or not 0 < len(data) <= 2 * 1024 * 1024:
    raise ValueError('invalid config size')
  flags=os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
  parent=os.open(control, flags)
  try:
    info=os.fstat(parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=control_owner or info.st_mode & 0o022:
      raise PermissionError('untrusted control directory')
    created=False
    try:
      os.mkdir('runtime',0o700,dir_fd=parent)
      created=True
    except FileExistsError:
      pass
    runtime=os.open('runtime',flags,dir_fd=parent)
    try:
      if created:
        os.fchown(runtime,runtime_owner,-1)
        os.fchmod(runtime,0o700)
      info=os.fstat(runtime)
      if not stat.S_ISDIR(info.st_mode) or info.st_uid!=runtime_owner or stat.S_IMODE(info.st_mode)!=0o700:
        raise PermissionError('untrusted runtime directory')
      tmp='.config-'+secrets.token_hex(12)
      fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=runtime)
      try:
        with os.fdopen(fd,'wb') as f:
          os.fchown(f.fileno(),runtime_owner,-1)
          f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,'config.yaml',src_dir_fd=runtime,dst_dir_fd=runtime)
        os.fsync(runtime)
      except BaseException:
        try: os.unlink(tmp,dir_fd=runtime)
        except FileNotFoundError: pass
        raise
      return runtime
    except BaseException:
      os.close(runtime)
      raise
  finally:
    os.close(parent)
