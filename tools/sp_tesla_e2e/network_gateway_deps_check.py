"""Failure: system YAML path shadows the active Python environment's CFFI backend."""
import sys
import openpilot.tesla.network_gateway  # noqa: F401
import cffi
import yaml  # noqa: F401

cffi.FFI()
system = '/usr/lib/python3/dist-packages'
if system in sys.path:
  environments = [index for index, value in enumerate(sys.path) if 'site-packages' in value]
  assert not environments or sys.path.index(system) > min(environments), 'system path shadows environment libraries'
print('gateway dependency path ordering: PASS')
