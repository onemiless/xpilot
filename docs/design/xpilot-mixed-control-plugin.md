# X-20261025-20：dev-sp Tesla 混控插件设计

2026-10-25，只读研究与设计；本次只提交本文，不实现、不接设备、不刷 Panda。

## 结论与范围

恢复对象是 **dzid26 的 angle-based cooperative steering：驾驶员轻扭矩与 SP 目标转角协作**。它与“SP/原车 AP 两轴控制权交接”是两个功能。建议先恢复前者，再为实际 Autosteer 共用补齐最小的报文所有权与 Panda 转发能力；不搬回整套 dynamic ACC、AP hybrid、雷达、自动调速或事件屏蔽逻辑。

| 路径 | 最小实现 | Panda 改动 | 能承诺什么 |
|---|---|---|---|
| A，先做 | 商店插件 `tesla_mixed_control`，使用现有 `lat_controller`；车端仍生成转向 CAN | 无 | 原车 Autosteer **设置可开启**；原车 ACC + SP 角度协作。Autosteer 实际掌舵时不能据此承诺安全共用 |
| B，满足实际 AP 共用 | A + 宿主横向报文裁决 + 固件内 OEM/SP 所有权裁决及转发扩展 | **有，须编译、验证、重刷** | 原车 AP 掌舵时 SP 不发竞争转向；AP 退出后经明确驾驶员接管恢复 SP。首版不自动切纵向、不承诺无缝切换 |

删除 `invalidLkasSetting` 阻挡只是解除设置门槛，不等于 CAN 控制权交接已完成。当前 `carstate_ext.py` 最后把它设为 False，但 Panda 仍有独立的转向拒发/转发条件（B:`opendbc_repo/opendbc/sunnypilot/car/tesla/carstate_ext.py:25-27`；B:`opendbc_repo/opendbc/safety/modes/tesla.h:217-230,261-287,335-357`）。完整目标若包含 Autosteer **实际启用**，应按 B 立项；A 是可独立验收的第一步。

## 固定证据版本与复查方法

下文 `B:` 为系统仓库基点 `a497ef963d9a4437adf05cd2997d476f3fbc26f5`，不是研究开始时本机的后续 `65bb09e3d5`。`D:` 为本次 fetch 的 `onemiless/openpilot` 的 `origin/dev-sp = 1cd7319be95bd1cbfca3ae586c28eed9da339fc9`。

`D` 的 gitlinks 是 opendbc **`65cc8fdbcb6aaaf0533ba296c9e6fe5422a696e8`**（下称 `O:`）、panda `0d1963ba624f0410a894daf6857eca11b02771d9`。`O:` 路径均相对于 `onemiless/opendbc` 根，不加 `opendbc_repo/`。另查历史 opendbc **`701700fab347fcf6fb9f9b5028270859b929fd99`**（`H:`）与 panda **`6ccdbb16a493369b0db4e0c61e1bcad962cc4b00`**（`P:`）。子仓库对象从本机已有 `/Users/mile/Desktop/mo-op/{opendbc_repo,panda}` 只读读取，未切换或修改那些 checkout。

指定四个父仓库提交均另行 fetch 到对象库；在本次可见的 Git 图中，它们都不是 `D` 的祖先，不能把它们的树直接称为当前 dev-sp。父仓库历史与当前 gitlink 内容交叉核对，避免把迁移前 UI、迁移后算法和不同 DBC 混在一起。

```sh
git fetch --no-recurse-submodules --no-tags origin refs/heads/dev-sp:refs/remotes/origin/dev-sp
git log origin/dev-sp -i --grep='coop\|dzid26\|lkas\|mixed\|handoff' --oneline
git grep -n -i -E 'coop|LKAS|DAS_steeringControlType' origin/dev-sp -- openpilot tools docs
git ls-tree origin/dev-sp opendbc_repo panda
git fetch --no-recurse-submodules --no-tags origin d806000fd779ac1d4ed9cc2563da54cecf93d838 fbb1baf173a7113c02902454f32fa2386c9831a4 4ae968c7400ec874cb69972690de695cae6b5ab1 308445caab32ca248441567e0bcec5efe3af1803
git show 4ae968c740 -- opendbc_repo
git show 308445caab -- panda
git -C /Users/mile/Desktop/mo-op/opendbc_repo show 65cc8fdbcb6aaaf0533ba296c9e6fe5422a696e8:opendbc/sunnypilot/car/tesla/coop_steering.py
```

