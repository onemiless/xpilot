# xpilot：规则与开发说明

所有人和所有 AI 动手改代码前必须先读这份文档。`AGENTS.md`（Codex 等）和 `CLAUDE.md`（Claude）都指向这里。

## 1. 项目是什么

- **定位**：sunnypilot（下称 SP）只适配 Tesla 的精简版，分支名 `xpilot`。
- **仓库**：系统 `github.com/onemiless/xpilot`（默认分支 `xpilot`），只放框架、商店和核心插件；其他插件在 `github.com/onemiless/xpilot-plugins`（默认分支 `main`），设备通过商店安装。推送分流见规则 15。
- **基线**：sunnypilot `922513f`，opendbc `5be750714`。只做这一次基准，之后不再整体合并上游，只手工挑修复（`tools/dev_sp_release/upstream_watch.py`）。
- **主体**：横向、纵向、模型都是 SP 原版，Tesla 车端代码是上游原版。
- **扩展**：SP 保持主栈，xpilot 通过插件钩子补充行为；可替换部分仅限第 4.3 节列出的 role。
- **现状（2026-10-23）**：插件扩展经过 `plugin.json` 发现、总开关、资源声明和出错隔离。雷达替换走 `radar_interface` 钩子（第 7 节），地图默认用 SP 的 OSM 进程，声明 `source:map` 的插件开启时让位（第 8 节），插件之间的数据走 `pluginMsg`（8d 节）。
- **已删除**：其他车型、comma 云、sunnylink、旧的大屏 UI、扭矩车用的横向控制、翻译（UI 只用英文）。
- **硬件**：C3、C3X、C3XL、C4。大屏设备上整屏是磁贴，行驶时 mici 画面固定在中间、磁贴排在四周（见第 9 节）。

## 系统版本与硬件镜像

目标系统统一为 **AGNOS 19.9**。`launch_env.sh` 与后台更新器使用同一个硬件清单：C3/tici 的 standard 配置选 `agnos-c3.json`，显式 c3xl 配置优先选 `agnos-c3xl.json`，C3X/tizi 和 C4/mici 选官方 `agnos.json`。三者的 system 条目相同。

C3/C3XL 保留各自锁定的 mr-one 引导链和写前白名单校验，禁止缺少专用清单时回退官方引导镜像。C3XL 的 Panda、驾驶员监控和音频能力策略不变。系统已为19.9时后台更新跳过刷写。本机检查只证明选择与写前保护；xpilot 的设备构建/运行验证须单独记录。

## 1b. 核心理念

1. **SP 主栈，插件扩展。** 可替换部分是第 4.3 节列出的 role；panda safety、CAN 发送限幅、资源裁决和故障锁定留在宿主，构成最小安全内核。核心插件（`sp_engagement`、`sp_models`、`sp_safety_fallback`）在系统仓库 `openpilot/tesla/plugins/<id>/`，其他插件在插件仓库的 `plugins/<id>/`。
2. **宿主定规则，插件只填内容。** 合并、裁决、限幅、出错隔离都写在宿主（`openpilot/tesla/`）里，插件改不了；插件只实现钩子。
3. **安全底线不可越过。** 车端检查、SP 的限幅和 panda safety 永远最后生效；插件再高的优先级也只能在这个范围内工作。
4. **出错只影响自己。** 一个插件坏了，只有它被停用，SP 和其他插件照常运行。
5. **先声明再使用。** 插件要用的 CAN 地址、进程、数据源和可替换环节都写进 `claims`；冲突由宿主按 `priority` 裁决，用户在插件市场里看得到。
6. **够用就好。** 钩子、参数、抽象只在真有插件需要时才加；UI 低频刷新、只在内容变化时重画。
7. **证据说话。** 先写失败模式和检查；本机检查、编译、safety 测试、设备实测分开报告，不能互相顶替。

## 2. 硬规则

1. **不改 SP 主体逻辑。** SP 文件里每个钩子点只加一行插件调用（循环、出错隔离等逻辑放在 `openpilot/tesla/` 里）。功能逻辑全部写在 `openpilot/tesla/plugins/<功能名>/`。
2. **不改上游 Tesla 车端代码。** `opendbc_repo/opendbc/car/tesla/*`、`opendbc_repo/opendbc/sunnypilot/car/tesla/*`、`opendbc/dbc/tesla_model3_*.dbc`、`opendbc/safety/modes/tesla.h` 保持上游原样。需要车端数据，就在插件里用自己的 `CANParser` 读。
3. **插件不能绕过车端和 panda 的检查。** 纵向：`long_target` 加速只能替换驾驶员设定车速（条件见第 4 节）；`plan_filter` 只能收紧；`role:long.planner` 可以整体替换纵向计划、包括跟车（用户 2026-10-22 决定），但加速度限制、FCW、踩刹车退出、原车 AEB 和 panda 限制不变。横向：`role:lateral.controller` 的输出不额外限制，靠车端转角速率限制和 panda 兜底。外接算力的数据只做建议。
4. **每个额外 CAN 发送地址都必须声明 panda 运行时规则**（第 6 节），固件校验内容、频率、时效和接管门。Tesla 自有地址只由原模式校验；没有规则的其他地址一律阻断。
5. **插件出错不能拖垮进程**：输入过期就当周期什么都不发。宿主兜底：插件在任何钩子里抛异常，就被记进 `XpilotPluginFaults`、本次进程内停用，其他插件和 SP 照常运行；plan filter 出错时计划照常发布。UI 磁贴和叠加层连续出错 3 次会停止绘制。
6. **Params**：插件的 key 写在自己 `plugin.json` 的 `params` 里，然后运行 `python3 openpilot/tesla/plugins/gen_params.py` 生成 `common/plugin_params.inc` 并一起提交；不属于插件的 key 才写进 `params_keys.h`。每个插件有一个 BOOL 总开关 `enable_param`，默认关闭；例外：只做显示、`claims` 为空的 `ui`、`overlays` 类插件（如 `speed_card`）可以默认开启；可替换核心环节的内置默认插件（如 `sp_models`）`enable_param` 为 `null`、一直开启，被替换时按优先级让位。设置项写在 `plugin.json` 的 `settings` 里。
7. **cereal**：只追加字段，不改已有字段的序号和类型。新消息用 `custom.capnp` 里的 `customReserved*` 槽位。
8. **验证**（沿用 `AGENTS.md`）：
   - 先列出失败模式，再写检查。
   - 优先做可重复的端到端检查。
   - 不准把静态检查说成编译、safety 测试或上车已通过。
   - 本地 commit 和设备上实际运行的 commit 分开报告。
9. **提交**：在 `xpilot` 分支上，一个连贯改动一个提交，只包含这次改动的文件。只有在用户要求时才推送。
10. **按插件拆分**：加功能就是在插件仓库加一个带 `plugin.json` 的插件目录。系统仓库的 `openpilot/tesla/plugins/` 只放核心插件，`manifest_check.py` M12 拒绝新目录。插件自动被发现，没有手写的注册表。插件内部按 5.3 拆分文件（入口 ≤150 行、其他 ≤400 行、只有 `ui.py` 画图），检查强制执行。
11. **只用声明过的资源**：插件只能使用 `plugin.json` 里 `claims` 声明过的资源；宿主会拒绝未声明的 CAN 发送、钩子和进程。
12. **车端数据只读、只经插件**：插件在 card 里用 `observe_can` 自己解析，再写进 `carStateSP` 里自己的字段（cereal 规则见第 7 条）；UI 和其他进程只读这些消息，不直接读 CAN。
13. **UI**：文字只用英文和 ASCII（设备字体没有 `‹ › · —` 等字）；磁贴只读消息、不写参数（设置写在详情页）；`hz` 按需要取最低值。
14. **不加新依赖**：只用标准库和仓库里已有的库。商店插件需要编译的部分以预编译二进制发布（见 8c）。
15. **推送分流**：插件的改动（`plugins/<id>/`、它的测试、`dist/`、`index.json`）只推到 `onemiless/xpilot-plugins`；系统的改动（宿主、商店、UI、核心插件、panda safety、`safety.json`、检查脚本、文档）只推到 `onemiless/xpilot`。一个功能两边都要改时分成两个提交、两个 PR，先合系统仓库。两个仓库的默认分支都受保护：走 PR，CI 通过才能合并（管理员可以直接推）。

## 3. 目录

```
openpilot/tesla/
  plugin.py                 Python 插件接口（Plugin 基类，所有钩子都有默认空实现）
  manifest.py               插件清单格式、类别、资源声明、冲突裁决（只用标准库）
  plugins/__init__.py       自动发现插件（PLUGINS），以及 card/plannerd/manager 用的调用宿主（总开关、冲突、资源检查、出错隔离）
  plugins/<id>/plugin.json  插件清单；plugins/<id>/plugin.py 插件代码；plugins/<id>/ui.py 可选，放磁贴和叠加层
  plugins/gen_params.py     把各插件清单里的 params 生成到 common/plugin_params.inc
  plugins/sp_models/        默认模型：SP 的模型管理器和 modeld（role:perception.model）
  plugins/sp_engagement/ sp_safety_fallback/   默认接管和安全兜底
  plugins/gen_safety.py     旧 registry 生成器，仅保留作迁移参考（第 6 节）
  store.py                  插件商店：下载、校验、安装；首次启动自动安装 DEFAULTS 里的显示插件（8c）
  ui/screen.py ui/tile.py ui/tiles.py ui/slots/   大屏磁贴宿主、磁贴基类、布局模型（无 pyray）、内置磁贴（slots/settings.py 设置页磁贴）
  ui/plugin_ui.py           UI 侧插件加载：找到 Tile、HudOverlay 和 ScreenOverlay，插件关闭时隐藏
  ui/plugins_page.py ui/store_page.py   mici 设置里的"Plugins"页和商店页（小屏用）
  ui/offroad.py ui/pages/   大屏停车时的详情页（见第 9 节）
  ui/settings_page.py ui/settings_meta.py        Tesla 设置页（上游 Tesla 选项、DM、地图）
opendbc_repo/opendbc/safety/plugins/
  plugin.h registry.h auto_speed.h ambient_light.h safety.json   旧测试 oracle/迁移参考
opendbc_repo/opendbc/safety/xpilot_rules.h   运行时引擎与验证器（第 6 节）
panda/board/xpilot_rules.h   app EP2 loader
.github/workflows/xpilot.yaml   CI：第 11 节的本机检查 + 插件仓库端到端
```

