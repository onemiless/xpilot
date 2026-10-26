#!/usr/bin/env python3
"""X-58 migration acceptance, extended to the broker (no device; isolated Params/plugins/IPC).

Failure cases written before implementation:
- Three or more publishers displace each other, mix source/kind/raw payload or lose samples.
- Forged source, undeclared kind, unknown PID, malformed/truncated datagrams reach cereal.
- Oversize, >100 Hz, >256 KiB/s/source or >1 MiB/s total pass; floods starve another source.
- Repeated excess spams faults, or restarting the broker repeats the same drive's fault.
- A dead broker blocks senders; manager fails to restart it or stale socket prevents recovery.
- TTL, TTL=0, latest(kind, source), sequence or original send time is lost.

Run on Linux for real SO_PASSCRED/SCM_CREDENTIALS. Darwin uses the documented test-only fallback.
Prints repeatable JSON evidence; assertions fail the check. Derived from task/X-20261026-58.
"""
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'opendbc_repo')]
from openpilot.cereal import custom, messaging


def sender(connection):
  from openpilot.tesla import plugins, plugin_msgd
  while (command := connection.recv()) is not None:
    source, kind, payload, ttl, raw = command
    if raw:
      data = {"source": source, "kind": kind, "payload": json.dumps(payload), "validForMs": ttl,
              "logMonoTime": time.monotonic_ns() - (2_000_000_000 if raw == "expired" else 0),
              "pid": os.getppid() if sys.platform == "linux" else os.getpid()}
      with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        try:
          sock.sendto(b"{" if raw == "malformed" else json.dumps(data).encode(), plugin_msgd.socket_path())
          sent = True
        except OSError:
          sent = False
    else:
      plugin = SimpleNamespace(NAME=source, CLAIMS=(f"msg:{kind}",))
      # Both spellings must work without a PubMaster in any sender.
      sent = plugins.send_msg(None, plugin, kind, payload, ttl)
    connection.send(sent)


def wait_for(test, timeout=5):
  end = time.monotonic() + timeout
  while not (result := test()):
    assert time.monotonic() < end, "timed out"
    time.sleep(.002)
  return result


