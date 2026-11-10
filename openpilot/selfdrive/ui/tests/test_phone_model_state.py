"""Failure cases: 44/50 vs 45/50, duplicate UI ticks, stale pre-drive model, lost source, offroad reset."""
import ast
from collections import deque
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
import unittest


class TestPhoneModelState(unittest.TestCase):
  def test_window(self):
    # Load the production state transition without starting device services or the GUI.
    tree = ast.parse((Path(__file__).parents[1] / 'ui_state.py').read_text())
    state = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UIState')
    method = next(n for n in state.body if isinstance(n, ast.FunctionDef) and n.name == '_update_chestnut_state')
    enum = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ChestnutState')
    namespace = {'Enum': Enum}
    exec(compile(ast.Module(body=[enum, method], type_ignores=[]), '<ui_state>', 'exec'), namespace)
    states, update = namespace['ChestnutState'], namespace['_update_chestnut_state']
    class SM(dict):
      updated = {'modelV2': True}
      recv_frame = {'modelV2': 1}
      alive = {'modelV2': True}
    sm = SM(deviceState=SimpleNamespace(chestnutPresent=False), modelV2=SimpleNamespace(big=False))
    ui = SimpleNamespace(sm=sm, started=True, started_frame=0, chestnut_present=False,
                         chestnut_alert=False, chestnut_state=states.DISCONNECTED,
                         _phone_big_frames=deque(maxlen=50), _phone_seen=False)
    def frame(big):
      sm['modelV2'].big = big
      update(ui)
    frame(False)
    self.assertEqual(ui.chestnut_state, states.DISCONNECTED)
    for _ in range(44):
      frame(True)
    self.assertEqual(ui.chestnut_state, states.FAILED)
    frame(True)
    self.assertEqual(ui.chestnut_state, states.ACTIVE)
    for _ in range(4):
      frame(False)
    self.assertEqual(ui.chestnut_state, states.ACTIVE)
    frame(False)
    self.assertEqual(ui.chestnut_state, states.ACTIVE)
    frame(False)
    self.assertEqual(ui.chestnut_state, states.FAILED)
    sm.updated['modelV2'] = False
    window = list(ui._phone_big_frames)
    frame(True)
    self.assertEqual(list(ui._phone_big_frames), window)
    for _ in range(50):
      sm.updated['modelV2'] = True
      frame(False)
    self.assertEqual(ui.chestnut_state, states.FAILED)
    ui.started = False
    update(ui)
    self.assertFalse(ui._phone_big_frames)
    self.assertFalse(ui._phone_seen)
    ui.started = True
    sm.recv_frame['modelV2'] = ui.started_frame
    frame(True)
    self.assertEqual(ui.chestnut_state, states.DISCONNECTED)


if __name__ == '__main__':
  unittest.main()