## 4. Python 插件接口（`openpilot/tesla/plugin.py`）

### 4.1 插件清单 `plugin.json`

```json
{
  "id": "my_feature", "name": "My Feature", "version": "1.0.0", "api": 1,
  "category": "longitudinal_planning", "use": "onroad", "entry": "plugin:MyFeature",
  "author": "someone", "description": "What it does and what it needs.",
  "priority": 100, "enable_param": "XpMyFeature",
  "claims": ["long.target", "can.tx:0x3C2:1"],
  "settings": [{"key": "XpMyFeatureLevel", "label": "Level", "range": [0, 10, 1, "", 1]}],
  "params": {"XpMyFeature": {"type": "BOOL", "default": "0"}, "XpMyFeatureLevel": {"type": "INT", "default": "5"}}
}
```

| 字段 | 含义 |
|---|---|
| `id` | 必须和目录名一致 |
| `api` | 插件面向的宿主接口版本，必须等于 `manifest.API_VERSION`（现在是 1），否则不加载 |
| `category` | 类别，见下表 |
| `use` | 必填：`onroad`、`offroad` 或 `both`；表示使用场景，Plugins 页显示。缺失或无效时带原因跳过。不改变钩子运行条件 |
| `entry` | 插件包内的 `模块:类名` |
| `priority` | 数字越小越优先：争同一个独占资源时它赢；同类插件的执行顺序也按它，数字相同按 id |
| `enable_param` | BOOL 总开关；`null` 表示一直开启。关闭时 card 不创建插件、进程不启动、plan filter/目标车速不加载、UI 不显示。车端下一次行程生效，UI 1 秒内生效 |
| `claims` | 要用的资源，见下表；没声明的一律被拒绝 |
| `settings` | 设置项：只有 key 和 label 是开关；带 `range: [最小, 最大, 步长, 单位, 除数]` 或 `choices: [[标签, 值], ...]` 是选择项 |
| `params` | 参数声明，格式见 `gen_params.py`；改完运行 `python3 openpilot/tesla/plugins/gen_params.py` |
| `rules` | `template_clone.v1` 规则列表，字段与限制见第 6 节合同；can.tx 必须精确匹配。旧 `safety` 字段一律拒绝 |
| `requires` | 可选能力需求：`{"hooks": ["setup"], "msg": ["lanes.hint"], "rule_abi": 1, "native": {"machine": "aarch64", "python": "cpython-312"}}`；每项可省略 |

清单有错（id 不对、类别未知、use 缺失或无效、api 不匹配、资源格式错、JSON 坏了）或代码导入失败的插件会被跳过，在 Plugins 页顶部显示 "Failed: <id>"，不影响其他插件。

磁贴声明须与 `use` 一致：`onroad` 不能有 `show="offroad"`，`offroad` 不能有 `show="onroad"`，`show="always"` 必须配 `both`。`manifest.tile_problems()` 是清单检查、商店安装和插件仓库检查共用的源码校验；安装拒绝不一致的包且保留旧版本。`use` 不授予或门控任何运行钩子。

能力预检统一调用 `manifest.compatible(manifest, HOST_CAPS)`，`api` 仍是 1，没写 `requires` 的旧插件继续有效。`hooks` 是 `Plugin` 方法名；`msg` 当前用 `*` 表示支持所有合法 kind 的 JSON 传输，不承诺 payload schema 或多发布者 broker。`native` 必须等于现有 `host_platform()` 的 machine/Python tag，旧 `platform` 字段也继续检查。未知能力、格式错误或不匹配都带原因拒绝安装、启用或加载；发现失败写进 `LOAD_ERRORS`，启用失败不改任何开关或故障标记。`RULE_ABI` 和 `HOST_CAPS["rule_abi"]` 为 `1`；要求其他规则 ABI 的插件带原因拒绝。旧 `safety.profile` 版本必须换成携带 `rules` 的新版，不作翻译。

系统 updater 在 finalized 树置为可激活之前，用该树自己的清单解析器和 `HOST_CAPS` 检查已安装插件，不导入插件代码。不相容列表（含原因）写入文件参数 `XpilotUpdateIncompatiblePlugins`（`/data/xpilot_plugins/.params/`，沿用 `ParamsView`），停车页逐项提示；不阻断系统更新。预检本身失败也提示，后续成功预检会清除旧提示。

card 每次启动用 `cloudlog.event("xpilot.drive_snapshot", ...)` 记录本行程的 system commit、active winners 的 id/version/sha256，以及存在时 `XpilotPandaRules` 的 `crc32`。插件 sha256 在发现时固定，按相对路径排序，对每个普通文件依次哈希 `路径 UTF-8 + NUL + 8 字节大端长度 + 内容`，排除 `__pycache__` 和 `.pyc`；它是内容摘要，不是商店压缩包摘要。快照不会证明 Panda 已加载或设备已部署。

### 4.2 类别（`manifest.CATEGORIES`，按控制链路顺序）

能力状态：**仅分类**表示目前只用于分组；**可补充**表示已有钩子可添加行为；**可替换**表示已有 role 可整体替换对应 SP 环节。类别本身不授予能力，具体以第 4.3、4.4 节的 claims 和钩子为准。

| 类别 | 用途 | 当前能力 |
|---|---|---|
| `perception_model` | 感知模型 | 可替换：`role:perception.model` |
| `object_fusion` | 前车和目标融合 | 仅分类 |
| `vehicle_params` | 标定和车辆参数 | 仅分类 |
| `engagement` | 接管状态机 | 可替换：`role:engagement.state` |
| `driver_monitoring` | 驾驶员监控 | 仅分类 |
| `lateral_planning` | 横向规划 | 可补充：`lat_correction` |
| `longitudinal_planning` | 纵向规划 | 可补充：`long_target`、`plan_filter`；规划替换限 `role:long.planner` |
| `lane_change` | 变道 | 可补充：`lane_change_policy`，限免推方向盘判断 |
| `navigation` | 导航驾驶 | 可补充：`source:map` + `processes()` 发布地图和导航数据 |
| `controller` | 控制器 | 可补充：`lat_correction`；横向控制器替换限 `role:lateral.controller` |
| `actuation` | 执行和车端报文 | 可补充：`control_sends`，受 panda safety 限制 |
| `safety_fallback` | 安全兜底和 Autopilot 交接 | 可替换：`role:safety.fallback`（Autopilot 交接还没做） |
| `data_source` | 数据源和外接算力 | 可补充：`observe_can`、`radar_interface`、`external_provider` |
| `alerts` | 报警 | 可补充：`alert_source` |
| `body_comfort` | 车身和舒适 | 可补充：`observe_can`、`control_sends` |
| `ui` | 磁贴 | 可补充：`Tile` |
| `overlays` | 行驶叠加层 | 可补充：`HudOverlay`、`ScreenOverlay` |
| `tools` | 工具和诊断 | 可补充：`processes()` |

仅分类的类别仍可使用通用钩子（`processes`、`alert_source` 等），但不能替换 SP 的对应环节。真有插件需要时再加专用钩子，不预先加。

有插件要做时，按下面的顺序考虑加钩子（越靠前越可能先用到、风险越低）：

| 顺序 | 类别 | 可能的钩子 | 理由 |
|---|---|---|---|
| 1 | `navigation` | 不需要新钩子：`source:map` + `processes()` 发布 `liveMapDataSP` / `navInstruction`（第 8 节） | 现有接口已够用，先写插件 |
| 2 | `vehicle_params` | 只读的参数修正（例如转向比、质量），在 card 发布 CarParams 前调用 | 影响小、可在本机验证 |
| 3 | `driver_monitoring` | 替换或补充 DM 的提醒策略（只能更严格） | Tesla 页已有 DM 选项，需求明确 |
| 4 | `object_fusion` | 在 radard 之后修正前车（`radarState`），只能让前车更近 | `radar_interface` 只覆盖原始雷达 |
| 5 | `lateral_planning` | 修正或替换模型给的路径 | 已有 `lat_correction` 和 `lat_controller`，多数需求可以先用它们 |
| 6 | `engagement` | 已做：`role:engagement.state` 整体替换普通状态机和 MADS | 默认插件 `sp_engagement`，宿主底线见 `core_host.py` |
| 7 | `perception_model` | 已做：`role:perception.model` 整体替换模型管理和推理进程 | 默认插件 `sp_models` |
| 8 | `safety_fallback` | 已做：`role:safety.fallback` 整体替换事件策略（Autopilot 交接还没做） | 默认插件 `sp_safety_fallback` |

### 4.3 资源声明（claims）和冲突