def measure():
  from openpilot.common.params import Params
  from openpilot.system.manager.process import ensure_running
  from openpilot.system.manager.process_config import managed_processes
  from openpilot.tesla import plugins, plugin_msgd
  broker = managed_processes['plugin_msgd']
  assert broker.should_run(False, None, None) and broker.should_run(True, None, None)
  params = Params()
  ctx = multiprocessing.get_context('spawn')
  workers, pipes = [], []
  pm = messaging.PubMaster(['managerState', 'deviceState'])
  reader = plugins.PluginMsgs()
  capture = messaging.sub_sock('pluginMsg', conflate=False)
  events = []
  drive = 123456789

  def status():
    state = messaging.new_message('managerState', valid=True)
    entries = [{'pid': p.pid, 'name': f'source{i}d', 'running': True} for i, p in enumerate(workers[:4])]
    # The test runner itself hosts source4 in card, but not the other sources.
    state.managerState.processes = entries + [{'pid': os.getpid(), 'name': 'card', 'running': True}]
    pm.send('managerState', state)
    state = messaging.new_message('deviceState', valid=True)
    state.deviceState.started, state.deviceState.startedMonoTime = True, drive
    pm.send('deviceState', state)

  def collect():
    events.extend(messaging.drain_sock(capture))

  def send(i, kind, payload, ttl=750, source=None, raw=False):
    pipes[i].send((source or f'source{i}', kind, payload, ttl, raw))
    assert pipes[i].poll(5), 'sender blocked'
    result = pipes[i].recv()
    collect()
    return result

  def fresh(kind, source, payload):
    collect()
    return reader.fresh(kind).get(source) == payload

  try:
    for _ in range(5):
      parent, child = ctx.Pipe()
      process = ctx.Process(target=sender, args=(child,))
      process.start()
      workers.append(process)
      pipes.append(parent)
    ensure_running([broker], False, params, None)
    wait_for(lambda: Path(plugin_msgd.socket_path()).exists())
    for _ in range(10):
      status()
      time.sleep(.02)

    # msgq resets reader registrations when its sole publisher starts; attach before the first burst.
    reader.fresh('_ready')
    collect()
    traffic = custom.TeslaTrafficControl.new_message().to_dict()
    traffic['rawPayload'] = '0123456789abcdef'
    payloads = [traffic, custom.TeslaBattery.new_message().to_dict(), custom.TeslaTrafficControlPlan.new_message().to_dict(), {'external': 'in+out'}]
    for sample in range(20):
      expected = [{**payloads[i], 'sample': sample} for i in range(4)]
      for i, payload in enumerate(expected):
        pipes[i].send((f'source{i}', 'demo.data', payload, 750, False))
      for pipe in pipes[:4]:
        assert pipe.poll(5) and pipe.recv(), 'concurrent sender failed or blocked'
      for i, payload in enumerate(expected):
        try:
          wait_for(lambda i=i, payload=payload: fresh('demo.data', f'source{i}', payload))
        except AssertionError as error:
          raise AssertionError((sample, i, reader.last)) from error
      time.sleep(.011)
    assert reader.latest('demo.data', 'source0') == {**traffic, 'sample': 19}
    assert reader.latest('absent', 'source0') is None
    assert reader.fresh('demo.data', time.monotonic_ns() + 1_000_000_000) == {}
    assert send(0, 'demo.forever', ['no expiry'], 0)
    wait_for(lambda: fresh('demo.forever', 'source0', ['no expiry']))
    assert reader.fresh('demo.forever', time.monotonic_ns() + 10**12) == {'source0': ['no expiry']}
    collect()
    assert all(e.pluginMsg.seq > 0 and e.pluginMsg.source.startswith('source') for e in events)
    for i in range(4):
      seq = [e.pluginMsg.seq for e in events if e.pluginMsg.source == f'source{i}']
      assert seq == sorted(set(seq)), seq
    delivered = len(events)

    # Actual SCM credentials identify worker0, even when JSON lies about its PID/source.
    assert send(0, 'demo.forged', 'forged', source='source1', raw=True)
    assert send(4, 'demo.forged', 'unknown PID', raw=True)
    assert send(0, 'undeclared', 'bad kind', raw=True)
    assert send(1, 'demo.oversize', 'x' * (16 * 1024 + 1), raw=True)
    time.sleep(.05)
    assert reader.fresh('demo.forged') == {} and reader.fresh('undeclared') == {} and reader.fresh('demo.oversize') == {}
    faults = params.get(plugins.FAULTS_PARAM)
    assert 'kind' in faults['source0'] and 'payload' in faults['source1'], faults
    assert send(3, 'demo.data', None, raw='malformed')
    assert send(3, 'demo.oversize', 'x' * 140000, raw=True)
    assert send(3, 'demo.expired', 'stale', 1, raw='expired')
    assert send(3, 'demo.data', {'after': 'invalid'})
    wait_for(lambda: fresh('demo.data', 'source3', {'after': 'invalid'}))
    assert not reader.fresh('demo.expired')
    first_fault = faults['source0']
    assert send(0, 'demo.oversize', 'x' * (16 * 1024 + 1), raw=True)
    time.sleep(.05)
    assert params.get(plugins.FAULTS_PARAM)['source0'] == first_fault, 'fault spam'

    # Reset the drive/window, then prove both source and total byte caps and fair shares.
    drive += 1
    status()
    time.sleep(1.05)
    events.clear()
    for sample in range(120):
      assert send(2, 'demo.rate', sample)
      time.sleep(.001)
    time.sleep(.03)
    collect()
    assert 0 < len([e for e in events if e.pluginMsg.kind == 'demo.rate']) <= 100
    assert 'rate' in params.get(plugins.FAULTS_PARAM)['source2']
    time.sleep(1.05)
    events.clear()
    # Each source has an equal reservation; one flood cannot consume a peer's budget.
    for i in range(4):
      for sample in range(30):
        send(i, 'demo.bytes', {'sample': sample, 'blob': 'x' * 16000})
        time.sleep(.001)
    # Fifth source is hosted by card; verifies the new API without the legacy pm argument.
    p = SimpleNamespace(NAME='source4', CLAIMS=('msg:demo.bytes',))
    for sample in range(30):
      plugins.send_msg(p, 'demo.bytes', {'sample': sample, 'blob': 'x' * 16000}, 750)
      collect()
      time.sleep(.002)
    time.sleep(.03)
    collect()
    byte_counts = {f'source{i}': sum(len(e.as_builder().to_bytes()) for e in events
                   if e.pluginMsg.source == f'source{i}' and e.pluginMsg.kind == 'demo.bytes') for i in range(5)}
    assert all(0 < n <= 256 * 1024 for n in byte_counts.values()), byte_counts
    assert sum(byte_counts.values()) <= 1024 * 1024, byte_counts
    assert max(byte_counts.values()) - min(byte_counts.values()) < 17000, byte_counts

    broker.proc.kill()
    broker.proc.join(5)
    start = time.monotonic()
    results = [send(0, 'demo.down', i) for i in range(100)]
    down_elapsed = time.monotonic() - start
    assert not any(results) and down_elapsed < 2, (results, down_elapsed)
    ensure_running([broker], True, params, None)
    status()
    wait_for(lambda: broker.proc.is_alive() and Path(plugin_msgd.socket_path()).exists())
    time.sleep(.1)
    status()
    def recovered():
      status()
      send(0, 'demo.recovered', 'yes')
      return fresh('demo.recovered', 'source0', 'yes')
    wait_for(recovered)
    fault_before = params.get(plugins.FAULTS_PARAM)['source0']
    assert send(0, 'undeclared_again', 'no', raw=True)
    time.sleep(.05)
    assert params.get(plugins.FAULTS_PARAM)['source0'] == fault_before, 'restart repeated drive fault'
    return {'credential_mode': 'SCM_CREDENTIALS' if sys.platform == 'linux' else 'Darwin test-only PID',
            'concurrent_publishers': 4, 'samples_delivered': delivered, 'fair_wire_bytes': byte_counts,
            'forgery_rejected': True, 'limits_and_fault_dedup': True, 'broker_restarted_by_manager': True,
            'down_100_sends_s': down_elapsed, 'ttl_and_latest': True, 'migration_ready': True}
  finally:
    broker.stop()
    for pipe in pipes:
      pipe.send(None)
    for process in workers:
      process.join(5)
      if process.is_alive():
        process.kill()
        process.join()
      assert process.exitcode == 0, process.exitcode


