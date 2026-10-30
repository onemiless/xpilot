"""Root-owned routing resources for the fixed reviewed IPv4 VPN profile."""
import ipaddress
import json
import os
from pathlib import Path
import pwd
import secrets
import shlex
import socket
import subprocess

from openpilot.tesla.network import CONTROL, DEV, MARK, OWNER, ROOT, TABLE, UID, lease, owner, parked, require_owner, run
from openpilot.tesla.network_gate import mutation_gate

IPT = 'iptables-legacy'
CHAINS = (('mangle', 'C3MH_MARK', 'OUTPUT'), ('nat', 'C3MH_OUTDNS', 'OUTPUT'), ('filter', 'C3MH_INPUT', 'INPUT'))


def run_parked(*args):
  with mutation_gate():
    parked()
    return run(*args, timeout=.5)


def journaled(key, value, *args):
  with mutation_gate():
    parked()
    state = owner()
    if state is None or state.get('pending') is not None:
      raise RuntimeError('VPN recovery required before further mutation')
    if not resource_absent(key, value):
      raise RuntimeError('VPN resource already exists')
    # Persist the intended resource before the kernel operation. Cleanup can
    # then recover when this process dies after the command succeeds.
    remember('pending', {'key': key, 'value': value})
    try:
      run(*args, timeout=.5)
    except subprocess.CalledProcessError:
      # A reported create failure does not grant ownership of a competing resource.
      remember('pending', None)
      raise
    try:
      remember(key, value)
    except BaseException:
      # The command returned success, so undo exactly that operation if its
      # ownership record could not be committed. Hard process death is separate.
      if key == 'interface':
        undo = ('ip', 'link', 'del', DEV)
      elif key == 'default_route':
        undo = ('ip', 'route', 'del', *args[3:])
      elif key == 'chains':
        undo = (IPT, '-t', args[2], '-X', args[4])
      elif key == 'hooks':
        undo = (IPT, '-t', args[2], '-D', *args[4:])
      elif key == 'rules':
        undo = ('ip', 'rule', 'del', *args[3:])
      else:
        raise RuntimeError('No rollback for network operation') from None
      run(*undo, timeout=.5)
      raise
    remember('pending', None)


def chain_marker(table, chain, marker):
  result = run(IPT, '-t', table, '-S', chain, check=False, timeout=.5)
  if result.returncode:
    return False
  expected = ['-A', chain, '-m', 'comment', '--comment', marker]
  return any(shlex.split(line) == expected for line in result.stdout.splitlines())


def journaled_chain(value):
  table, target = value[-1]
  with mutation_gate():
    parked()
    state = owner()
    if state is None or state.get('pending') is not None:
      raise RuntimeError('VPN recovery required before further mutation')
    if not resource_absent('chains', value):
      raise RuntimeError('VPN chain already exists')
    suffix = secrets.token_hex(10)
    temporary = 'XP_' + suffix
    marker = 'xpilot:' + suffix
    if run(IPT, '-t', table, '-S', temporary, check=False, timeout=.5).returncode != 1:
      raise RuntimeError('VPN temporary chain already exists')
    remember('pending', {'key': 'chains', 'value': value, 'temporary': temporary, 'marker': marker})
    run(IPT, '-t', table, '-N', temporary, timeout=.5)
    run(IPT, '-t', table, '-A', temporary, '-m', 'comment', '--comment', marker, timeout=.5)
    run(IPT, '-t', table, '-E', temporary, target, timeout=.5)
    remember('chains', value)
    tokens = dict(owner().get('chain_tokens', {}))
    tokens[f'{table}/{target}'] = marker
    remember('chain_tokens', tokens)
    remember('pending', None)