| 资源 | 含义 | 独占 |
|---|---|---|
| `can.tx:0x<地址>:<总线>` | 发送这个 CAN 地址（还需要 panda 运行时规则） | 是 |
| `process:<名字>` | 运行这个 manager 进程 | 是 |
| `source:<数据>` | 提供某类数据，如 `source:radar`、`source:lanes` | 是 |
| `source:radar` | 用 `radar_interface` 替换车型自带的雷达（第 7 节） | 是 |
| `source:map` | 提供 `liveMapDataSP` 地图数据，开启时停掉 SP 的 OSM 进程（第 8 节） | 是 |
| `role:<环节>` | 占用一个可替换的环节，如 `role:lateral.controller` | 是 |
| `long.target` | 使用 `long_target` 钩子 | 否，宿主按固定规则合并 |
| `plan.filter` | 使用 `plan_filter` 钩子 | 否，按优先级串联 |
| `alert` | 使用 `alert_source` 钩子 | 否，每个级别取优先级最高插件的第一条 |
| `lat.correction` | 使用 `lat_correction` 钩子 | 否，按优先级串联，总改动有上限 |
| `external.provider` | 使用 `external_provider` 钩子 | 否，所有 provider 一起运行 |
| `role:lane_change.policy` | 使用 `lane_change_policy` 钩子 | 是 |
| `role:lateral.controller` | 替换横向控制器（`lat_controller`） | 是 |
| `role:long.planner` | 替换纵向规划（`long_planner`），可以自带跟车逻辑 | 是 |
| `role:perception.model` | 整体替换模型（下载、选择、缓存、推理）。宿主 `ROLE_PROCESSES` 保留 `models_manager`、`modeld`、`modeld_tinygrad`、`modeld_warmup` 四个名字，`fork_processes.py` 把原进程对象交给宿主；只有声明该 role 的插件会收到 `role_processes(role, procs)`，可返回全部、部分或不返回，且必须是传入字典里的原对象（按身份校验）。返回的进程只在该插件持有 role 且原 `should_run` 条件成立时运行；无人持有时四个都不运行。默认 `sp_models`（优先级 10000、一直开启）返回全部；替换插件也可保持空选择、用 `processes()` 自带进程，不能借此占用这四个保留名。hook 抛异常或返回外来对象时记录故障、拒绝本次选择，其他插件继续注册。必须按原语义发布 `modelV2`、`drivingModelData`、`cameraOdometry`、`modelDataV2SP`。替换插件优先级要小于 10000；切换只在停车时改开关、下次启动进程时生效 | 是 |
| `role:engagement.state` | 用 `engagement(selfdrive)` 整体替换接管：普通状态机和 MADS。默认 `sp_engagement`（优先级 10000、一直开启） | 是 |
| `role:safety.fallback` | 用 `safety_fallback(ctx)` 整体替换兜底策略（哪些事件触发退出、禁止接管、软退出）。默认 `sp_safety_fallback` | 是 |
| `msg:<类型>` | 发布这一类 `pluginMsg` 消息（见 8d 节），如 `msg:lanes.hint` | 否，多个插件可发同一类，用 `source` 区分 |

- **冲突裁决**：两个已开启的插件声明了同一个独占资源时，`priority` 小的生效（相同则按 id），另一个整体不生效，Plugins 页显示 "conflict"。在 Plugins 页打开一个插件，会自动关掉和它冲突的插件。
- **合并规则由宿主固定**：插件改不了。目标车速的规则见下面 `long_target`。
- **底线**：不管优先级多高，都越不过 SP 的限制和 panda safety。

**必需角色**：宿主 `REQUIRED_ROLES` 固定为 `role:perception.model`、`role:engagement.state`、`role:safety.fallback`；`missing_roles(params)` 按这个顺序返回没有 active owner 的角色，关闭或冲突落败的插件不算 owner。停车页为每个缺失角色显示一条 `No <role> plugin: driving assistance unavailable`。Plugins 页拒绝关掉最后一个已开启的 claimant（包括打开别的插件时自动关掉冲突插件的情况），显示 `required role <role>`，不写参数；有其他已开启 claimant 时允许关闭替换插件。商店卸载按磁盘清单检查，若会删掉某必需角色最后一个已安装或内置 claimant，就返回同样原因并保留文件；还有内置默认插件或其他已安装 claimant 时允许卸载，不要求它已开启。当前三个 `sp_*` 默认插件的 `enable_param` 仍为 `null`、一直开启。

### 4.4 钩子

| 钩子 | 调用方 / 时机 | 用途 |
|---|---|---|
| `__init__(CP, params)` | card，识别车型后调用一次 | 创建 CANParser，读取参数 |
| `setup(CP_SP)` | card，发布 CarParamsSP 之前 | 设置 flags；额外 TX 权限通过运行时规则装载，不置插件 safetyParam 位 |
| `observe_can(can_list, now_ns)` | card，每批 CAN | 只读解析；返回需要立即发送的帧（少用） |
| `publish_state(CS_SP)` | card，每个周期 | 填写 carStateSP 里本插件的字段；也可以从这里读 `CS_SP.speedLimit` |
| `control_sends(CS, CC, now_ns)` | card，每个控制周期 | 返回要追加发送的 `CanData` |
| `service_params(params)` | card，约 10 Hz | 重新读取实时设置 |
| `processes()`（类方法） | manager | 额外进程。manager 相关的 import 写在方法内部 |
| `role_processes(role, procs)`（类方法） | manager | 选择宿主提供的 SP 角色进程，规则见 `role:perception.model`；默认返回空列表 |
| `plugins.send_msg(pm, plugin, kind, payload, valid_for_ms)`、`plugins.PluginMsgs().fresh(kind)` | 任何插件代码 | 发布和读取 `pluginMsg`（8d 节），每个 `kind` 要声明 `msg:<kind>` |
| `long_target(CP, params)`（类方法） | plannerd，每个计划周期 | 返回带 `update(sm, v_ego, v_cruise)` 的对象，给出目标车速（m/s）或 None。低于巡航车速：和 SP 自己的限速一样取最小；高于巡航车速：只替换驾驶员设定车速，且只在已接管、没踩油门/刹车、没 override 时生效，多个插件取最低，上限 `V_CRUISE_MAX`。MPC 跟车、模型和 SP 的限速功能仍然取最小，所以加速不会越过它们 |
| `alert_source(CP, params)`（类方法） | selfdrived，每周期（100 Hz） | 返回带 `update(sm) -> list[PluginAlert]` 的对象。`PluginAlert(severity, title, body)`，severity 为 `info`（无声）、`warning`（提示音）、`critical`（警告音）。插件报警在任何状态都显示，不会让系统退出，优先级最高到 MID，SP 自己的退出和严重报警永远优先。标题最多 60 字符，正文 80 |
| `lane_change_policy(params)`（类方法） | modeld 的 DesireHelper | 返回带 `allow(carstate, direction, default) -> bool` 的对象，只替换 SP 的"不用推方向盘就开始变道"这一个判断（`default` 是 SP 原来的结果）。打转向灯、最低车速、盲区和路沿检查仍然强制生效 |
| `lat_correction(CP, params)`（类方法） | controlsd，横向生效时每个控制周期 | 返回带 `update(sm, v_ego, curvature) -> 曲率或 None` 的对象。多个修正按优先级串联；总改动不超过 0.5 m/s² 横向加速度对应的曲率（低于 5 m/s 按 5 m/s 算），NaN/inf 被忽略；之后 SP 的 `clip_curvature`（ISO 加加速度和加速度限制）照常生效 |
| `lat_controller(CP, CP_SP, CI, params)`（类方法） | controlsd | 返回和 `LatControl` 接口一致的对象（`update(...) -> (steer, 转角, lac_log)`、`reset()`），替换 SP 的横向控制器。输出不和 SP 比较、不额外限制，只靠车端转角速率限制和 panda 兜底。SP 的控制器一直并行运行；插件出错或输出 NaN/inf 时，本次运行剩下的时间都改用 SP 的输出 |
| `long_planner(CP, CP_SP, params)`（类方法） | plannerd，SP 算完之后 | 返回带 `update(sm, sp) -> plan 或 None` 的对象。`sp` 是 SP 的结果（`speeds`、`accels`、`jerks` 各 17 个点，`a_target`、`should_stop`）；返回同样字段的 plan 就替换 SP 的计划，**包括跟车**。`a_target` 限制在 ACCEL_MIN/MAX；长度不对、NaN/inf 或 None 时保留 SP 的计划；出错的规划器被停用。SP 的 MPC 仍然运行，FCW 仍由它决定 |
| `plan_filter(CP, params)`（类方法） | plannerd | 返回带 `publisher(pm, sm)` 的对象，用来收紧纵向计划；不需要就返回 None |
| `engagement(selfdrive)`（类方法） | selfdrived，启动时一次 | 返回带 `state_machine`（SP `StateMachine` 接口）和 `mads`（MADS 接口，None 表示关闭 MADS 横向接管）的对象。插件可以保存、读取 `selfdrive`，只通过这两个输出改变接管，由宿主限幅；默认插件返回 `SelfdriveD.__init__` 已建好的 SP 实例，也走相同宿主路径。宿主底线（`openpilot/tesla/core_host.py`，用户 2026-10-23 决定）：底线里的 USER/IMMEDIATE 退出事件一定生效；接管必须有 SP 产生的驾驶员 ENABLE 事件（MADS：LKAS 按钮或屏幕手势，或普通 ENABLE）且没有底线 NO_ENTRY；`active` 必须和 `enabled`、状态一致；软退出最多 SOFT_DISABLE_TIME + 1 秒；`ET.PERMANENT` 一直显示（FCW、原车 AEB）；MADS 开启时按 LKAS 按钮一定退出；遵守 `Mads` 开关 |
| `safety_fallback(ctx)`（类方法） | selfdrived，启动时一次；返回对象每周期在 SP 的 `update_events` 之后执行 | 返回带 `update(ctx, CS, names, names_sp) -> (names, names_sp)` 的对象。`ctx = SimpleNamespace(sm, CP, CP_SP, params=view(params))` 由宿主只建一次，不暴露 `selfdrive` 或事件对象。返回两份已知事件名列表（保留或增加的事件）；宿主在原 `events` / `events_sp` 对象中重建，保持报警计数，再把底线事件放回。默认插件原样返回 SP 列表。底线：踩油门/刹车/取消（pedalPressed、buttonCancel、pcmDisable）、fcw、stockAeb、stockFcw、controlsMismatch(+Lateral)、relayMalfunction、canError、canBusMissing、selfdrivedLagging、commIssue(AvgFreq)、processNotRunning、selfdriveInitializing、DM 事件、lkasDisable。DM 锁定仍由 SP 处理 |
| `radar_interface(CP, CP_SP, params)`（类方法） | card，启动时 | 需要 `source:radar`。返回和 `RadarInterfaceBase` 接口一致的对象（`update(can_list) -> RadarData 或 None`），替换车型自带的雷达。原车雷达一直并行运行；插件出错后本次运行剩下的时间回到原车雷达 |
| `external_provider(params)`（类方法） | externald，启动时 | 需要 `external.provider`。返回一个 provider（`poll`、`send`、`close`），见 8b |
| `SERVICES` | plannerd、selfdrived、controlsd | 钩子需要额外订阅的服务；这些服务不参与存活和有效性检查，不会影响接管 |