父仓库 grep 不递归 gitlinks，算法/safety 必须另查子仓库。关键历史：

| 提交 | 实际改动与证据 |
|---|---|
| [d806000fd7](https://github.com/onemiless/openpilot/commit/d806000fd779ac1d4ed9cc2563da54cecf93d838)、[fbb1baf173](https://github.com/onemiless/openpilot/commit/fbb1baf173a7113c02902454f32fa2386c9831a4)，同题 `vtb: angle based coope steering replaces lkas mode` | **都只改 UI**：去 Beta/23 km/h 门槛描述及 48 km/h 摆动提示，改成轻扭矩产生转角、速度越高越沉；仍只允许停车改开关。d806 的 `openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/tesla.py:19,33-45`。分别引用 opendbc `6430e398310b5e701bef10ce7f07e04588db2141`、`a905eeb07f8e3c4d8f6337bc979360d411cd478f`；不能将 UI diff 当算法实现 |
| [4ae968c740](https://github.com/onemiless/openpilot/commit/4ae968c7400ec874cb69972690de695cae6b5ab1) | 只把 opendbc gitlink 从 `a905eeb07` 更新到 `701700fab`；后者 revert `975fd914e`，DBC 的 control type 从 3 位退回 2 位，safety 同步由 `>>5` 改为 `>>6`（H:`opendbc/dbc/tesla_model3_party.dbc:186-192`；H:`opendbc/safety/modes/tesla.h:229-230,278-282`） |
| [308445caab](https://github.com/onemiless/openpilot/commit/308445caab32ca248441567e0bcec5efe3af1803) | 只把 panda gitlink `74a0adced` 更新到 `6ccdbb16a`；子历史含 `7b48d3863` wake-on-CAN、`a4a54d7db` 心跳按 engaged 门限、`6ccdbb16a` CAN 流量唤醒。P:`board/main.c:175-195,219-233`：≥200 RX/s 唤醒、低流量保持超时、心跳丢失进 SILENT；不是混控报文放行逻辑 |
| O 的后续移植 | `e15d18898f046e569f3e8d1a1415fa25d62c4508` 移入 Tesla 控制；`3b9bf1895eca4983b2ed7cb620f318d362129112` 修 FAULT/真实 carOutput、清无用 TX；`322b203fd504c40bcac69693674534d53eea9929` 保留 2/3-bit 双协议。当前 O 的算法文件历史只有移植及修正，不能靠父仓库 grep 找齐 dzid26 早期提交 |

## dev-sp 到底做了什么

### 角度协作本体

旧 LKAS 协作依赖 EPS 的 `LANE_KEEP_ASSIST` 模式；dzid26 改成在软件中处理驾驶员扭矩，最终发送普通 angle control。当前 O 实现的顺序为：

1. 先对 SP 计划转角做车型 VM 限幅，再在 **50 Hz** 转向发送周期调用 coop；`lat_active` 还要求没有 `steeringDisengage`、没有真实 ACC FAULT（O:`opendbc/car/tesla/carcontroller.py:67-91`）。
2. 恢复转向时渐增转角变化速度，斜率常量 300 deg/s²；非 active 或 coop 关闭时清除 override 积累（O:`opendbc/sunnypilot/car/tesla/coop_steering.py:19-25,153-174`）。
3. 驾驶员扭矩减去 0.5 Nm 死区，按速度和 VM 换算角度；2.5 Nm 是映射范围参考，不是允许掩盖 EPS 强制退出的门限。目标映射采用 2.0 m/s² 横向加速度与最大转角，≤0.1 m/s 时不除以趋零的 holding gain（O:`opendbc/sunnypilot/car/tesla/coop_steering.py:27-38,43-82,113-118`）。
4. 用“当前 override 所需保持扭矩”扣除中性扭矩，按驾驶员剩余用力累积/回正角度；同向计划变化去重，反向变化按驾驶员用力补偿；末尾 VM 再限幅并 anti-windup（O:`opendbc/sunnypilot/car/tesla/coop_steering.py:120-151,176-192`）。其中 `MAX_ANGLE_RATE=5` 是限幅函数的**每次更新角度上限**，不能写成 5 deg/s（B:`opendbc_repo/opendbc/car/lateral.py:150-160`）。

H 还包含未启用的低速 acceleration/jerk 实验：调用被注释，不能作为迁回要求；当时把角度 debug 填入 `accel/curvature/torque` 也不能恢复（H:`opendbc/sunnypilot/car/tesla/coop_steering.py:239-260,275-304`；H:`opendbc/car/tesla/carcontroller.py:63-67`）。O 已报告实际最终发送转角/加速度，并在非发送周期保持上次值（O:`opendbc/car/tesla/carcontroller.py:31-33,89-94,149-157`）。

### 车端状态、CAN 与原车 AP

| 部位 | dev-sp 行为 |
|---|---|
| CarState | 从 `EPAS3S_sysStatus` 读取反号后的轮角、torsion torque、hands-on、EAC 状态；强 override/EAC 高转速故障退出，另有 >5 Nm 退出。读取 `DAS_status.DAS_autopilotState`、OEM steering 与 longitudinal 模板（O:`opendbc/car/tesla/carstate.py:78-98,180-204`；O:`opendbc/safety/modes/tesla.h:21,179-191`） |
| 主转向 TX | `0x488`, party bus 0, 4 bytes, 50 Hz：`DAS_steeringAngleRequest=-angle`、haptic=0、`DAS_steeringControlType` angle/NONE；DBC 带 counter/checksum。FSD14 与 2/3-bit 编码另有映射条件（O:`opendbc/car/tesla/teslacan.py:6-30`；O:`opendbc/safety/modes/tesla.h:278-290,327-351,568-571`） |
| 转向允许 TX | `0x27D`, bus 0, 3 bytes, 10 Hz，`APS_eacMonitor.APS_eacAllow=1`（O:`opendbc/car/tesla/carcontroller.py:96-97`；O:`opendbc/car/tesla/teslacan.py:71-76`） |
| 纵向与交接 | `0x2B9`, bus 0, 8 bytes：SP 正常 25 Hz；`DAS_accState=4` 控制、13 为零加速度 cancel；AP hybrid 可让 Panda 直接转发 OEM longitudinal。进入 OEM 时发 `DAS_aebEvent=3` 内部标记，Panda **消费并拒发**；恢复 SP 时安全接受的第一帧原子关闭 OEM 转发（O:`opendbc/car/tesla/carcontroller.py:99-146`；O:`opendbc/car/tesla/teslacan.py:40-69`；O:`opendbc/safety/modes/tesla.h:362-383,494-498,519-522`） |
| 横向 AP 交接 | AP owns lateral 时停发 SP `0x488`；进入时 angle=实测、type=3 内部标记，Panda 消费并拒发后开启 OEM 转发；退出时复位角度/override 状态。不是两份 angle command 在 CAN 上求和（O:`opendbc/car/tesla/carcontroller.py:70-94`；O:`opendbc/car/tesla/teslacan.py:32-38`；O:`opendbc/safety/modes/tesla.h:337-343,494-516`） |

**coop 本体不选择 AP owner。** O 的另一套 `carstate_ext` 才有 `sp/dynamicStock/manualStock/apHybridStock` 来源、AP active 状态 3/4/5/6、退出 8/9、故障 14/15、确认样本与冷却；driver torque ≥0.5 Nm 会在该 AP 会话中锁住 SP 横向，不因松手马上跳回 OEM（O:`opendbc/sunnypilot/car/tesla/carstate_ext.py:23-45,293-329,359-445`）。AP hybrid 初始化要求 SP longitudinal 开启，并置 `safetyParamSP` 64/128 两个交接位（O:`opendbc/sunnypilot/car/interfaces.py:172-178`）。这是后续完整混合 AP 插件的参考，不是角度协作的隐含依赖。

O 曾按 AP 状态/交接会话抑制 invalid-LKAS，并在父仓库过滤 `buttonCancel/pcmDisable/wrongCarMode/...`。真实 FAULT 则清 owner，控制器发 inactive steering/零加速度 cancel（O:`opendbc/car/tesla/carstate.py:189-198`；O:`opendbc/sunnypilot/car/tesla/carstate_ext.py:699-717`；D:`openpilot/sunnypilot/selfdrive/car/tesla/control_runtime.py:69-84`）。**旧事件屏蔽不应迁回**：当前宿主对 cancel、panda/CAN、DM 等有不可删除底线（B:`openpilot/tesla/core_host.py:33-38,74-100`）。

### 参数、UI、Panda 边界

- `TeslaCoopSteering`：持久 BOOL，默认 0；初始化置 `CP_SP.flags.COOP_STEERING`，不是 safetyParam 位。当前 dev-sp UI 反而又显示 Beta/23 km/h 与 48 km/h 警告；不能据旧 UI 删除提交断言这些速度限制已完成实车验证（D:`openpilot/common/params_keys.h:291-292`；O:`opendbc/sunnypilot/car/interfaces.py:141-146`；D:`openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/tesla.py:15-16,55-70`）。
- 旁系开关：`TeslaApHybrid`、`TeslaDynamicApLongitudinal`、四指纵向切换、`DynamicAutoStock`、转向灯/弯道回 SP、80/70 km/h 双阈值；默认关闭，启动时锁存。MADS 的多指横向接管与四指纵向切换也不是一个开关（D:`openpilot/common/params_keys.h:178-186`；D:`openpilot/sunnypilot/selfdrive/car/tesla/control_profile.py:20-32,48-50`；O:`opendbc/sunnypilot/car/tesla/carstate_ext.py:194-219,719-743`）。
- Panda 的 steering safety 在 **opendbc 子仓库**；panda 项目提供固件/唤醒/心跳。不能用 `308445caab` 证明控制 safety。O 继续有 angle/rate/controls gate、强 override、AEB 优先、Summon 避让，并增加 owner marker/转发状态（O:`opendbc/safety/modes/tesla.h:179-191,321-381,494-527`）。这些源码事实都不证明某台实物 Panda 已刷入或车辆线束接法正确。

## 与 B 基点现状的差异

| 当前已有 | 缺口与设计含义 |
|---|---|
| SP `coop_steering.py` 只有 flag→type：开=2、关=1（B:`opendbc_repo/opendbc/sunnypilot/car/tesla/coop_steering.py:16-28`） | 没有扭矩→角度、积分、resume ramp、anti-windup；不能改名包装就称恢复 dzid26 |
| Tesla controller 每两帧 VM 限幅后发 steering，10 帧发 allowed（B:`opendbc_repo/opendbc/car/tesla/carcontroller.py:30-48`） | 不知道 AP owner，照常发 0x488；其 `lat_active` 只另查 hands_on<3。要保留 SP/车端底线，不覆盖整个 Tesla port |
| 当前 DBC **只支持 3-bit**，接口按 0x489/0x054 指纹判断旧固件 dashcamOnly；SP 对 Model X 另有覆写（B:`opendbc_repo/opendbc/dbc/tesla_model3_party.dbc:220-226,353`；B:`opendbc_repo/opendbc/car/tesla/interface.py:44-66`） | 禁止把 H 的 2-bit DBC/`>>6` 整体复制到新系统。旧 2-bit 支持需要单独的协议适配任务和 safety 证明，插件不绕过 dashcamOnly |
| 当前 `stockLkas` 只是 control type==2；safety 的 stock-LKAS latch 仅在 **controls 两轴均不允许**时抓上升沿，转发依据该 latch（B:`opendbc_repo/opendbc/car/tesla/carstate.py:110-116`；B:`opendbc_repo/opendbc/safety/modes/tesla.h:217-230,283-287,335-357`） | 它不是“原车 Autosteer active”通用检测，type=1/4 和 controls 已允许时的切入不能靠它覆盖。旧 O 的 nonzero 检测也不应不分场景照抄 |
| `setup(CP_SP)` 可改 flags/safetyParam；先于 CP/CP_SP 发布，pandad 分开设置 base param 与 SP param（B:`openpilot/tesla/plugin.py:38-55`；B:`openpilot/selfdrive/car/card.py:127-159`；B:`openpilot/selfdrive/pandad/panda_safety.cc:69-78`） | 可在启用 angle-coop 插件时清掉原生 COOP flag，避免两种 coop 叠加；新 safety 位必须读 manifest，不能复制旧 64/128 或混淆 `CP.safetyConfigs[].safetyParam` 与 `CP_SP.safetyParam` |
| `lat_controller` 独占 role，SP 并行，异常/非有限值后本进程永久回 SP（B:`openpilot/tesla/plugin.py:95-102`；B:`openpilot/tesla/plugins/__init__.py:449-484`） | 足以接角度算法；没给插件 SP 的已算输出，最小适配可复用 `LatControlAngle` 求基准角度。controlsd 的学习 VM 与旧算法的固定 safety VM 不同，必须显式区分 |
| `lat_correction` 在 clip_curvature 前，总修正最多 0.5 m/s²，低速按 5 m/s（B:`openpilot/tesla/plugin.py:87-93`；B:`openpilot/tesla/plugins/__init__.py:395-414`） | 不适合迁回 2.0 m/s² 映射和 angle-state 算法，不扩大此钩子的上限 |
| `control_sends` 在 `CI.apply` 之后 **extend**；claim 只校验插件发的 address/bus（B:`openpilot/selfdrive/car/card.py:261-266`；B:`openpilot/tesla/plugins/__init__.py:158-174`） | 无法删除、替换或抑制 SP 帧，也不对 SP 自有报文做独占裁决。声明 `can.tx:0x488:0` 后再 append 会双发；不能用 monkey patch 绕过 |
| safety profiles 只有 ambient_light 0x2000→0x679:1、auto_speed 0x4000→0x3C2:1（B:`opendbc_repo/opendbc/safety/plugins/safety.json:1-4`） | manifest 支持 `safety:{profile:...}`，CAN claims 必须等于该 profile 的 tx；一个 profile 只允许一个地址，新头文件需要系统固件发布（B:`openpilot/tesla/manifest.py:138-158`；B:`openpilot/tesla/plugins/gen_safety.py:46-81`） |
| C safety_plugin 只有 active/rx/tx/reset，无 fwd；命中 profile 后直接调用 plugin tx，**不再调用 mode tx**（B:`opendbc_repo/opendbc/safety/plugins/plugin.h:6-12`；B:`opendbc_repo/opendbc/safety/safety.h:230-247,262-282`） | 当前机制是扩展 TX，不是给现有 0x488 叠加 safety。把 0x488 登记为 profile 会遮蔽原车端检查；且单加头文件仍不能打开 OEM forwarding。这是 B 路径必须先修的系统接口问题 |

## 插件形状与宿主责任

### A：现有 hooks 的最小落地

- 插件仓库 `plugins/tesla_mixed_control/{plugin.json,plugin.py,controller.py}`；入口只接线，算法用 O 的有效代码与原许可，不带 H 中未调用的 jerk 实验。category=`controller`，claim=`role:lateral.controller`，一个默认关闭的 BOOL 总开关 `XpTeslaMixedControl`。无直接 CAN claim、无 safety profile、无新增消息字段或 UI 磁贴。复用市场详情页，英文说明当前只支持 SP 掌舵，原生 `TeslaCoopSteering` 在本插件启用期间由插件模式覆盖。
- `setup(CP_SP)` 在本插件开启时清 `COOP_STEERING`，使车端走 angle mode；算法自身改由插件总开关启用，不能再依赖被清掉的旧 flag。不改原生参数持久值、不启用 longitudinal、不修改车端源码。关闭插件、下次启动恢复原生参数语义。controlsd 应拿到 card 发布的同一 CP_SP。
- `lat_controller` 复用 `LatControlAngle` 的基准输出（构造要传 DT_CTRL），使用 O 的扭矩/override 算法，输出仍交原 Tesla controller 发包。读取已有 `CS.steeringTorque/steeringAngleDeg/vEgo/vEgoRaw`；需要 AP 判定时，`observe_can` 自建只读 parser，不能将 `stockLkas` 当 AP owner。
- **时序适配是验收项**：旧算法在车端限幅之后、50 Hz；现钩子在 controller 限幅之前、100 Hz。保留 20 ms 积分步长/保持非更新输出，明确处理 card/controlsd 相位、丢周期和双重限幅；safety VM 用现有 `get_safety_CP()`，不得把学习 VM 当固定 safety VM。不能直接每 10 ms 调旧 update 并宣称等价（B:`openpilot/selfdrive/controls/lib/latcontrol_angle.py:10-37`；B:`openpilot/selfdrive/controls/controlsd.py:76-80,110-125`；B:`opendbc_repo/opendbc/car/tesla/carcontroller.py:12-16,27-45`）。
- 如果回放不能证明相位/限幅后的行为符合预期，采用下面 B 的 50 Hz actuation 接口；不在 port 内插入算法。插件异常后的现有 SP 回退可能产生 offset 突变，必须测过才可进入闭场。

### B：实际 Autosteer 共用所需的最小系统扩展

暂定范围：**stock longitudinal 保持原车，`CP.openpilotLongitudinalControl=False`**；OEM AP 与 SP coop 互斥拥有横向。没有自动按车速切换、OEM longitudinal marker、AP 退出自动续控。SP 接管必须再次满足驾驶员 ENABLE 与原有宿主底线。

1. 新增一个窄的、独占的横向 actuation hook/claim（拟名 `role:lateral.actuation`，当前不存在）：在 card 的 `CI.apply` 后、发 sendcan 前接收原输出/帧、CS、CC、时间；只在 SP 原定 0x488 周期给出 angle/允许发送/owner 建议。宿主控制 0x488 **唯一来源**，统一编码、限幅并同步实际 `carOutput`；插件不能改 0x27D、0x2B9 或别的帧。旧算法可在这里精确复用 50 Hz 阶段；不同时占用 A 的 lat_controller 重复计算。
2. 宿主据新鲜 OEM `DAS_status`、0x488、DI/EPS 数据确认 AP ownership，AP 掌舵时抑制 SP 0x488，复位 override；SP 恢复从实测角度开始，经过 fresh idle/NONE 确认及明确驾驶员接管。状态过期/插件出错时停止 SP actuation，保持故障锁定，不能让普通“回 SP”策略自动抢回 AP。阈值和确认时间先做可重复回放，再固定为协议常量；不添加用户调参面板。
3. Panda 新增编译期 `tesla_mixed_control` profile/header（只管 0x488:0，bit 待分配；0x2000/0x4000 已占用）。B 的 manifest 同时声明 `role:lateral.actuation`、`can.tx:0x488:0` 和 `safety:{"profile":"tesla_mixed_control"}`；新宿主把该 claim 用于替换来源，不允许旁路 append。`setup` 从 `self.MANIFEST.safety["param_bit"]` 置 CP_SP 位；旧固件无 profile 时安装/启用应被拒绝。这个 profile 需要**窄的转发/owner接口扩展**，现有四回调不能表达。
4. 先改通用 safety 分发，使**已属于 Tesla mode 的帧同时满足原 mode TX 检查与 profile 的额外限制**；profile 不得用现有短路路径替代 mode。先完成无状态的 owner/时效/频率预检，再调用有状态的 mode 检查，全部通过才提交 profile 的 owner/计时状态；防止被 profile 拒绝的帧推进 mode 角度历史。新增转发扩展也只能在经过审核的 Tesla/0x488 路径选择 OEM owner，不能让任意插件取消 relay/nooutput/Autopark 等阻断。原生 `tesla.h` 保持原样；相关裁决在系统的 safety 扩展层和新 header。若保留 mode 检查使某次交接被拒绝，就等待 OEM idle，不能越权放行。
5. 固件必须自己验证 OEM 输入长度、checksum/counter、freshness 和 AP/DI/EPS 状态，独立仲裁 owner；Python 的 owner 是建议。当前 `safety_plugins_rx` 能收到未列入 mode RX 表的帧，`valid` 不代表那些帧做过 checksum/counter 检验（B:`opendbc_repo/opendbc/safety/safety.h:184-195`）。因此不能把接收到 `DAS_status` 等同于已可信；需在 profile 自己验证。
6. 不重用旧 type=3 标记：当前 DBC 中它是真实 `EMERGENCY_LANE_KEEP`。不在普通 CAN 上新增特殊控制消息，首版 owner 从经固件验证的 OEM 状态与合法 SP 接管推导；接管条件无法证明时拒绝切换。旧 type=3/AEB=3 机制只作历史解释，不迁入最小版。

宿主/SP 保留：驾驶员接管与取消、MADS、DM/FCW/AEB、数据存活检查、车型参数、转角编码/最终限幅、carOutput、controlsMismatch 与故障锁定。插件拥有：扭矩→目标角度状态、resume ramp、anti-windup、只读 AP 观察与 owner 建议。Panda 拥有最终 TX 和 forwarding 裁决，软件停发不等于 OEM 一定已接管。

系统接口、固件 header/`safety.json`/registry、对应检查及文档先走 `onemiless/xpilot`；插件代码、E2E、发布包/索引走 `onemiless/xpilot-plugins`，分 PR，系统先合并。本任务不创建上述实现或 PR。

## 每一项 CAN TX 与 safety 门槛

总线只表示软件路由：party=0、vehicle=1、autopilot_party=2，不证明线束接法或电气隔离。A 插件直接 TX 集合为空；下表包含它依赖的宿主 TX，以及 B 需要的唯一横向所有权扩展。

| CAN / 来源 | 所需检查与现状 |
|---|---|
| **0x488:0 / 4 bytes，SP 横向唯一输出，50 Hz** | 原 Tesla mode 的合法 type、inactive angle、controls_allowed **或** controls_allowed_lateral、VM 横向加速度/jerk/实时角速率、stock LKAS/Autopark 与 relay 检查必须保留。B 再限制 SP owner、fresh DI/EPS/AP、有效 counter/checksum、无 haptic、绝对转角≤360°、协议/符号一致及实际 20 ms 频率预算；NONE 只允许合法实测/零角度。AP owner 下拒绝一切竞争 SP steering。当前依据：B:`opendbc_repo/opendbc/safety/modes/tesla.h:233-287,360-370`；B:`opendbc_repo/opendbc/safety/lateral.h:353-411`。不要把 `.frequency=50` 的限幅计算误称为已有独立 TX 频率/校验和检查 |
| **0x27D:0 / 3 bytes，宿主 10 Hz** | A/B 不由插件重发或申领；维持 mode 白名单、relay、Autopark 条件。当前 mode 没有单独检查 APS_eacAllow 内容或发送周期，不能声称已具备；AP 共用时需在宿主保留固定 ALLOW 编码与原周期，并在固件验证其与 owner/原车 EPS 的配合，失败则不能开放 B（B:`opendbc_repo/opendbc/car/tesla/teslacan.py:45-50`；B:`opendbc_repo/opendbc/safety/modes/tesla.h:256-259,360-370`） |
| **0x2B9:0 / 8 bytes，宿主 cancel；非本插件 TX** | B 只用 stock longitudinal，禁止插件主动加减速。保留原 mode：非 long 模式只能 accState=13、accelMin/Max=0；任何发送均不得携 AEB event，stock AEB 活跃时连 cancel 也不发。允许取消不要求 controls_allowed，是现有模式用于退出的窄例外，不扩大到加速（B:`opendbc_repo/opendbc/safety/modes/tesla.h:289-325`）。A 若搭配 SP long，其正常25 Hz加速度仍全由原 controller/mode 管，不归 coop |
| **OEM 0x488:2→0、0x2B9:2→0 / forwarding** | 这是 Panda 转发，不是 Python 新 TX claim。B 只为 0x488 增加经确认的 OEM owner 转发；0x2B9 在 stock-long 模式沿用原转发，AEB 优先。必须证实 SP/OEM 无双源、fault/过期时无意外抢控。noOutput/relay/Autopark 规则不被 profile 解禁（B:`opendbc_repo/opendbc/safety/safety.h:262-282`；B:`opendbc_repo/opendbc/safety/modes/tesla.h:335-357`） |
| **不需要的 TX** | 不申请 0x3C2 调速、0x679 灯、0x300/301 雷达、0x082/0x3FD/0x370/0x399/0x3E9 诊断/旧试验，也不发送旧横纵 handoff marker。不存在“为了混控”开放任意 CAN 的理由 |

所有主动转向都必须有 Panda 允许的横向 controls；noOutput、relay 故障、输入超时、心跳失联不能被插件绕过。controls_allowed_lateral 是现有 MADS 权限，不是插件自造的免检开关。原生安全测试继续作为底线，新增固件测试必须先写失败矩阵。

## 风险与先行失败清单

实现前先冻结这些失败场景，再写检查，再写实现；不在实现后补单元测试：

- **协议错配**：2/3-bit、FSD14 语义映射、type=3 实车含义、角度/扭矩符号、量化/counter/checksum错；不支持的车型/固件必须拒绝。
- **控制冲突**：SP 与 OEM 同周期双发；AP 在 controls 已允许时进入、NONE→ANGLE/LKAS/FSD；原车仅设置开启但未 active；旧 `stockLkas` 误判。
- **状态/节拍错误**：100/50 Hz 倍增积分、丢帧/重放/乱序/陈旧状态、standstill 的增益除零、转向与 plan 反向、饱和后积分不消退、非 TX 周期 carOutput 不真实。
- **退出不生效**：刹车/取消/油门 override、EPS hands-on≥3/高角速率故障、ACC FAULT、CAN/relay/DM故障、Autopark/Summon、进程重启/卡死；不通过删除 floor events“修好”交接。
- **恢复突变**：AP→SP 时沿用旧 override/限幅状态、插件异常回 SP 跳角、用户没请求却自动接管、freshness 超时误切换；仅停发 SP 但 OEM 未转发造成控制空窗。
- **配置漂移**：原生 coop 与插件双开、行驶中改总开关、旧 firmware/新 manifest 不匹配、profile 遮蔽 mode tx、下次重启参数来源不同。

## 验证计划与可重复产物

以下为**未来实施验收**，本次没有运行控制 E2E、libsafety、固件构建、MISRA 或车辆测试。

| 层次 | 操作与通过条件 | 必交产物 |
|---|---|---|
| Host A | 商店安装→manifest/claims→setup→真实 controlsd/card/CI→发送 CAN 解码。覆盖上述失败清单；O 的 50 Hz输出作为固定输入条件下的参考，明确记录新钩子前后限幅差别；关插件与 B 基点逐帧比较，非横向行为不变 | 输入 fixture SHA-256、参数快照、系统/插件/参考 O SHA、逐帧 desired/override/实发角度、owner、时间、carOutput、断言 JSON |
| Host B + CAN replay | 真实 card 送入带 counter/checksum 的 CAN 记录，再把 sendcan 与同一输入喂给新编译的 libsafety RX/TX/FWD；覆盖 AP 在先/SP 在先、type0..7、cancel/FAULT/AEB/Autopark、掉流和边界时序。任一时刻仅一个横向来源，越权/过时/重复 TX 全拒；验证 profile 不遮蔽 base safety | 可重放 CAN 输入、accepted/rejected/FWD 时间线、两轴 owner、拒绝原因、构建与输入哈希、JSON/日志；重复运行判定一致 |
| Firmware gate | 保留并运行 Tesla 和 xpilot plugin safety 套件、MISRA、目标 Panda 构建；A 不新增 header，B 必须测 bit关/开、noOutput、模式切换/reset、时钟回绕和全局故障 | 完整命令/退出码、工具链与板型、固件/ELF SHA-256、safety/MISRA日志；host 通过不能称已刷设备 |
| 设备停车检查，另行授权后 | 先确认 parked/offroad/无控制与独占使用；核对系统/插件 SHA、Params、线束 bus、Panda serial/firmware signature/readback；安装关闭→开关→重启、拔除插件/回滚、进程故障。只做停车可安全完成的检查，不把静止 EAC 行为当行驶证明 | 设备 active commit 与主机 commit 分列，固件签名、参数/安装前后哈希、pandaState/CAN/进程日志、截图、回滚证明 |
| 封闭场地，停车门槛通过后 | 低速左右弯、停止/倒车限制、0.5 Nm 附近和强 override、接管/松手/取消/刹车、AP↔SP 明确切换、AP未接管/退出失败、故障演练；逐步提高速度。验证舒适性、摆动、交接空窗和驾驶员提示 | 路线/车辆软硬件/工况、CAN+controls+模型日志、角度/扭矩/加速度曲线、视频时间对齐、每项通过/失败与回滚版本 |

可复用现有静态/宿主门槛（实施时运行，不能代替以上控制 E2E）：

```sh
python3 tools/sp_tesla_e2e/manifest_check.py
python3 tools/sp_tesla_e2e/plugin_host_check.py
python3 tools/sp_tesla_e2e/phase3_check.py
python3 tools/sp_tesla_e2e/phase4_check.py
python3 tools/sp_tesla_e2e/safety_manifest_check.py
python3 openpilot/tesla/plugins/gen_safety.py --check
```

新增 E2E 应在插件仓库先落失败 fixture/检查入口，再接实现；建议固定 CLI `python3 plugins/tesla_mixed_control/tests/e2e.py --output <证据目录>`（**尚不存在，本文仅规定验收接口**）。目录必须自包含 `inputs/`、`params.json`、`source.json`、`frames.jsonl`、`result.json`、`commands.txt`、`SHA256SUMS`；记录真实命令和环境，重复运行可核对判定，不能只给 PASS 截图。safety 独立测试同样先写拒绝矩阵，优先在这一端到端链路复用真实库。

## 用户待定项与本次交付

1. “和原车 Autosteer 共用”是设置保留，还是要求实际 AP↔SP 切换？建议以 **B 的互斥 owner + 手动明确再接管**作为最小完整目标；A 不冒充 B。
2. 首批车辆的软件版本/2-bit或3-bit、HW代际、车型与线束是哪一种？本次未访问设备，不能从历史记录替用户确定。
3. 是否接受首版 stock ACC、协作参数固定、强 override 按当前安全策略退出，以及不迁移动态纵向/自动 AP 恢复？恢复旧 >5 Nm 门限如有需求须单独安全评审，不能偷偷改变当前底线。

本次只完成固定版本源码研究、差异与设计；文档分支从指定 B 创建。验证仅涵盖引用可解析、文档 diff/提交范围和推送读取；未实施插件，未做 CAN/设备/封闭场地验证，未改变任何设备 active commit。
