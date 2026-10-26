#!/usr/bin/env python3
from openpilot.cereal import custom
from opendbc.car.structs import car
from openpilot.common.gps import get_gps_location_service
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
import openpilot.cereal.messaging as messaging
from openpilot.tesla import plugins


def main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  cloudlog.info("plannerd is waiting for CarParamsSP")
  CP_SP = messaging.log_from_bytes(params.get("CarParamsSP", block=True), custom.CarParamsSP)
  cloudlog.info("plannerd got CarParamsSP")

  gps_location_service = get_gps_location_service(params)
  plugin_services = plugins.services()  # xpilot plugins: extra inputs for their hooks
  ignore_services = ["liveMapDataSP", "carStateSP", "selfdriveStateSP", gps_location_service, *plugin_services]

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP, CP_SP)
  plan_filters = plugins.plan_filters(CP, params)  # xpilot plugins may only tighten the published plan
  longitudinal_planner.plugin_long_targets = plugins.long_targets(CP, params)  # xpilot plugin speed targets (may raise)
  longitudinal_planner.plugin_long_planner = plugins.long_planners(CP, CP_SP, params)  # xpilot replaceable planner
  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance', 'longitudinalPlanSP'])
  sm = messaging.SubMaster(['carControl', 'carControlSP', 'carState', 'controlsState', 'radarState', 'modelV2', 'selfdriveState',
                            'liveMapDataSP', 'carStateSP', 'selfdriveStateSP', gps_location_service, *plugin_services],
                           poll='modelV2', ignore_alive=ignore_services, ignore_avg_freq=ignore_services, ignore_valid=ignore_services)

  while True:
    sm.update()
    longitudinal_planner.sla.update_buttons(sm['selfdriveStateSP'].buttonsReleaseToggle)
    if sm.updated['modelV2']:
      longitudinal_planner.update(sm)
      longitudinal_planner.publish(sm, plugins.plan_sink(plan_filters, pm, sm))

      ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
      msg = messaging.new_message('driverAssistance')
      msg.valid = sm.all_checks()
      msg.driverAssistance.leftLaneDeparture = ldw.left
      msg.driverAssistance.rightLaneDeparture = ldw.right
      pm.send('driverAssistance', msg)


if __name__ == "__main__":
  main()