SP 里的调用位置：`selfdrive/car/card.py`（`CarPlugins`、`radar_interface`）、`selfdrive/controls/plannerd.py`（`services`、`plan_filters`、`plan_sink`、`long_targets`）、`sunnypilot/selfdrive/controls/lib/longitudinal_planner.py`（`apply_long_targets`）、`selfdrive/selfdrived/selfdrived.py`（`alert_sources`、`update_alerts`）、`sunnypilot/selfdrive/selfdrived/events.py`（插件报警定义，见 `openpilot/tesla/alerts.py`）、`selfdrive/controls/lib/desire_helper.py`（`lane_change_policies`、`lane_change_allowed`）、`selfdrive/controls/controlsd.py`（`lat_corrections`、`apply_lat_corrections`、`lateral_controller`）、`selfdrive/controls/lib/longitudinal_planner.py`（`apply_long_planner`）、`sunnypilot/system/fork_processes.py`（`processes`，以及 `holds(params, "source:map")` 关掉 SP 的地图进程）、`selfdrive/selfdrived/selfdrived.py`（`install_core`）、`selfdrive/car/card.py`（`core_alive`：Tesla 安装宿主后始终门控，包括默认插件；启动尚无 `selfdriveState` 时不发控制，发布后恢复；断流约 0.1 秒内停止）。

**接管/兜底出错**：每个角色都只取当前 owner，没有 owner、钩子抛异常或返回 None、更新输出不合法（兜底非列表或未知事件名），本次行驶两轴都锁定为退出并显示 `xpilotPluginFault`（selfdrived 重启也保持，靠 `XpilotPluginFaults`），停车重启 manager 后才清除；行驶中不切回其他实现。兜底输出先整体校验，出错保持 SP 原事件和底线。宿主只认识角色，不认识默认插件 id。卡死由 card 的 `core_alive` 和 panda 心跳兜底。进程内插件是受信任的已审核代码，claims 不是沙箱，最终底线是 panda safety。

**模型角色缺失**：无人持有 `role:perception.model` 时，宿主不运行四个 SP 模型进程，也不新增接管机制。SP 已订阅 `modelV2` 并检查存活/有效性（`openpilot/selfdrive/selfdrived/selfdrived.py:113-120`）；正常通信检查中，`all_checks()` 不通过且 `all_alive()` 为假会产生 `commIssue`（同文件 `:439-450`），该事件已有 `NO_ENTRY` 和 `SOFT_DISABLE`（`openpilot/selfdrive/selfdrived/events.py:674-676`）。前面已有禁止接管和退出事件时沿用该事件；大模型加载和完成后的 5 秒等待沿用 SP 的暂缓条件。`processNotRunning` 只针对 manager 标为 `shouldBeRunning` 却未运行的进程（`selfdrived.py:412-418`），不能用它解释角色无人持有、进程按条件停掉的情况；缺少 `modelV2` 的通用门控是通信检查。以上是源码证据，不代表设备验证。

## 5. 写一个新插件

### 5.1 步骤

1. **先想清楚类型**，选最小的一种：
   - 只显示：一个 `ui.py` 磁贴或叠加层，读已有的消息。`claims` 为空，可以默认开启。
   - 读车端数据再显示：card 钩子 `observe_can` 解析 + `publish_state` 写到 `carStateSP` 的新字段，`ui.py` 磁贴订阅 `carStateSP`。参考插件仓库的 `plugins/bms/`。
   - 影响驾驶：用第 4.4 节的规划/控制钩子，声明对应 claim；能"只收紧"的就不要"替换"。
   - 发 CAN：`control_sends` + `can.tx` claim + panda 运行时规则（第 6 节）。参考插件仓库的 `plugins/auto_speed/`、`plugins/ambient_light/`。
   - 独立进程：`processes()` + `process:<名字>` claim。参考插件仓库的 `plugins/traffic_light/`、`plugins/external_compute/`。
2. **写失败模式**（在检查脚本或模块 docstring 里列出），再写检查：纯逻辑放进 `tools/sp_tesla_e2e/` 里已有的 `*_check.py`（或插件自己的 `tests/`），渲染和触摸只能在设备上用 `big_screen_e2e.py` 确认。
3. **建目录**：在插件仓库建 `plugins/<id>/`（只有核心插件放系统仓库的 `openpilot/tesla/plugins/<id>/`）：空的 `__init__.py`、`plugin.json`（4.1）、`plugin.py`、可选的 `ui.py`：

```python
# plugin.py
from openpilot.tesla.plugin import Plugin

class MyFeature(Plugin):   # 名字、开关、设置都来自 plugin.json，代码里不要写
  def observe_can(self, can_list, now_ns):
    for mono_time, frames in can_list:
      for address, data, bus in frames:
        ...                # 只读解析，记下最新一帧和时间
    return []

  def publish_state(self, CS_SP):
    CS_SP.myFeature.available = ...   # 过期就标成不可用，不要发旧值

# ui.py
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec

class MyTile(Tile):
  SPEC = TileSpec("my_feature", "My Feature", sizes=("small", "medium"), services=("carStateSP",), hz=2)

  def state(self, sm):              # 返回可比较的值；变了才重画
    v = sm["carStateSP"].myFeature
    return f"{v.value:.0f}" if v.available else None

  def draw(self, rect, size, state):
    self.draw_value(rect, state or "--", "unit")
```

4. **新消息字段**：加在 `cereal/custom.capnp`（只追加，见规则 7），设备上要重新编译（scons）。
5. **参数**：写进 `plugin.json` 的 `params` 和 `settings`。商店插件的参数存在 `/data/xpilot_plugins/.params`（8c），不用生成；核心插件才运行 `python3 openpilot/tesla/plugins/gen_params.py`，把 `common/plugin_params.inc` 一起提交。
6. **跑本机检查**（第 11 节），全部通过后按功能拆分提交；有 CAN 发送的在插件声明固件 ABI 支持的规则（第 6 节），先完成宿主装载、差分 safety 测试、MISRA 和固件构建；插件的 `plugin.json` 携带规则；最后上设备实测，报告设备上的 commit。
7. **不用注册**：插件自动被发现，插件市场按类别显示它的开关、版本、作者、简介和设置。插件仓库运行 `tools/build_index.py` 后发布（8c）。

### 5.2 API 一览

**清单和元数据**（`openpilot/tesla/manifest.py`）：`plugin.json` 字段见 4.1；`CATEGORIES`（类别和显示名）；`API_VERSION`（现在 1）；claims 格式见 4.3。宿主从清单填好 `Plugin` 的类属性：`NAME`、`TITLE`、`DESCRIPTION`、`CATEGORY`、`PRIORITY`、`ENABLE_PARAM`、`CLAIMS`、`SETTINGS`、`MANIFEST`（含 `version`、`author`），插件只读。插件自己可以设 `SERVICES`（钩子要额外订阅的服务）。

**Python 钩子**（`openpilot/tesla/plugin.py` 的 `Plugin`，全部有空的默认实现，详细规则见 4.4）：

| 进程 | 钩子 | 需要的 claim |
|---|---|---|
| card | `__init__(CP, params)`、`setup(CP_SP)`、`observe_can(can_list, now_ns)`、`publish_state(CS_SP)`、`service_params(params)` | 无 |
| card | `control_sends(CS, CC, now_ns) -> [CanData]` | `can.tx:...` |
| card | `radar_interface(CP, CP_SP, params)` | `source:radar` |
| manager | `processes() -> [ManagerProcess]` | `process:<名字>` |
| manager | `role_processes(role, procs) -> [ManagerProcess]` | 对应的 `role:<环节>` |
| plannerd | `long_target(CP, params)` | `long.target` |
| plannerd | `plan_filter(CP, params)` | `plan.filter` |
| plannerd | `long_planner(CP, CP_SP, params)` | `role:long.planner` |
| controlsd | `lat_correction(CP, params)` | `lat.correction` |
| controlsd | `lat_controller(CP, CP_SP, CI, params)` | `role:lateral.controller` |
| modeld | `lane_change_policy(params)` | `role:lane_change.policy` |
| selfdrived | `alert_source(CP, params)`，返回 `PluginAlert(severity, title, body)` | `alert` |
| externald | `external_provider(params)`，返回 `Provider`（`poll`、`send`、`close`） | `external.provider` |