def run():
  with tempfile.TemporaryDirectory(prefix='x62-') as tmp:
    root = Path(tmp)
    os.environ['OPENPILOT_PREFIX'] = root.name
    os.environ['PARAMS_ROOT'] = str(root / 'params')
    os.environ['XPILOT_PLUGINS_DIR'] = str(root / 'plugins')
    kinds = ('data', 'forever', 'forged', 'oversize', 'rate', 'bytes', 'down', 'recovered', 'expired')
    for i in range(5):
      path = root / 'plugins' / f'source{i}'
      path.mkdir(parents=True)
      (path / '__init__.py').write_text('')
      (path / 'plugin.py').write_text('from openpilot.tesla.plugin import Plugin\nclass P(Plugin):\n  pass\n')
      manifest = {'id': f'source{i}', 'name': f'Source {i}', 'version': '1.0.0', 'api': 1,
                  'category': 'tools', 'use': 'both', 'entry': 'plugin:P', 'enable_param': None,
                  'claims': [f'process:source{i}d'] + [f'msg:demo.{k}' for k in kinds]}
      (path / 'plugin.json').write_text(json.dumps(manifest))
    shm = Path('/tmp' if sys.platform == 'darwin' else '/dev/shm') / ('msgq_' + root.name)
    shm.mkdir()
    try:
      result = measure()
      print(json.dumps(result, indent=2))
    finally:
      import shutil
      shutil.rmtree(shm)
      shutil.rmtree('/tmp/xpilot-plugin-msg-' + root.name, ignore_errors=True)


if __name__ == '__main__':
  run()
