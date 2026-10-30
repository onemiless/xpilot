"""Launch a staged VPN core by held descriptors with reduced privileges."""
import os
import subprocess

from openpilot.tesla.network import UID


def _launch(executable, runtime, demote):
  executable_path = f'/proc/self/fd/{executable}'
  runtime_path = f'/proc/self/fd/{runtime}'
  return subprocess.Popen([executable_path, '-d', runtime_path, '-f', runtime_path + '/config.yaml'],
                          pass_fds=(executable, runtime), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=demote,
                          env={'PATH': '/usr/bin:/bin', 'HOME': runtime_path, 'USER': 'xpilot-vpn'})


def launch(executable, runtime):
  def demote():
    os.setgroups([])
    os.setgid(UID)
    os.setuid(UID)
  return _launch(executable, runtime, demote)