`can_list` 每一项是 `(mono_time_ns, [(address, data, bus), ...])`；总线编号用 `opendbc.car.tesla.values.CANBUS`（`party`、`vehicle`、`autopilot_party`）。解析可以用位运算，也可以用 `opendbc.can.CANParser` 读 `opendbc/dbc/` 里的 DBC。

**参数**：钩子拿到的 `params` 是 `openpilot.tesla.plugin_params.ParamsView`：`get(key)`、`get_bool(key)`、`put(key, value)`、`put_bool(key, value)`、`remove(key)`，内置和商店插件用法一样。

**宿主工具**（`openpilot/tesla/plugins/__init__.py`，给宿主和页面用，插件一般不需要）：`PLUGINS`、`is_enabled(cls, params)`、`active(params)`、`resolve(params) -> (生效的插件, 冲突)`、`holds(params, claim)`、`FAULTS_PARAM`（`XpilotPluginFaults`）、`LOAD_ERRORS`。

**UI**（插件的 `ui.py`，无参构造，插件关闭时自动隐藏，连续出错 3 次停止绘制）：

| 类 | 接口 | 用途 |
|---|---|---|
| `ui.tile.Tile` | `SPEC`、`state(sm)`、`draw(rect, size, state)`；画图用 `face(rect)`、`draw_value(rect, value, unit, sub)`、`draw_lines(rect, lines, size)`，以及模块函数 `text(...)`、`text_w(...)`、`fit(...)`、`clip(...)`、`lines_in(...)` 和颜色 `DIM`；颜色只用 `Tile.COLOR` | 大屏磁贴 |
| `ui.tiles.TileSpec` | `id, title, sizes, services, show, hz, order`（见第 9 节） | 磁贴声明 |
| `ui.plugin_ui.ScreenOverlay` | `update(sm)`、`draw(zones)` | 大屏行驶时的整屏叠加层 |
| `ui.plugin_ui.HudOverlay` | Widget：`update()`、`render(rect)`；读 `ui_state.sm` | mici 行驶画面叠加层 |

**消息**（插件可以读或发布）：`carStateSP` 里插件自己的字段（例 `teslaBattery`）、`liveMapDataSP` 和 `navInstruction`（地图和导航，第 8 节）、`externalIn` / `externalOut`（外接算力，8b），以及 SP 的所有服务（`carState`、`radarState`、`longitudinalPlan`、`deviceState` 等）。

**panda safety**：插件声明 ABI v1 规则，固件统一验证和执行，见第 6 节；旧 C 插件接口只供差分测试。

**商店**（8c）：插件包和内置插件同样格式；代码按 `xpilot_ext.<id>` 导入、内部用相对导入；参数存在 `.params` 文件里；二进制要在清单写 `platform`。

### 5.3 插件代码结构规范（强制）

一个插件不能把所有代码写在一个文件里。按职责分文件：

| 文件 | 放什么 | 限制 |
|---|---|---|
| `plugin.json` | 清单：名字、类别、claims、参数、设置（4.1） | 名字、开关和设置只写在这里 |
| `plugin.py`（入口模块） | 只做钩子胶水：接收钩子参数，调用逻辑模块，返回结果 | 不超过 150 行 |
| `ui.py` | 磁贴和叠加层 | 只有这个文件可以 `import pyray` |
| 其他 `*.py` | 逻辑，按职责一个模块（例 `decode.py` 解析 CAN、`planner.py` 算目标） | 每个不超过 400 行；纯逻辑和 I/O（CAN、参数、消息）分开，纯逻辑才能在本机检查 |
| `tests/` | 插件自己的检查 | 不计行数 |

- 文件超过限制就按职责拆分，不要压缩代码、删注释或合并行来凑行数。
- 检查：核心插件由 `manifest_check.py` M11 检查（`manifest.layout_problems`）；商店插件在安装时检查，不符合就拒绝安装（`store_check.py` S16）。

## 6. panda 运行时规则（G1，ABI v1）

插件携带 `template_clone.v1` TX 权限规则，不签名；网络鉴权不属于系统任务。固件定义精确二进制格式、限制和错误码，合同见 [`docs/design/xpilot-panda-rules.md`](docs/design/xpilot-panda-rules.md)。最多 8 条、4 KiB 单缓冲区，无堆内存。每个 TX 位必须且只能归属于 copy、constant、field、counter 或 checksum 一项；装载时拒绝缺口、重叠、重复地址、未知 ABI/type、非 bus 1，以及 Tesla 模式拥有的地址。

Tesla 自有地址按地址独占，与 bus/length 无关（包括 0x488、0x2B9、0x27D、0x3DF 和 RX/转发列表）。它们只走原模式白名单和 hook，规则不能接管；其他地址有匹配规则才可能发送。规则全局门：Tesla 模式、有 vehicle bus、bus 1、无 relay malfunction、非 silent/noOutput。默认必须 `controls_allowed`。只有所有 field 值均声明为 exempt 的帧，才同时豁免接管门和会话预算；其余模板、TTL、内容、counter、checksum、间隔和全局门仍生效。

- auto_speed：0x3C2，idle mux-1 模板，TTL 1.5 秒，间隔 250 ms，每会话 50 格，空闲 3 秒重开；无豁免。
- ambient_light：0x679，TTL 1 秒，红色、亮度及 (zone,side) 元组，间隔 80 ms，每会话 150 帧/15 秒，空闲 1 秒重开；无豁免，必须接管。旧文档“允许未接管时告警”与旧头文件不符，已纠正。
- navassist：0x3E9，idle 模板、TTL 1.5 秒、模板 counter+1、通用 TESLA_SUM8、间隔 100 ms、首帧起 60 秒窗口最多 200 帧。(request,reason) 只能 (1,8)/(2,8)/(3,4)。cancel=(3,4) 只释放转向灯，允许未接管时发送；用户 2026-10-25 决定 cancel 不占用且不受会话预算限制。它仍受模板、校验和、counter、间隔、relay 和 noOutput 限制。

app 固件专用 CAPS/STATUS/BEGIN/COMMIT/CLEAR 及 EP2 64 字节记录见合同。主机必须先验证 CAPS 是 app，绝不向 bootstub EP2 写规则。BEGIN 立即撤销旧表，CRC/格式/上传失败后空表；只允许非 car mode 提交。启动和每次切入非 car mode 清表，每次设置模式重置模板和预算，切入 car mode 中止未完成上传。表为空或失败不改变基础 Tesla 接管条件。

宿主衔接（X-20261025-33）：card 编译已启用且裁决胜出的规则到 `XpilotPandaRules`；pandad 每循环只发一条 EP2 记录，保留心跳和总截止时间，在 0xDF/0xDC 前 COMMIT 并核对 STATUS CRC/count。失败 CLEAR、记录功能故障，基础模式仍正常 arming。没有规则也显式装载空表。插件不再分配 safetyParam 位；G14 固定位保留。旧 `safety.profile` 插件必须发布新版本替代，系统不提供翻译层；主机实现和模型验证不代表设备已部署。

`XpilotPandaRules` 是 BYTES：完整表紧接 4 字节 little-endian CRC32（只计算表，参数总长 20–4100）；没有 JSON 或额外长度前缀。唯一编码器为 `openpilot/tesla/panda_rules.py`，固件测试 fixture 也导入它。card 在 setup 后、CP/SP 发布前同步写入；没有规则也写空表。跨插件重复地址或总量超限，只丢弃当前插件并记录 `card.rules`，其他规则保留。旧 generator 读取入口只供旧 oracle 检查，生产发现、安装与编译均不读取 safety.json。

功能故障参数 `XpilotPandaRulesFault`（BOOL）在 manager 启动和停车时清除。规则持有者运行期任一 hook 异常视作关键故障，同步置位；pandad 在上传期和接管后检查，CLEAR 整表并保持基础模式；CLEAR 读回失败先设 NO_OUTPUT 清表再恢复基础模式。本行程不重新授予规则；表变更只在回到非 car mode 后重载。初始化/setup/编译拒绝发生在表写入之前，只停用该插件。observe_can 的即时发送和控制发送都受 `core_alive` 门控。上传每个 100 Hz 循环最多一条 EP2 记录，心跳保持 10 Hz；总期限两秒，相关传输限时 5 ms。CAPS 失败绝不写 EP2；STATUS 要独立核对 state/error/CRC/length/count。

生产固件只包含 `xpilot_rules.h` 引擎。`auto_speed.h`、`ambient_light.h`、`registry.h`、`gen_safety.py`、`safety.json` 暂留作测试 oracle/迁移参考，不再授予生产发送权限。X-33 落地、差分门通过后另行删除，不能在本任务删除。navassist oracle 固定为 Sniper-Eon/xpilot `ab3535b5a4acbf318c55005868d37ed19e2cf77d` 的测试 fixture，唯一故意差异是 cancel 会话豁免。

检查：`test_xpilot_rules.py`（失败矩阵 R1–R13、真实 C 引擎和 app loader、旧头文件差分）、`test_xpilot_plugins.py`、全部 opendbc safety、cppcheck MISRA、可用时 mutation、F4/H7/body 构建与 RAM/flash 对照。运行 `XPILOT_RULES_ARTIFACT=<路径> PYTHONPATH=opendbc_repo python -m unittest opendbc.safety.tests.test_xpilot_rules` 保存可重复的流摘要。主机检查、固件构建、设备 active commit 和刷写分别报告；本任务无设备访问。

Tesla 接管例外（用户 2026-10-25 决定）：`safety.h` 的最低频率检查遵守 `ignore_frequency_check`，时效、校验和、计数器和质量检查不变；SP 扩展允许原车 Autosteer 设置（mixed AP），以后由 mixed AP 插件接管，不改上游 `carstate.py` 或 panda 的 Autosteer 安全逻辑。

