"""Failure: client timeout leaves a privileged gateway process running."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from openpilot.tesla import network

with patch.object(network.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}))) as run:
  network.NetworkVPN('test').request('status')
  args = run.call_args.args[0]
  assert '/usr/bin/timeout' in args, 'no root-side timeout'
  assert run.call_args.kwargs.get('start_new_session') is True
  assert run.call_args.kwargs['timeout'] > 15
print('gateway requests use bounded root-side timeout and dedicated session: PASS')