def resource_absent(key, value):
  if key == 'interface':
    return run('ip', 'link', 'show', DEV, check=False, timeout=.5).returncode == 1
  if key == 'default_route':
    rows = json.loads(run('ip', '-j', 'route', 'show', 'table', 'all', timeout=.5).stdout)
    return not any(str(row.get('table')) == TABLE and row.get('dst', 'default') == 'default' for row in rows)
  if key == 'chains':
    table, chain = value[-1]
    if run(IPT, '-t', table, '-S', check=False, timeout=.5).returncode:
      return False
    return run(IPT, '-t', table, '-S', chain, check=False, timeout=.5).returncode == 1
  if key == 'hooks':
    rule = value[-1]
    result = run(IPT, *rule[:2], '-S', rule[2], check=False, timeout=.5)
    return result.returncode == 0 and not any(shlex.split(line) == ['-A', *rule[2:]] for line in result.stdout.splitlines())
  if key == 'rules':
    rows = json.loads(run('ip', '-j', 'rule', timeout=.5).stdout)
    return not any(row.get('priority') == 17880 for row in rows)
  raise ValueError('Unknown journaled resource')


def remember(key, value):
  state = owner()
  if state is None:
    raise PermissionError('Missing VPN ownership record')
  state[key] = value
  pending = OWNER.with_suffix('.pending')
  pending.write_text(json.dumps(state))
  os.replace(pending, OWNER)


def hotspot():
  data = json.loads(run('ip', '-j', '-4', 'addr', 'show', 'dev', 'wlan0').stdout)
  for dev in data:
    for address in dev.get('addr_info', []):
      if address.get('local') == '192.168.43.1':
        return str(ipaddress.ip_network(f"{address['local']}/{address['prefixlen']}", strict=False))
  return None