## 7. 雷达接入接口

写一个声明 `source:radar` 的插件，实现 `radar_interface` 钩子（见 4.4）。插件关闭、没有声明或出错时用原车雷达。外接雷达如果需要发送报文，还要声明 `can.tx` 并携带 panda 运行时规则。

`opendbc_repo/opendbc/car/car_helpers.py` 里的 `RADAR_PLUGINS` 是旧接口，已不用，保持为空（opendbc 不改）。

## 8. 地图接口

- **数据**：任何数据源只发布 `liveMapDataSP`，包括限速、前方限速、路名、`targetVelocities`（弯道目标速度）、`source`。
- **默认数据源**：SP 的 OSM（`mapd`、`mapd_manager`、`map_osm` 进程）。
- **新增数据源**：写一个插件，声明 `source:map` 和 `process:<名字>`，用 `processes()` 启动自己的进程发布 `liveMapDataSP`（可以继承 `sunnypilot/mapd/live_map_data/base_map_data.py`）。这个插件开启时，SP 的三个地图进程自动不启动；关掉插件就回到 OSM。
- **已知问题**：地图插件开启时，Tesla 页的 "OSM Map Data" 开关仍显示原值，但不起作用。
- **导航**：发布 `navInstruction`，再把 256×256 地图图像发到 VisionIPC 的 `mapsd` 流。

## 8b. 外接算力接口（插件仓库 `plugins/external_compute/`）

- **消息**：`externalIn`（外部 → 设备）和 `externalOut`（设备 → 外部），类型 `ExternalData`：`source`、`kind`（如 `llm.reply`、`vision.lanes`）、`seq`、`payload`（JSON 文本）、`validForMs`（0 表示不过期）。
- **只做建议**：外部数据不能直接控制车辆。要用它的插件订阅 `externalIn`，用 `provider.is_fresh()` 检查时效，再通过自己的钩子（如 `long_target`）和限制生效。
- **桥接进程** `externald`：插件开启时运行，把所有 provider 收到的消息发到 `externalIn`，把 `externalOut` 转给 provider。
- **provider 接口**（`provider.py`）：`poll(timeout)`、`send(msg)`、`close()`。现有 `tcp.py`：外部设备通过网线、USB 网卡或 WiFi 连到设备端口（默认 8770），按行发 JSON，第一行必须是 `{"hello": "<令牌>", "name": "..."}`。令牌在设备的 `/data/params/d/XpilotExternalToken`，首次启动自动生成。明文 TCP，只在可信网络上用。
- **新增 provider**（例如调用大模型 HTTP API）：写一个插件，声明 `external.provider`，在 `external_provider(params)` 钩子里返回一个 `Provider` 子类的实例。`external_compute` 自己就用这个钩子提供 TCP provider。`externald` 只在 `external_compute` 开启时运行。

## 8c. 插件商店（`openpilot/tesla/store.py`）

- **仓库**：`github.com/onemiless/xpilot-plugins`。`plugins/<id>/` 是源码，`tools/build_index.py` 生成可复现的 `dist/<id>-<版本>.tar.gz` 和 `index.json`（每个插件的 url 和 sha256）。第三方提 PR，维护者审核后合并。**暂不签名**：sha256 只防下载损坏或被替换，可信度来自审核过的仓库。
- **设备端**：Plugins 磁贴就是插件市场（见第 9 节），左下角 Refresh 拉取索引（第一次打开时自动拉一次）。只能停车时安装、更新、删除，重启后生效。
- **安装位置**：`/data/xpilot_plugins/<id>/`（可用环境变量 `XPILOT_PLUGINS_DIR` 改），系统更新不会动它。安装过程：限制大小下载 → 校验 sha256 → 只解出普通文件（拒绝 `../`、绝对路径、链接）→ 校验清单（id、api、入口模块、不能和内置插件同 id、参数名不能重复）→ 用改名的方式整体替换。任何一步失败，原来装的版本保持不变。
- **下载插件的代码**：作为包 `xpilot_ext.<id>` 导入，插件内部要用相对导入（`from .controller import X`）。
- **下载插件的参数**：不编译进 Params，而是存在 `/data/xpilot_plugins/.params/<key>`，支持 BOOL、INT、FLOAT、STRING、JSON。宿主把交给插件、开关判断和设置页的 params 都包了一层（`plugin_params.ParamsView`），插件照常用 `params.get/get_bool/put`。删除插件时参数值保留，重装后设置还在。
- **带二进制的插件**：需要编译的代码（例如 acados 求解器）以预编译二进制发布，清单里写 `"platform": {"machine", "python"}`（由插件自己的 `build_native.py` 在目标设备上生成）。设备只显示、安装和加载平台一致的版本；升级 Python 等导致不一致时，插件被跳过并显示 "Failed"。
- **发 CAN 的插件**：插件规则和 `can.tx` claims 必须一致，并符合设备固件 ABI（第 6 节）；旧 profile 版本由新版规则插件替代。
- **首次启动**：`store.DEFAULTS`（speed_card、lead_card、speed_limit_card、car_tile、map_tile、clock_tile、ip_tile、blindspot_edges）在停车且能连上索引时自动安装一次，装好后设 `DoReboot` 重启生效，并写标记 `/data/xpilot_plugins/.defaults_done`；索引连不上就 30 秒后重试，不写标记。用户之后删掉的插件不会再被装回。

## 8d. 插件间消息 `pluginMsg`

- **用途**：插件之间传数据的通用通道，不用再为每个插件加 cereal 字段。已有自己 cereal 字段的插件（如 `traffic_light`）保持不变。
- **消息**：cereal 服务 `pluginMsg`（`log.capnp` 的 `@139`，原 `customReserved13`），类型和外接算力相同，都是 `ExternalData`：`source`、`kind`、`seq`、`payload`（JSON 文本）、`validForMs`（0 表示不过期）。
- **发送**：`plugins.send_msg(pm, plugin, kind, payload, valid_for_ms)`，`pm` 要能发 `pluginMsg`，`plugin` 传插件类或实例。宿主填 `source` = 插件 id，插件自己改不了。清单里没有 `msg:<kind>` 声明的类型一律拒发，返回 False，并像其他越权一样记到 `XpilotPluginFaults`。
- pluginMsg 当前只支持一个实际发布端；同一进程也不得创建多个独立发布实例。多发布者冲突目前只做故障处理，不保证原发布者继续发送。
- **接收**：`plugins.PluginMsgs()` 订阅 `pluginMsg`（不合并），`fresh(kind)` 返回 `{来源插件 id: payload}`，每个来源只取最新一条，过了 `validForMs` 的不返回。
- **只做建议**：和外接算力一样，收到的数据只能通过接收方插件自己的钩子和限制生效。

## 9. UI（大屏，2160×1080，UI v4：磁贴）

整个大屏是 Windows Phone / Win 8 风格的磁贴（tile）。不画图的布局逻辑在 `openpilot/tesla/ui/tiles.py`（`tile_layout_check.py` 在本机测），宿主在 `ui/screen.py`，磁贴基类在 `ui/tile.py`。

- **网格**：8 列 × 4 行，格子 250 px，间隙 12 px（步距 262），整体居中，原点 `ORIGIN=(38,22)`。磁贴尺寸（列×行）：`small` 1×1、`medium` 2×2、`wide` 4×2、`large` 4×4。
- **两套布局**：停车布局（`show` 为 `always`/`offroad` 的磁贴，整屏 32 格）和行驶布局（`show` 为 `always`/`onroad` 的磁贴，绕开 mici 画面的 14 格），各自保存顺序、尺寸、位置和隐藏列表；磁贴在停车时编辑，行驶画面的位置也可在行驶时拖动。
- **排布**：先放固定了位置的磁贴（列表顺序；位置放不下、越界或压到 mici 画面就当作没固定），再把其余磁贴按列表顺序放进第一个放得下的位置。扫描顺序：停车时按列（第 0 列从上到下，再第 1 列……，Win 8 横屏）；行驶时按行、每行从两边往中间（第 0 列、第 7 列、第 1 列、第 6 列……），所以左右两边交替填充。前面留下的空洞会被后面的小磁贴填上；放不下的磁贴不显示（编辑模式里可以从"添加"重新放）。
- **行驶时**：mici 画面默认在 `(300,59,1560,699)`（第 1–6 列 × 第 0–2 行这块区域里按 536:240 居中，缩放约 2.91），这 18 格不放磁贴；磁贴绕着它排：第 0 列、第 7 列和第 3 行的第 1–6 列，共 14 格。没固定位置的磁贴在自己支持的尺寸里从保存的尺寸往小试，放得下就用。2026-10-25 用户决定覆盖旧的“画面固定”规则：从画面内开始拖动可移动画面，松手时吸附到整格，6×3 格的占位保持在网格内（列 0–2、行 0–1）。位置改变后沿用上述 first-fit 重排，覆盖的固定磁贴也重新找空位。其余触摸全部忽略，画面内点击也不交给 mici 控件；报警仍只由 mici 在画面内显示一次。
- **摄像头颜色**：接合与未接合都保持彩色；`cameraview.py` 固定 shader 的 `engaged=0`，保留原 shader 和驾驶员相机增强。
- **停车时**：显示停车布局。点一下磁贴打开它的详情页（有 `PAGE` 的）；长按（0.6 s）磁贴或空格进入编辑模式。
- **编辑模式**（只在停车时）：三个按钮 `Park | Drive | Done` 为 320×160 px、56 px 字号。三个大按钮不能竖排进一个空格，因此横排放在底部一行中间的四格；编辑行驶布局时，如果画面在下方，就改放顶部空行。编辑期间 `place/add_cell` 保留这四格，磁贴临时重排且不覆盖按钮、加号或 Driving view；退出后释放，不改保存的磁贴位置。Park/Drive 切换布局，Done 退出。编辑行驶布局时灰色画面仍可拖动吸附，HUD 框的交互优先于画面。选中磁贴可改尺寸、取消固定或拖动固定；点加号打开 Add tiles；点空白取消选中。
- **保存**：布局存在 `XpilotTileLayout`（JSON `{"park": {"tiles": [[id, 尺寸, 列, 行], ...], "hidden": [id, ...]}, "drive": {"tiles": [...], "hidden": [...], "view": [列, 行], "hud": {"插件 id": [fx, fy, visible]}}}`，列/行为 `null` 表示不固定；旧格式 `{"tiles": [[id, 尺寸]], "hidden": [...]}` 读作停车布局），不进 `params_keys.h`，由 `plugin_params.ParamsView` 存成文件 `/data/xpilot_plugins/.params/XpilotTileLayout`。读取时丢掉不认识的 id、不属于这套布局的磁贴和不支持的尺寸；新出现的磁贴按 (`order`, `id`) 用默认尺寸排到最后；插件关闭时它的磁贴不显示但位置保留。`drive.view` 缺失、格式错误或越界时回到默认 `(1,0)`；移动画面时两套磁贴布局一起保存。
- **加磁贴**：插件的写在 `plugins/<名字>/ui.py`；不属于插件的放 `openpilot/tesla/ui/slots/`。继承 `Tile`，写 `SPEC = TileSpec(id, title, sizes=..., services=..., show="onroad"|"offroad"|"always", hz=..., order=...)`，会被自动发现。磁贴没有自己的颜色：都使用宿主设置的 `Tile.COLOR`，行驶时为中性深灰，停车时为所选主题色。
  - `show` 必填、只接受上述三个值、没有默认值；`manifest_check.py` M13 检查内置声明，`store_e2e.py` E8 用同一检查扫描商店安装后的 `ui.py`。Park 的添加列表只含 offroad/always，Drive 只含 onroad/always。`tiles.add_cell/move/place` 始终避开当前 Driving view，不能把磁贴放入画面。
  - `sizes`：支持的尺寸，第一个是默认；要在行驶时显示必须包含 `small`。
  - `state(sm)`：读数据，返回这块磁贴要显示的内容（可比较的值）。最多每秒调用 `hz` 次；**只有返回值变了（或尺寸、行驶/停车配色变了）才重画**，其余帧画缓存的纹理。重画在 `host.viewport()` 里做，即 `begin_drawing()` 之前：屏幕帧中途切到磁贴纹理会让 GPU 把屏幕存出再读回，疑似 onroad 磁贴闪烁的原因。`draw(rect, size, state)` 画磁贴正面。
  - 外观用 `Tile.face(rect, ...)`（底色、左下角标题）、`draw_value`（大数字、单位、灰色副行）或 `draw_lines`，保持统一。
  - **文字自动适配尺寸**（`tile.py`、`tiles.py`）：不要自己算字号。字号随磁贴变大（`tiles.scale`：small 1，medium 及以上 2；标题 34/51 px），宽度放不下就缩小，最小 `tiles.MIN_FONT`（24 px），还放不下就截断加 `...`（`tile.clip`）。`draw_lines` 放不下的行从末尾丢掉，所以重要的行放前面。自己画文字时用 `tile.text`、`tile.fit`、`tile.clip`、`tile.lines_in`；不要直接调 `rl.draw_text_ex`：gui_app 会把它放大 FONT_SCALE 倍（1.16），而 `rl.measure_text_ex` 不放大，量出的宽度就不对，文字会被裁掉。文字只用 ASCII（设备字体缺 `‹ › · —` 等字，会显示成 `?`；`tile_layout_check.py` 检查）。
  - 连续出错 3 次停止绘制，不影响其他磁贴。
- **配色**：`tiles.THEMES`（Cobalt、Teal、Emerald、Crimson、Violet、Graphite、Black），在 Toggles 页的"Tile color"里选，存在 `XpilotTileTheme`（STRING，和布局一样存成 `.params` 文件，不进 `params_keys.h`）。宿主启动时和每次回到磁贴屏时读取，停车时设置 `Tile.COLOR` 并让所有磁贴重画；编辑栏的选中按钮也用这个颜色。Black 为纯黑底 `(0,0,0)`、没有任何主题边框，文字保持白色，停车和行驶一致；旧保存值 `Outline` 读作 Black。编辑框、加号和选择控件的线仍用于编辑反馈；其他主题行驶时 `Tile.COLOR=(32,32,32)`，文字保持白/灰色；每次行驶/停车切换都会使磁贴缓存失效，恢复停车主题色。
- **插件市场**（Plugins 磁贴，`pages/plugins.py`）：左边一列是 `manifest.CATEGORIES` 里有插件的分类（加载失败时最前面多一个 "Failed"），左下角是商店的 Refresh；右边是选中分类的列表：每个插件一行开关，标题下是 use（`both` 显示为 `onroad + offroad`）、版本、作者和简介，后面是商店里有、这次没加载的插件（Install）。点插件行（开关以外）打开插件页：开关、设置项、Source/Version/Author/Category/Use/Claims/Priority，商店下载的插件还有 Update/Remove。Source 说明能不能卸载：内置插件（代码在仓库里，随系统更新）只能关掉开关，不能卸载；商店插件可以 Remove。分类和行由 `offroad.market_sections` 生成（`offroad_pages_check.py` O12），分类未知的商店插件归到 tools。原来单独的 Store 子页已删除。
- **Tesla 页**（`pages/tesla.py`）：Vehicle 组第一行是 "Car model"，打开子页选 Auto 或 sunnypilot 车型列表（`car_list.json`）里的某一款 Tesla。选车型写 `CarPlatformBundle`（`{**car_list[名字], "name": 名字}`，和 params_migration 一样），card 下次启动时把 `platform` 当作固定指纹；Auto 删除这个参数，恢复自动识别。只在停车时可用。列表由 `offroad.tesla_models` 生成（O13 检查平台都在 opendbc 里）。
- **设置磁贴（内置）**：Status（原 Home）、Toggles、Models、Plugins、Tesla、Network、Device、Software、Developer，放在 `ui/slots/settings.py`（Models 是宿主设置磁贴 `ui/slots/models.py`，仅当 `sp_models` 生效时显示；它从核心插件移回宿主，使三个核心插件都能声明 `use="onroad"`，同时严格满足 tile/use 一致性；替换插件自带自己的设置磁贴，其 `use` 需包含 offroad），`show="offroad"`。`PAGE` 是 `ui/pages/` 里对应的原生页面（Models 是 `pages/models.py` 的 `ModelsPage`：当前模型和下载状态、小模型/大模型列表子页（默认模型，再按文件夹分组，收藏在前，按钮 CURRENT/QUEUED/SELECT）、取消下载、刷新列表、清除缓存；选择、刷新、清缓存只在停车时可用）。设置磁贴不再打开 mici 页面（`offroad_pages_check.py` O1 不允许 `MICI`）。
- **详情页（WP 风格）**：整屏。左上角是返回圆钮（线画的箭头，点击区 `BACK=(0,0,140,230)`），右边小号大写的上级名（`XPILOT` 或父页面名），下面是大号小写标题；内容区 `CONTENT=(140,230,1920,850)`，由宿主按屏幕像素直接画 `ui/pages/` 里的原生列表页（tici 的 `list_view`/`scroller_tici` 控件）。页面打开的子页（如 Plugins 的插件页、Tesla 的 Car model）压栈，返回一层层退回，退到底回到磁贴屏。
  - 每页一个模块：`openpilot/tesla/ui/pages/<页>.py`，带 `show_event` 的 Widget（`show_event` 时从 params 重新读状态）。共用部分在 `pages/base.py`。不画图的逻辑在 `ui/offroad.py`（视口模式、触摸映射、插件行、商店任务 `StoreJobs`、停车提示），`offroad_pages_check.py` 在本机测。
  - 页面内容和 mici 一致（同样的参数、动作和可用条件，行驶中或接合时不能改）。重置校准、重启、关机、卸载用 `ConfirmDialog`；这些 tici 弹窗压在导航栈上，在 mici 那一遍里隐藏；宿主 modal 模式只全屏画栈顶弹窗，不渲染背景详情页或磁贴屏，避免 NetworkUI 的 NEEDS_AUTH 每帧重置键盘。
  - Device 的子页都是原生页面（`pages/training.py`、`terms.py`、`regulatory.py`、`driver_camera.py`），用 `ListPage.open()` 打开；法规信息（`mici_fcc.html` 是 comma four 的标签）只在 mici 硬件上显示；训练指南和车内摄像头只在停车时可用，在 `hide_event` 和息屏超时时清掉 `IsDriverViewEnabled`（息屏后不再渲染；这个参数开着时 hardwared 不会进入 onroad）。`pages.base.back()` 调宿主的 `back()` 关闭当前页。
  - 宿主里已经没有 3 倍的 mici 页面；`FALLBACK_VIEW=(296,295,1608,720)` 只留给意外压到 main 之上的 mici 控件。
  - 页面调用 `self.open(None, None)` 表示返回上一层。
  - 开机引导：mici 的 `OnboardingWindow` 在栈里时，宿主不画 mici 纹理，改为全屏画原生 `pages/onboarding.py` 的 `OnboardingPage`（同样的参数：`HasAcceptedTerms`、`HasAcceptedTermsSP`、`CompletedTrainingVersion`、`DoUninstall`；拒绝前在页内再确认一次），完成后宿主 `pop_widgets_to(main)`，和 mici 的回调一样。