def reserve(provider):
  previous = owner()
  if previous is not None:
    require_owner(previous, provider)
    if previous['lease'] != lease():
      raise PermissionError('Previous VPN generation must be cleaned first')
    return
  # Refuse collisions before changing anything; never adopt an existing interface.
  if run('ip', 'link', 'show', DEV, check=False).returncode == 0:
    raise RuntimeError('VPN interface already exists')
  if run('ip', 'route', 'show', 'table', TABLE, check=False).stdout.strip():
    raise RuntimeError('VPN routing table already exists')
  rules = json.loads(run('ip', '-j', 'rule').stdout)
  if any(r.get('priority') in (17880, 17890) or str(r.get('table')) == TABLE for r in rules):
    raise RuntimeError('VPN routing policy already exists')
  for table, chain in [(t, c) for t, c, _ in CHAINS]:
    if run(IPT, '-t', table, '-S', chain, check=False).returncode == 0:
      raise RuntimeError('VPN firewall chain already exists')
  try:
    pwd.getpwuid(UID)
  except KeyError:
    pass
  else:
    raise RuntimeError('VPN runtime UID is assigned')
  for entry in Path('/proc').iterdir():
    try:
      if (entry.name.isdigit() and entry.stat().st_uid == UID
          and (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'):
        raise RuntimeError('VPN runtime UID already has a process')
    except FileNotFoundError:
      pass
  for port in (17890, 17891, 17893):
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
      with socket.socket(socket.AF_INET, kind) as probe:
        # Closed Go listeners can leave TIME_WAIT. This still rejects listeners,
        # while allowing immediate managed restart after a verified core stop.
        if kind == socket.SOCK_STREAM:
          probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('0.0.0.0', port))
  CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True)
  with mutation_gate():
    parked()
    if owner() is not None:
      raise PermissionError('VPN ownership changed during reservation')
    state = {'provider': provider, 'lease': lease(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'chains': [], 'rules': [], 'hooks': []}
    pending = OWNER.with_suffix('.pending')
    pending.write_text(json.dumps(state))
    pending.chmod(0o600)
    os.replace(pending, OWNER)


def prepare(provider):
  reserve(provider)
  if owner().get('interface'):
    return
  journaled('interface', True, 'ip', 'tuntap', 'add', 'dev', DEV, 'mode', 'tun', 'user', str(UID))
  run_parked('ip', 'addr', 'replace', '198.18.0.1/30', 'dev', DEV)
  run_parked('ip', 'link', 'set', DEV, 'up')


def hotspot_rules(network):
  if not network:
    return []
  return [('-t', 'nat', 'PREROUTING', '-i', 'wlan0', '-j', 'C3MH_DNS'),
          ('-t', 'filter', 'FORWARD', '-i', 'wlan0', '-o', DEV, '-j', 'ACCEPT'),
          ('-t', 'filter', 'FORWARD', '-i', DEV, '-o', 'wlan0', '-j', 'ACCEPT')]


def enable(provider):
  state = owner()
  require_owner(state, provider)
  if state.get('enabled'):
    return
  journaled('default_route', True, 'ip', 'route', 'replace', 'default', 'dev', DEV, 'table', TABLE)
  for table, chain in [(t, c) for t, c, _ in CHAINS]:
    journaled_chain(owner()['chains'] + [[table, chain]])
  for chain, table in (('C3MH_MARK', 'mangle'), ('C3MH_OUTDNS', 'nat')):
    run_parked(IPT, '-t', table, '-A', chain, '-m', 'owner', '--uid-owner', str(UID), '-j', 'RETURN')
  for subnet in ('127.0.0.0/8', '192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12', '169.254.0.0/16',
                 '198.18.0.0/15', '224.0.0.0/4', '255.255.255.255/32'):
    run_parked(IPT, '-t', 'mangle', '-A', 'C3MH_MARK', '-d', subnet, '-j', 'RETURN')
  run_parked(IPT, '-t', 'mangle', '-A', 'C3MH_MARK', '-j', 'MARK', '--set-mark', MARK)
  for protocol in ('udp', 'tcp'):
    run_parked(IPT, '-t', 'nat', '-A', 'C3MH_OUTDNS', '-p', protocol, '--dport', '53', '-j', 'REDIRECT', '--to-ports', '17893')
  for interface in ('lo',):
    run_parked(IPT, '-t', 'filter', '-A', 'C3MH_INPUT', '-i', interface, '-j', 'RETURN')
  for protocol in ('udp', 'tcp'):
    run_parked(IPT, '-t', 'filter', '-A', 'C3MH_INPUT', '-p', protocol, '--dport', '17893', '-j', 'REJECT')
  for table, chain, parent in CHAINS:
    state = owner()
    marker = state['chain_tokens'][f'{table}/{chain}']
    hook = ['-t', table, parent, '-m', 'comment', '--comment', marker, '-j', chain]
    journaled('hooks', state['hooks'] + [hook], IPT, '-t', table, '-I', *hook[2:])
  journaled('rules', owner()['rules'] + ['mark'], 'ip', 'rule', 'add', 'priority', '17880', 'fwmark', MARK + '/0xffff', 'lookup', TABLE)
  with mutation_gate():
    parked()
    remember('enabled', True)


def disable(provider, retain=False):
  state = owner()
  if state is None:
    return  # No ownership record means no right to remove resources.
  require_owner(state, provider)
  pending = state.get('pending')
  if pending is not None:
    if not isinstance(pending, dict) or pending.get('key') not in ('interface', 'default_route', 'chains', 'hooks', 'rules'):
      raise ValueError('Invalid pending VPN resource')
    if pending['key'] == 'chains' and 'marker' in pending:
      table, target = pending['value'][-1]
      if chain_marker(table, target, pending['marker']):
        state['chains'] = pending['value']
        state.setdefault('chain_tokens', {})[f'{table}/{target}'] = pending['marker']
    else:
      state[pending['key']] = pending.get('value')
  failures = []
  def remove(args, absent):
    try:
      result = run(*args, check=False)
      if result.returncode and not absent():
        failures.append(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
      failures.append(args)

  def rule_absent(priority):
    rules = json.loads(run('ip', '-j', 'rule').stdout)
    def matches(item):
      if item.get('priority') != priority or str(item.get('table')) != TABLE:
        return False
      if priority == 17890:
        return item.get('iif') == 'wlan0'
      try:
        mark = item.get('fwmark')
        mask = item.get('fwmask')
        mark = int(mark, 0) if isinstance(mark, str) else int(mark)
        mask = int(mask, 0) if isinstance(mask, str) else int(mask)
        return mark == int(MARK) and mask == 0xffff
      except (TypeError, ValueError):
        return False
    return not any(matches(item) for item in rules)

  def hook_absent(rule):
    result = run(IPT, *rule[:2], '-S', rule[2], check=False)
    if result.returncode:
      return False
    wanted = ['-A', *rule[2:]]
    return not any(shlex.split(line) == wanted for line in result.stdout.splitlines())

  def chain_absent(table, chain):
    if run(IPT, '-t', table, '-S', check=False).returncode:
      return False
    return run(IPT, '-t', table, '-S', chain, check=False).returncode == 1

  def chain_unreferenced(table, chain):
    result = run(IPT, '-t', table, '-S', check=False)
    if result.returncode:
      return False
    for line in result.stdout.splitlines():
      words = shlex.split(line)
      if any(words[index] in ('-j', '-g') and words[index + 1] == chain for index in range(len(words) - 1)):
        return False
    return True

  def route_absent(destination, device):
    rows = json.loads(run('ip', '-j', 'route', 'show', 'table', 'all').stdout)
    return not any(str(row.get('table')) == TABLE and row.get('dst', 'default') == destination
                   and row.get('dev') == device for row in rows)

  def interface_absent():
    return run('ip', 'link', 'show', DEV, check=False).returncode == 1

  if pending is not None and pending.get('key') == 'chains' and pending.get('temporary'):
    table = pending['value'][-1][0]
    temporary = pending['temporary']
    result = run(IPT, '-t', table, '-S', temporary, check=False)
    if result.returncode == 0:
      lines = [shlex.split(line) for line in result.stdout.splitlines()]
      empty = lines == [['-N', temporary]]
      if empty or chain_marker(table, temporary, pending['marker']):
        remove((IPT, '-t', table, '-F', temporary), lambda: chain_absent(table, temporary))
        remove((IPT, '-t', table, '-X', temporary), lambda: chain_absent(table, temporary))
      else:
        failures.append(('unowned temporary chain', table, temporary))

  if 'mark' in state['rules']:
    remove(('ip', 'rule', 'del', 'priority', '17880', 'fwmark', MARK + '/0xffff', 'lookup', TABLE),
           lambda: rule_absent(17880))
  if 'hotspot' in state['rules']:
    remove(('ip', 'rule', 'del', 'priority', '17890', 'iif', 'wlan0', 'lookup', TABLE),
           lambda: rule_absent(17890))
  for rule in reversed(state['hooks']):
    remove((IPT, *rule[:2], '-D', *rule[2:]), lambda: hook_absent(rule))
  for table, chain in reversed(state['chains']):
    marker = state.get('chain_tokens', {}).get(f'{table}/{chain}')
    if marker is not None and not chain_marker(table, chain, marker):
      # A chain with this name no longer carries our generation marker.
      continue
    if not chain_unreferenced(table, chain):
      failures.append(('referenced chain', table, chain))
      continue
    remove((IPT, '-t', table, '-F', chain), lambda: chain_absent(table, chain))
    remove((IPT, '-t', table, '-X', chain), lambda: chain_absent(table, chain))
  if state.get('default_route'):
    remove(('ip', 'route', 'del', 'default', 'dev', DEV, 'table', TABLE), lambda: route_absent('default', DEV))
  if state.get('hotspot_route'):
    remove(('ip', 'route', 'del', state['hotspot'], 'dev', 'wlan0', 'table', TABLE),
           lambda: route_absent(state['hotspot'], 'wlan0'))
  if state.get('interface'):
    remove(('ip', 'link', 'del', DEV), interface_absent)
  if 'previous_forward' in state:  # Clean up a session created by the older router profile.
    Path('/proc/sys/net/ipv4/ip_forward').write_text(state['previous_forward'])
  if failures:
    raise RuntimeError('VPN cleanup incomplete; ownership retained for retry')
  if not retain:
    OWNER.unlink()