- **不画 mici 纹理**靠 `system/ui/lib/application.py` 里一行钩子：`host.viewport()` 返回 `None` 时跳过 `draw_texture_pro`。这行放在 `begin_drawing()` 之前，宿主在里面重画变化的磁贴纹理。此时 `to_logical` 把所有触摸映射到 mici 屏幕很远之外，下面继续运行的 mici 控件不会响应。这是纹理合成入口的改动；HUD 平移和报警绘制的最小改动见下条。
- **HUD 布局**：停车时编辑 Drive，Driving view 内同时显示已开启插件 `HudOverlay` 和内置 HUD 的有标签框。拖动移动，点击可隐藏/恢复允许隐藏的项；框不越出画面。内置项保存为 `drive.hud["builtin:<name>"]=[fx,fy,visible]`，插件仍用插件 id；左上角比例随画面移动保持。缺失、非数字、非有限、越界或非 BOOL 的条目回到原位置和可见状态。关闭插件时保留位置。`tiles.builtin_hud_boxes()` 提供默认占位；`plugin_ui.draw_builtin()` 只给 SP 原绘制函数传平移后的 rect，不改其绘制和状态条件；小屏无宿主时保持原调用。
  - 可隐藏：`torque_bar`（转向弧线）、`set_speed`（设定速度）。只允许移动：`steering_wheel`（接管状态、critical 图标及叹号、turn intent 一起移动）、`model_source`（加载/失败状态）、`blind_spot_indicators`（两侧一组）。编辑器显示 Move only，手写 `visible=false` 也不会隐藏安全指示。
  - `_draw_current_speed()` 在此基线没有调用，旧坐标超出 240 px 高画面；按用户补充不增加新元素。其他可分离内容：方向盘组内的 turn intent/critical 叹号；外层的 DMoji、纵向状态、置信度球、书签和导航不参与本次编辑。报警不进入布局；移除 `augmented_road_view.py` 中错误的大屏跳过条件，始终由 SP 在原处绘制。
  - 插件 `HudOverlay.default_rect(view)` 返回 mici 逻辑坐标的默认矩形，基类为 120×48。宿主在调用 `render(rect)` 前设置 Widget 矩形；固定绝对坐标的旧插件须在插件仓库适配。绘制裁剪到 Driving view；ScreenOverlay 继续使用画面外区域。`use` 不增加运行期门控。
- **整屏叠加层**：`ScreenOverlay`（`update(sm)`、`draw(zones)`），行驶时在磁贴之后绘制；`zones` 是当前行驶画面四周的 left/right/top/bottom 和 view 本身的屏幕矩形。出错隔离同上，插件关闭时隐藏。例：`blindspot_edges`。
- 已知：`NetworkUI` 自己建一个 `WifiManager`，mici 的 `NetworkLayoutMici` 只在 mici 设置里构造（大屏不构造），所以大屏进程里只有 Network 页那一个。
- Mac GPU 检查：`python3 tools/sp_tesla_e2e/big_screen_e2e.py --host --output <目录>`。运行真实宿主、NetworkUI、键盘、CameraView 的 PC shader、插件 HUD 渲染入口和触摸映射；替换设备服务、磁贴数据、图标和 NV12 摄像头输入；SP HUD 与插件 HUD 使用真实绘制入口；使用 macOS Arial 字体。保存 `result.json`、键盘、接合彩色、Park/Drive 大按钮、内置/插件 HUD 编辑与隐藏、HUD 移动后 onroad、Black 无边框 和画面移动前后截图，不连接设备、不发送 CAN、不写真实参数。证明主机合成与交互；设备字体、图标、实时摄像头和物理触摸仍须设备验证。
- 检查：`tools/sp_tesla_e2e/tile_layout_check.py`（网格、排布、行驶避让、编辑操作、手势、保存格式、ASCII、文字适配）、`offroad_pages_check.py`（本机）；渲染和触摸：`tools/sp_tesla_e2e/big_screen_e2e.py`（默认路径在设备上跑，`--host` 可在 Mac 跑上述用例；设备路径截图磁贴屏、编辑模式、每个设置详情页、插件市场的分类和插件页，检查停车时除 Models 外不画 mici 纹理）。

## 10. 现有插件

系统仓库只有核心插件：

| 插件 | 功能 | 参数 | panda |
|---|---|---|---|
| `sp_engagement` / `sp_safety_fallback` | 默认接管和兜底：返回 SP 已建好的状态机/MADS 和事件列表，与其他实现走相同宿主底线；装一个持有 `role:engagement.state` / `role:safety.fallback` 的插件并打开即替换，宿主底线见第 4.4 节 | 无（一直开启） | 无发送 |
| `sp_models` | 默认模型：SP 的模型管理器和 modeld 原样运行，只是归到插件下；装一个持有 `role:perception.model` 的插件（如 OP、Jetlink、Chipmunk 模型）并打开即替换 | 无（一直开启） | 无发送 |

其他插件（auto_speed、traffic_light、ambient_light、external_compute、bms 和大屏显示插件）的列表和说明在插件仓库的 `README.md`。它们保留原来的参数名，从旧版本升级的设备要把旧参数值复制到 `/data/xpilot_plugins/.params/`。panda 生产权限由第 6 节运行时规则提供；旧 `auto_speed.h` / `ambient_light.h` 只保留作测试 oracle。

### 插件仓库 use 补齐清单（2026-10-25）

只读检查 `onemiless/xpilot-plugins/main@9d713ffc99cfc1a90e281a239047069e9cfde26a`：以下 17 个源码清单均缺少 `use`，本任务不修改插件仓库。推荐值按钩子和现有磁贴的 `show` 推导；其中 op_models 兼有行驶模型角色和 offroad 设置磁贴，必须是 both。

| 建议 use | 插件 |
|---|---|
| `onroad` | ambient_light、auto_speed、blindspot_edges、external_compute、overspeed_warning、tesla_mixed_control、tn_long、traffic_light |
| `both` | bms、car_tile、clock_tile、ip_tile、lead_card、map_tile、op_models、speed_card、speed_limit_card |

商店索引目前含上述 16 个（tn_long 尚未发布），包内清单同样缺失 use。`store_e2e` 应以 E9/E1 报缺失声明、以 E2 报其安装失败导致的未发现；补齐源码后还须重建 dist/index 才能通过。索引未带 use 时市场显示 use not declared，已加载插件按经校验的清单显示。

## 11. 本机可以跑的检查

```
python3 tools/sp_tesla_e2e/agnos_update_check.py                     # 实际启动/OTA19.9选择和写前保护；分区IO模拟
python3 tools/sp_tesla_e2e/static_imports.py                         # 有没有断掉的 import
python3 tools/sp_tesla_e2e/tile_layout_check.py                       # 大屏磁贴：网格、排布、行驶避让、编辑操作、手势、重画判断、ASCII 文字、文字适配磁贴
python3 tools/sp_tesla_e2e/plugin_host_check.py                       # 插件宿主：出错隔离、总开关、元数据、参数、目标车速、雷达/地图/外接 provider、pluginMsg
python3 tools/sp_tesla_e2e/manifest_check.py                          # 插件清单、资源声明、冲突裁决、代码结构（5.3）、只放核心插件（M12）
python3 tools/sp_tesla_e2e/safety_manifest_check.py                   # rules 清单、位覆盖、所有权、表大小及 G14 编码
python3 tools/sp_tesla_e2e/phase2_check.py                            # 插件报警、变道策略、横向修正
python3 tools/sp_tesla_e2e/phase3_check.py                            # 可替换的横向控制器和纵向规划
python3 tools/sp_tesla_e2e/phase4_check.py                            # 可替换的接管和安全兜底：宿主底线、出错锁定、card 门控
python3 tools/sp_tesla_e2e/panda_rules_check.py --output /tmp/panda-rules # C++ host → T32 真实 C loader；先 scons 构建 pandad/tests/test_panda_rules
python3 tools/sp_tesla_e2e/store_check.py                             # 插件商店：下载、校验、安装、代码结构、参数（本机 HTTP 服务）
python3 tools/sp_tesla_e2e/offroad_pages_check.py                     # 大屏停车页面：页面模块、参数名、插件行、商店行、视口、触摸映射
python3 tools/sp_tesla_e2e/store_e2e.py --repo ~/xpilot-plugins       # 插件仓库端到端：索引 → 安装 → 发现 → 钩子生效
python3 tools/dev_sp_release/upstream_watch.py                        # 列出值得 cherry-pick 的上游提交
```

CI（`.github/workflows/xpilot.yaml`）在每个 PR 上跑以上检查（`upstream_watch` 除外）和 `gen_params.py`/`gen_safety.py --check`。环境：Python 3.12，`pip install pyzmq numpy pycapnp pytest zstandard setproctitle cython scons setuptools`，`git submodule update --init msgq_repo`，在 `msgq_repo` 里 `scons msgq/ipc_pyx.so`，`PYTHONPATH=msgq_repo`。插件自己的测试和外接算力 TCP 桥接检查在插件仓库的 CI 里。

编译、safety 测试、UI 渲染和上车验证只能在 Mac 或设备上做。

## 授权说明（给用户）

要让 AI 修改 panda safety 核心文件，在仓库的 `.claude/settings.json`（或 `~/.claude/settings.json`）里加：

```json
{ "permissions": { "allow": ["Edit(opendbc_repo/opendbc/safety/**)", "Write(opendbc_repo/opendbc/safety/**)"] } }
```

加完后重启会话。safety 的改动必须先编译并跑测试，再上车。
