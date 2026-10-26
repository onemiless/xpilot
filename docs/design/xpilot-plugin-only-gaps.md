# X-20261025-21：离“后续功能只安装商店插件”还缺什么

2026-10-25，只读源码审计；唯一仓库改动为本文。未访问设备、未实现接口、未运行控制 E2E/固件构建，也不声明设备 active commit。

## 结论与固定证据

目标可以覆盖**宿主已提供能力范围内**的新功能，不能承诺任意新协议、硬件或安全语义永远不升级系统。硬阻塞主要是新 CAN 安全能力、执行源/交接、正式参数/DM语义及核心schema；共享消息汇聚、统一网络入口是优先基础建设，但其中有纯插件的过渡路径。路径 1 的 coop 算法已经可以先做商店插件。

| 标记 | 固定版本；下文 `标记:路径:行号` 均相对此版本，不指工作区最新文件 |
|---|---|
| S | `onemiless/xpilot`，`task/X-20261025-18` = `55a1f377367a564276baf06e6394834bd549d857`；本分支从它创建 |
| P | `onemiless/xpilot-plugins/main` = `723e9312bb89a8b6cf58a1a081d38dfc6219cd74` |
| N | [插件 PR #1](https://github.com/onemiless/xpilot-plugins/pull/1)，head `85dff86174a83d48f4cd2dd905fddbd30aba5283`（`Sniper-Eon/xpilot-plugins`）；尚未合并 |
| F | [系统 PR #1](https://github.com/onemiless/xpilot/pull/1)，head `ab3535b5a4acbf318c55005868d37ed19e2cf77d`（`Sniper-Eon/xpilot`）；尚未合并 |
| D | `onemiless/xpilot/task/X-20261025-20` = `a761d21e7b33e6b89f00a93e25a3b8bbfe52b622`，`docs/design/xpilot-mixed-control-plugin.md`；其中 A/B 对应本任务路径 1/2 |

用户已定：规则随插件发布、运行时加载到 Panda，**不签名**；安装/启用者负责。固件只解释预定义规则类型；Tesla mode 已拥有的 0x488、0x2B9 等必须通过原 mode TX 检查，规则只能再收紧。首次加入规则引擎可升级固件。本文据此取代 D 中“每个插件新增 C header/profile”的发布方案，保留其交接底线（D:`docs/design/xpilot-mixed-control-plugin.md:106-113`）。配对认证属于网络输入边界，与规则不签名是两回事。

## X-20261025-33 状态更新（主机，无设备）

G1 host 已接通：清单 `rules` ABI 1 → card 编译 active winners → 二进制 `XpilotPandaRules` → pandad app CAPS 预检/非 car mode 分步 EP2 装载 → COMMIT/STATUS 摘要核对。失败清表并置 `XpilotPandaRulesFault`，基础模式照常配置；旧 safety.profile 拒绝，没有翻译或插件 param_bit。T32 固件格式沿用 [`xpilot-panda-rules.md`](xpilot-panda-rules.md)。商店插件重发属于 X-34，尚未包含于此系统改动。

G12 本次触及的边界已实现：规则持有者运行期关键故障撤销整张 Panda 表；observe_can 发送受 core_alive 门控。P45-P48、C20、H1-H14 提供主机检查和真实 C loader 模型 trace。下文 G12 的 plan_filter 单调收紧/恰好一次发布及其他 G3/G5 边界仍未关闭；不将这次局部撤权称作全局 floor 完成。未连接设备、未刷写，设备 active commit 未知。下表保留原审计固定版本证据，不代表当前源码仍处于原状态。

## 草稿校正：硬阻塞与基础建设分开

| 草稿 | 审计结论 |
|---|---|
| G1 | 确认硬阻塞；已有profile可复用，新报文/规则类型仍需系统。最终目标是一次引擎支持范围内只更新规则数据 |
| G2 | 认证和路由的一致性缺口，**不是新网络插件必改系统的硬约束**：插件现在就能写认证，或实现external_provider复用现有有token的TCP桥；统一强制边界才需提升到宿主 |
| G3 | 确认硬阻塞；现有append不能完成真实AP单源交接，raw CAN claim也不解决OEM forwarding |
| G4 | 已有独占fallback，不必为单个策略新增hook；共享窄filter仅为多个策略共存，且只能显式allowlist删事件 |
| G5 | 确认多publisher冲突；**合作插件也可先共用一个商店router进程/本地IPC**，不改cereal。要使现有send_msg及所有producer透明受管才需host/broker改动；路由服务不必与网络捆绑 |
| G6 | 标准详情页确实缺text，STRING存储已有；大屏插件可用Tile.PAGE自带页面先落地。通用两套UI、配对/secret体验统一才改宿主 |
| G7 | object_fusion/vehicle_params/DM仅分类；lateral_planning已有受限修正。先补有具体探针的参数/DM合同，不因分类存在就给每类造完整替换role |

对应源码证据及风险见下方同ID表；“建议补宿主”不等于“功能当前绝无纯插件实现”。

## 探针逐段追踪

“需 S”指功能按当前代码落地会触及系统仓库；“可 P”指已有正式钩子足够，不包含绕过 claims、monkey patch 或多发同一地址。

| 探针 / 阶段 | 当前链路与判定 |
|---|---|
| navassist：安装 | N manifest 声明进程、四个 msg kind、long.target、lane_change.policy、0x3E9；S 只内置两个 safety profile，因此**连关闭转向灯选项也不能让整个包通过安装**：claims 必须完整匹配 profile。需 S/G1，或另发无 CAN 版本的 P 包；即使合F，0x8000经CP_SP发布还有G14高位溢出。不能把N当作S已有能力。N:`plugins/navassist/plugin.json:12-26`；S:`openpilot/tesla/manifest.py:138-158`、`opendbc_repo/opendbc/safety/plugins/registry.h:5-13` |
| navassist：手机 → UDP | navassistd 自开 `0.0.0.0:4213`；8 KiB、每来源 20/s、严格 schema/session/seq/TTL 已有，但来源只是 IP 的哈希，没有配对身份。原样收包**可 P**；统一认证入口需 S/G2，不能说“完全没有边界检查”。N:`plugins/navassist/process.py:48-58`、`plugins/navassist/udp.py:14-35`、`plugins/navassist/udp.py:54-98`、`plugins/navassist/rate.py:10-28` |
| navassist：状态 → 跨进程 | 一个 PubMaster 发布 `nav.state`、`lanes.topology`、`lanes.intent`、`oem.lanes`，没有新 cereal 字段，**可 P**。第二个独立插件也发布 pluginMsg 时遇单发布端约束，需 S/G5。N:`plugins/navassist/process.py:57-108`；S:`openpilot/tesla/plugins/__init__.py:270-301`、`XPILOT.md:383-385` |
| navassist：速度 → SP long | `NavSpeedTarget` 读新鲜 nav.state，返回速度上限 → `apply_long_targets` → SP candidates 取最小 → MPC/LongControl → 原 0x2B9。**可 P**，前提是本次启动已配置 SP long；它不会把 stock ACC 变成动态 SP long。N:`plugins/navassist/target.py:18-45`；S:`openpilot/sunnypilot/selfdrive/controls/lib/longitudinal_planner.py:63-68`、`openpilot/tesla/plugins/__init__.py:337-355`、`opendbc_repo/opendbc/car/tesla/carcontroller.py:50-62` |
| navassist：策略 → 变道 | policy 读 OEM/intent/phone veto → DesireHelper 的免推方向盘判断；灯、最低速度、原盲区/路沿仍检查，**可 P**。policy 返回 false 不能阻止驾驶员扭矩分支，也不覆盖已开始阶段；强 veto/中途 abort 是额外能力 G13。N:`plugins/navassist/policy.py:55-73`；S:`openpilot/selfdrive/controls/lib/desire_helper.py:62-95` |
| navassist：灯 → 车辆 → UI | `observe_can` 留 idle 模板，`control_sends` 造 0x3E9，card append 后由 Panda 拦/放；回读物理灯给 DesireHelper，ScreenOverlay 自读 pluginMsg。Python/显示**可 P**；Panda 新地址需 S/G1。F 已用 C header 实现模板/校验和/计数/100 ms/200帧每60s及 active controls gate；cancel=3 可在 disengage 后关灯。N:`plugins/navassist/plugin.py:27-68`、`plugins/navassist/turnsignal.py:22-53`、`plugins/navassist/turnsignal.py:65-151`、`plugins/navassist/ui.py:25-49`；F:`opendbc_repo/opendbc/safety/plugins/navassist.h:13-103` |
| mixed 路径 1：coop | 商店安装 → setup 清原 COOP flag → `role:lateral.controller` → 原 Tesla controller 限幅/50 Hz 0x488 → 原 Panda mode。**可 P，无新固件**；100 Hz hook 与旧 50 Hz、限幅前后位置、异常回 SP 跳角必须回放证明。若不能满足原算法时序才升到 G3。D:`docs/design/xpilot-mixed-control-plugin.md:94-100`；S:`openpilot/tesla/plugins/__init__.py:449-484`、`opendbc_repo/opendbc/car/tesla/carcontroller.py:30-48` |
| mixed 路径 2：真实 stock AP 共用 | OEM 观察/角度算法**可 P**；AP owner → 抑制 SP 0x488 → 确认 OEM forwarding → idle/驾驶员明确再接管 → 同步 carOutput，缺 G3/G1 的固件 owner 原语。`stockLkas` 不是通用 AP active，光删 invalidLkasSetting 不完成交接。首版 stock long，不夹带动态纵向。D:`docs/design/xpilot-mixed-control-plugin.md:102-126`；S:`openpilot/selfdrive/car/card.py:261-266`、`opendbc_repo/opendbc/safety/modes/tesla.h:217-230`、`opendbc_repo/opendbc/safety/modes/tesla.h:335-357` |
| auto_speed：重述为 rules | 参数/只读 CAN/速度反馈 → `control_sends` 的单格滚轮 → 0x3C2:1。已有 profile 的新 Python 实现现在**可 P**；将 header 改成数据规则才需一次 S/G1。保持启动时 SP long+vehicle bus、idle mux1 模板、±1 tick、1.5s、250ms、50格会话/3s空闲、controls gate。P:`plugins/auto_speed/plugin.py:36-85`；S:`opendbc_repo/opendbc/safety/plugins/auto_speed.h:13-73` |
| ambient_light：重述为 rules | 自读灯模板/昼夜，CS 盲区 → `observe_can` 或 `control_sends` → 0x679:1。当前 profile 的 Python 变化**可 P**；规则化需一次 S/G1。保留1s模板、红色/亮度≤100、zone/side组合、保护位、80ms、150帧且15s/1s空闲。**代码实际还要求 controls_allowed**，与 XPILOT §6“未接管也可告警”冲突；迁移先保留代码语义，放宽必须另作明确变更。P:`plugins/ambient_light/plugin.py:27-54`；S:`opendbc_repo/opendbc/safety/plugins/ambient_light.h:14-76`、`XPILOT.md:326-327` |
| 动态 ACC/AP hybrid long | 策略/目标速度**可 P**；在 stock 与 SP long 间运行中切换还缺 G8：CP 决定控制器发送分支，controlsd 决定 longActive，pandad 每行程配置一次，Tesla mode 的 long 位决定非 cancel TX 和 OEM 0x2B9 转发。需 S+固件 owner 原语，long_planner/long_target 无法完成。S:`opendbc_repo/opendbc/car/tesla/interface.py:39-42`、`openpilot/selfdrive/controls/controlsd.py:99-117`、`openpilot/selfdrive/pandad/panda_safety.cc:5-19`、`opendbc_repo/opendbc/safety/modes/tesla.h:289-325`、`opendbc_repo/opendbc/safety/modes/tesla.h:350-379` |
| DM policy | 只显示额外提醒用 alert_source，或独占 safety.fallback 增加**已知**退出事件，已经**可 P**；替换视觉 DM 的阈值/状态/lockout 不行。dmonitoringd 固定构造 DriverMonitoring，selfdrived 读其结果，需 G7a 的只收紧策略钩子；不能靠第二个进程争发 driverMonitoringState。S:`openpilot/selfdrive/monitoring/dmonitoringd.py:12-34`、`openpilot/selfdrive/selfdrived/selfdrived.py:257-279`、`openpilot/tesla/plugin.py:73-77`、`openpilot/tesla/core_host.py:70-101` |
| vehicle_params correction | CP/CP_SP 已在 get_car/CI 构造并缓存后才创建 CarPlugins。`self.CP` 是共享可变对象，setup 也能改 CP_SP，**并非 Python 完全改不了参数**；但没有字段权限、依赖重算及各进程一致性合同。正式校正需 G7b；不能把临时改 self.CP 或往 Params 写一份 CP 当作完成。S:`opendbc_repo/opendbc/car/car_helpers.py:177-187`、`opendbc_repo/opendbc/car/interfaces.py:106-117`、`openpilot/selfdrive/car/card.py:114-160`、`openpilot/tesla/plugin.py:38-44` |

## 缺口优先级与最小宿主能力

P0：先铺插件发布基础（含现有位域编码G14）；P1：对应实际控制/配对探针再做；P2：特定插件明确需要时做。FW 指需要新固件并刷入；纯 loader/host/UI 更新仍是系统发布，不等于刷 Panda。未来规则超出固件已有类型时仍须升级固件。

| ID / 优先级 | 今天什么会坏；源码依据 | 最小关闭方式 | FW | 风险与留在宿主的 floor |
|---|---|---|---|---|
| **G1 / P0 runtime rules + loader** | 新 CAN 行为必须匹配编译 profile；一 profile/一地址/一 uint16 位，registry 静态 include；现 dispatch 命中插件后跳过 mode TX，无 runtime upload。S:`openpilot/tesla/manifest.py:138-158`、`openpilot/tesla/plugins/gen_safety.py:46-81`、`opendbc_repo/opendbc/safety/safety.h:230-247`、`openpilot/selfdrive/pandad/panda.cc:50-55` | 插件内规则文件；宿主验证声明/资源→只加载本行程 active winners→Panda 有界解析器、规则表、版本/类型查询、原子提交/摘要读回、复位/撤销。新增地址只按预定义类型放行；mode 自有帧为 mode TX **AND** 附加规则，先无副作用预检，再 mode 检查，最后提交规则计时状态 | **y，一次引擎+传输；新类型另需** | 规则无签名；不接收 C/脚本/任意字节码。noOutput、relay、controls、AEB、模式合法性、输入独立校验、时效/频率/会话预算固定在固件；不能通过另一长度/总线绕过自有地址判断 |
| **G14 / P0 safetyParam高位编码** | navassist的0x8000超出cereal Int16正数范围；generator却允许至0x8000，setup把正整数原样OR后，真实converter直接new_message。S:`openpilot/cereal/custom.capnp:393-398`、`openpilot/selfdrive/car/helpers.py:38-48`、`openpilot/tesla/plugins/gen_safety.py:56-58`；N:`plugins/navassist/plugin.py:27-31` | 明确现有16bit位域的有符号传输编码/解码，保持字段序号/类型；runtime rules转独立版本化加载协议，不继续每插件烧一个param位 | n | 本次真实转换检查：3个低位组合往返成功，0x8000/0x8001/0xe001均KjException；不是仅静态猜测，也不是整车E2E。未知位/旧固件拒用的floor保留 |
| **G5 / P0 pluginMsg 汇聚** | shared msg claim 不等于多 publisher；send_msg 直接用调用方 pm，未填 seq；reader 支持 TTL=0 永不过期。S:`openpilot/tesla/plugins/__init__.py:270-301`、`XPILOT.md:383-385` | 一个宿主 broker 独占 cereal publisher；各进程用有界本地 IPC 投递，宿主按已启用插件/claim 写 source、seq；消费保留 per source/kind。消息可声明 payload schema 版本，控制类强制有限 TTL；独立于网络使用 | n | 限单来源与全局队列/速率/大小、重放和来源选择；consumer 不再取任意 first source。默认 seq=0 不可当已有防重放能力；claims 不提供进程安全隔离 |
| **G2 / P0 统一网络 ingress** | N UDP 无认证；但 P TcpProvider **已有** token、4 clients、64KiB、50Hz、5s hello，不能重造一套或说全无认证。externald 随 external_compute 插件运行，不是宿主通用服务。P:`plugins/external_compute/tcp.py:19-39`、`plugins/external_compute/tcp.py:62-121`、`plugins/external_compute/plugin.py:16-28` | 将现有 bridge/认证边界提升为宿主服务，声明 plugin/kind 路由；复用 TCP 的限制机制，手机 UDP 配对绑定身份并验证数据完整性/seq/session，按插件投递 G5；协议业务解析仍归插件 | n | 配对/撤销、每 peer/全局限额、有限 freshness、非阻塞队列在宿主；明文 token TCP 只认证、不保密。插件仍是受信代码，不能声称本服务阻止插件自己开 socket |
| **G9 / P0 能力/版本预检与更新事务** | api 仅精确等于1；任意合法 role 字符串可 parse，未必有调用点；machine+Python tag 不覆盖 libc/native/system schema/rule engine，系统更新只切系统树。S:`openpilot/tesla/manifest.py:26-65`、`openpilot/tesla/manifest.py:126-158`、`openpilot/tesla/manifest.py:213-220`、`openpilot/system/updated/updated.py:359-386` | 保留 API major，增加插件所需**已命名能力及版本**（hook/rule types/消息合同/native ABI）；install、enable、manager启动、系统更新激活前统一预检。以系统版本+插件包hash+规则hash记录一份本行程快照，更新不相容则说明原因并保留可回退组合 | **n；查询新引擎依赖 G1** | 磁盘 safety.json 不能证明 Panda 实物能力；以读回版本/规则摘要门控。升级不能让关键插件静默消失后自动换控制 owner；核心 required roles 和故障锁定继续在宿主 |
| **G12 / P0 新接口必须实守 floor** | 文档说 plan_filter“只能收紧”，但 host 只包装 publisher，未比较前后值；插件异常还可能一次双发。hook异常隔离是每进程局部，`record_fault`不是全局即时撤权；card.core_alive 不覆盖 observe_can。S:`openpilot/tesla/plugins/__init__.py:119-128`、`openpilot/tesla/plugins/__init__.py:417-446`、`openpilot/selfdrive/car/card.py:175-176` | 在新增 G1/G3/G5 的共享边界集中验证结果/存活与撤权；plan_filter 改成宿主接收结果后验证单调收紧、finite及恰好一次发布；冻结行程 owner，关键插件 fault 关闭其控制 lease。保持普通只读插件的局部隔离 | **n；Panda撤权/lease依赖G1** | 不能把 Python claim 当安全沙箱；维持最终车端/Panda限幅、FCW/AEB、driver enable、core liveness及fault锁定。先写失败矩阵与E2E，再实现，不能只把新接口接通 |
| **G3 / P1 单源 actuation** | `CI.apply` 后只 extend，CAN claim 只裁决插件之间，SP 自有帧不参与；observe_can 还有立即发的第二条路径。S:`openpilot/selfdrive/car/card.py:175-176`、`openpilot/selfdrive/car/card.py:261-266`、`openpilot/tesla/plugins/__init__.py:161-184` | 窄 `role:lateral.actuation`：插件给角度/owner/抑制建议，宿主在原发送周期统一编码/最终限幅，并同步 carOutput；按 address/bus 为 SP、插件、OEM 建唯一来源。所有插件 CAN 出口含 observe_can 经同一裁决；纵向按 G8 单独加 | **y，路径2还需 G1 owner/FWD 原语；单纯 host 帧替换 n** | 双发、限幅历史已推进后抑制、旧 angle 恢复跳变；宿主须维护实际已选输出历史，原 port 仍是限幅/编码依据。故障时停止竞争轴并锁定，不能套普通 lat_controller 的“自动回 SP” |
| **G4 / P1 可并存的事件过滤：已完成 X-20261026-45（2026-10-26）** | CarState 保留真实 invalidLkasSetting；全局清除已移除 | 共享 `event.filter`：SP events→按 priority/id 的 filters→独占 fallback→floor；mixed 1.1.0 仅在自身开关开启时删 invalidLkasSetting，requires.hooks 门控旧宿主 | n | allowlist 唯一项 invalidLkasSetting；越权/垃圾/异常只停用该 filter、保留事件并显示故障，不锁定两轴；G3 路径2未实施 |
| **G8 / P1 动态 longitudinal owner** | long planner 只换计划；CP.long、CC.longActive、controller 25Hz/cancel 分支、Panda long mode 与 OEM forwarding 不随插件计划切换。S:`openpilot/tesla/plugins/__init__.py:488-519`、`opendbc_repo/opendbc/car/tesla/carcontroller.py:50-62`、`openpilot/selfdrive/pandad/panda_safety.cc:5-19` | 固定本行程支持的硬件能力，新增窄 long owner 建议/actuation hook；宿主确认 stock/SP/none、CC与PID reset、cancel/计数器/反馈、真实carOutput；Panda用预定义 owner 原语裁决0x2B9 TX/FWD。不是循环改 CP 或每次切 safety mode | **y** | 不允许借非long模式规则发主动加减速；mode TX、原车AEB优先、controls/driver intent和物理反馈仍强制。不能无缝交接就停在 none；不把 stock ACC 自动恢复当默认成功 |
| **G6 / P1 文本/配对设置** | Setting 只识别 range/choices，否则当 BOOL；STRING 存储已经有，缺交互。S:`openpilot/tesla/manifest.py:77-108`、`openpilot/tesla/plugin_params.py:11-31`、`openpilot/tesla/ui/pages/base.py:116-150`、`openpilot/tesla/ui/settings_page.py:43-65` | 设置元数据增加 text/secret、长度/字符校验；两套设置页共用既有键盘，secret 遮罩；G2 配对码显示/撤销作为宿主控件。插件通过 ParamsView 读写，不新增编译 key | n | 停车可改、输入边界校验、秘密不进日志；生成一次配对码不等于要求用户手输所有 token。无需为配对再造任意表单框架 |
| **G7b / P2 vehicle_params** | 类别只是分组；setup 太晚，CP_SP 还会被后续 set_car_specific_params 改写，paramsd 又有独立学习/缓存。S:`openpilot/selfdrive/car/card.py:114-160`、`openpilot/selfdrive/locationd/paramsd.py:202-257` | 最小为 CI构造/依赖计算前的 startup 参数patch，限定首批物理字段；按字段冲突裁决、范围验证、重算相关量，统一 CP/CP_SP/cache 发布。学习参数另有明确策略，不能把 startup 值当永远不变 | n | 质量/转向比等误值影响控制；禁止改 dashcamOnly/safetyModel/模式权限来解锁车型。固定 safety VM 及固件边界不随插件校正放宽；新 capnp 字段仍属 G10 |
| **G7a / P2 DM policy** | alert_source 不退出；safety.fallback 可加已有事件但不修改 DM 内部状态，额外监控插件争独占 fallback 也难组合。S:`openpilot/tesla/plugin.py:73-77`、`openpilot/selfdrive/monitoring/dmonitoringd.py:16-34` | 在默认 DM 结果后加共享 policy 建议，仅提高 alertLevel/lockout 或缩短允许时长；宿主聚合后唯一发布 driverMonitoringState，保持原模型运行。仅显示提醒时复用 alert，不加 hook | n | 不准清原DM事件/lockout或伪造摄像头健康；硬件是否有 cabin camera、DM enable 的每行程锁存留宿主。S:`openpilot/sunnypilot/hardware/driver_monitoring.py:11-31` |
| **G10 / P2 插件数据与编译 schema 的边界** | pluginMsg payload JSON 已容纳新 kind；但 publish_state 仅能写编译过的 CS_SP，SERVICES 也拒绝未知服务，任意核心类型/新字段不能运行时注册。S:`openpilot/tesla/plugin.py:50-51`、`openpilot/tesla/plugins/__init__.py:78-79`、`openpilot/cereal/custom.capnp:631-638`、`openpilot/cereal/custom.capnp:393-398`、`opendbc_repo/opendbc/car/structs.py:58-64` | 插件私有状态默认走 G5 的版本化 kind；只有 SP 主栈真要消费新语义，才加一次窄 adapter/hook；不用每插件扩 carStateSP。不做动态 cereal schema | n | JSON 只是传输，不会自动让 planner/controller理解内容；核心 schema/跨进程语义、日志可解析性留宿主。需要新核心字段时承认仍有系统构建 |
| **G11 / P2 native/包边界** | `.so` 可随包，platform 仅machine/Python；tar只普通文件、不支持链接，20MiB压缩上限但没有解压总量/文件数上限；发布器把文件都设0644、安装也不恢复执行位，直接 NativeProcess ELF 不会自然可执行。S:`openpilot/tesla/store.py:34-35`、`openpilot/tesla/store.py:87-124`；P:`tools/build_index.py:23-37` | 先补解压预算；已有 .so+PythonProcess 足够的不要改。首个直接 ELF 插件出现才增加包内明确 executable 清单及受限chmod；native ABI需求接 G9，优先发匹配目标的自包含包 | n | 解压耗尽磁盘、ABI装上但导入崩；.so 仍是受信本机代码，无沙箱。不为未出现的大模型包扩大限额，不加pip/apt在线安装器 |
| **G13 / P2 变道强 veto/abort** | lane_change_policy 仅覆盖 preLaneChange 的 auto 分支，扭矩仍可启动，Starting 分支不再问插件。S:`openpilot/selfdrive/controls/lib/desire_helper.py:62-95` | 若目标明确含“任何来源均不得启动/正在变道也要中止”，另加仅能阻止的 veto，并由宿主按变道阶段执行明确的渐退/复位；当前 navassist 的有限功能不因此强制扩接口 | n | 中途立即清 desire 可能造成路径跳变；灯取消不是控制abort证明。最低速度、原盲区/路沿和驾驶员取消保留宿主 |

### G1 的规则类型必须能表达真实现有规则

下表是建议的**固件类型组合**，不是已实现 DSL；未知类型/字段/过大参数一律拒绝，固定最大规则数、模板数和单周期计算量。不用一个“允许任意值的自定义表达式”代替规则设计。

| 探针 | 需要的固件原语 / 特别边界 |
|---|---|
| auto_speed | address/bus/len+Tesla/vehicle-bus；带 RX predicate 的模板缓存（mux1、tick=0）、bitmask相等、masked enum `{1,63}`；template TTL、最小间隔、idle-reset会话计数、controls gate。保持 S:`opendbc_repo/opendbc/safety/plugins/auto_speed.h:18-55` 的逐帧判定 |
| ambient_light | masked-template、常量/亮度范围、zone与side的**有限组合**、TTL、间隔、会话时长和计数；不能只有“字段各自合法”却接受组合非法。控制权限依当前代码，不按旧文档放宽。S:`opendbc_repo/opendbc/safety/plugins/ambient_light.h:29-59` |
| navassist | 只存checksum正确且idle的模板；预定义checksum算法、counter=(template+1) mod16、保护位、request/reason配对、active/cancel分支、固定窗口计数。active要controls，cancel只关灯的例外固化于类型边界。F:`opendbc_repo/opendbc/safety/plugins/navassist.h:18-91` |
| mixed / hybrid | 上述位规则**不足够**，还需固件自带的OEM输入验证、owner状态机及窄FWD路由原语。当前四回调没有fwd；mode本身会挡0x488/0x2B9，规则做 AND 不会把 blocked 变成 allow。S:`opendbc_repo/opendbc/safety/plugins/plugin.h:6-12`、`opendbc_repo/opendbc/safety/safety.h:262-282`、`opendbc_repo/opendbc/safety/modes/tesla.h:335-357` |

**交接边界**：不能承诺“装任意一份runtime rules就能原车AP共用”。一次固件扩展必须把支持的 owner/转发语义写成固定能力，且独立验证OEM帧，不信Python owner。若在保留原mode TX/FWD约束下某场景仍被拒，就不支持该场景/等待OEM idle；不能让数据规则替换mode checks来实现D。未来新车辆协议或新的交接语义仍会需要固件迭代。`safety_plugins_rx` 收到valid不等于未列入RX表的帧已做checksum/counter检验（S:`opendbc_repo/opendbc/safety/safety.h:184-195`）。

加载链路至少覆盖：停车安装/启用→验证manifest/规则/能力→冻结active winners→各Panda初始化并读回摘要→允许本次控制；模式reset、拔插/重启、卸载/禁用或关键故障使规则失效/停止竞争TX，重新验证后才重载。未知引擎、部分上传、旧hash、掉电必须拒用新CAN能力。现有pandad只在onroad首次配置mode，USB请求只有mode/param与alternative/SP位；新增loader不能靠Python setup置bit代替（S:`openpilot/selfdrive/pandad/panda_safety.cc:5-19`、`openpilot/selfdrive/pandad/panda_safety.cc:69-78`、`openpilot/selfdrive/pandad/panda.cc:50-55`）。

## 不构成缺口的能力、生命周期与 YAGNI

| 项目 | 判定 / 暂不做的原因 |
|---|---|
| 商店参数 → params_keys.h | **草稿应纠正：普通插件key无需编译**。ParamsView支持BOOL/INT/FLOAT/STRING/JSON，存 `/data/xpilot_plugins/.params`；核心SP/C++若直接Params读取新的key才需编译/adapter。插件不能复用已注册host key作为自己的声明；flags目前被忽略，不提供自动CLEAR/DONT_LOG语义。S:`openpilot/tesla/plugin_params.py:1-11`、`openpilot/tesla/plugin_params.py:42-98`、`openpilot/tesla/store.py:119-124`、`openpilot/common/params.py:131-134` |
| process lifecycle | 新插件的processes本来可随包声明；但PLUGINS只在import发现、managed_processes启动时建一次，所以安装/升级需停车后重启。已注册进程有运行条件轮询，**不是每新增进程就要改系统**。不做行驶热加载；仅需G9快照/重启一致性。S:`openpilot/tesla/plugins/__init__.py:90-95`、`openpilot/tesla/plugins/__init__.py:200-241`、`openpilot/system/manager/process_config.py:164-165`、`openpilot/system/manager/manager.py:157-166` |
| 只有内置插件能替换核心？ | **已修好**。perception.model、engagement.state、safety.fallback均按role选owner，默认插件也走floor；外部可自带进程或选择宿主原进程对象。别新增按插件id分支。S:`openpilot/tesla/plugins/__init__.py:200-223`、`openpilot/tesla/core_host.py:229-253`；S提交`45f25c75a`、`938340192` |
| 普通UI / 自定义停车页 | Tile、HudOverlay、ScreenOverlay与store ui_module已通；大屏Tile.PAGE可挂详情页，TileSpec.services能订阅已知服务。无需再加“通用插件UI引擎”。overlay额外消息可自己用PluginMsgs，ui_state.sm不会自动加插件SERVICES；小屏没有等价Tile.PAGE入口。S:`openpilot/tesla/plugins/__init__.py:98-101`、`openpilot/tesla/ui/plugin_ui.py:20-69`、`openpilot/tesla/ui/screen.py:102-103`、`openpilot/tesla/ui/screen.py:187-207` |
| G7c object_fusion | 类别只有分组；已有radar_interface换原始tracks，缺radard之后的融合hook。当前所有探针不需要任意替换融合；有首个明确目标再加有边界的result hook，不为分类齐全而开发。S:`openpilot/tesla/manifest.py:33-39`、`openpilot/tesla/plugins/__init__.py:522-546`、`openpilot/selfdrive/controls/radard.py:234-265` |
| G7d lateral_planning | **不是完全无hook**：lat_correction总改动≤0.5m/s²，随后clip_curvature；lat_controller可换控制器。路径1优先用现有接口；完整路径/轨迹planner暂不加，也不为迁旧算法扩大修正上限。S:`openpilot/tesla/plugins/__init__.py:395-414`、`openpilot/selfdrive/controls/controlsd.py:119-137` |
| 内置相机/HUD外观替换 | overlay可加内容，不能让底层相机shader或任意内置HUD消失；55a1的相机保留彩色仍是系统特例。若下个外观插件确需这些，再加枚举/声明式显示策略，而不是替换整个cameraview。现探针无需，暂不做。S:`openpilot/selfdrive/ui/mici/onroad/cameraview.py:323-326`、`openpilot/selfdrive/ui/sunnypilot/mici/onroad/hud_renderer.py:22-35` |
| 系统updater / 插件updater | 插件在系统树外，正常系统overlay reset不删除它；store升级是停车、hash验证、rename替换、有恢复路径，成功版本不保留长期rollback包。没有自动跨系统/插件/固件事务，也不自动重建native；补G9预检，先不做行驶热更新/自动依赖求解器。S:`openpilot/tesla/manifest.py:28-29`、`openpilot/tesla/store.py:127-164`、`openpilot/tesla/ui/offroad.py:198-210`、`openpilot/system/updated/updated.py:359-386` |
| 超出插件边界 | 新cereal核心字段、新Panda规则类型、新安全模式/协议、新驱动/相机/AGNOS/硬件标定、修改宿主floor，仍需系统维护。拒绝动态cereal、任意代码规则、通用FWD解禁、pip/apt插件依赖安装、无需求扩大包限额。参数或JSON不使消费者/硬件凭空支持新语义 |

## SP 调用点与历史改动清单

完整检索命令（在S根目录；没有设备路径）：

```sh
rg -n 'plugins\.' openpilot/selfdrive openpilot/sunnypilot openpilot/system opendbc_repo/opendbc/sunnypilot
git log --oneline --name-only 65bb09e..55a1f37736 -- openpilot/selfdrive openpilot/sunnypilot openpilot/system opendbc_repo/opendbc/sunnypilot opendbc_repo/opendbc/safety
git log --oneline --name-only 8b8bc63f4..65bb09e -- openpilot/selfdrive openpilot/sunnypilot openpilot/system opendbc_repo/opendbc/sunnypilot
git diff 922513f 8b8bc63f4 -- openpilot/selfdrive openpilot/sunnypilot openpilot/system
```

opendbc在导入树中是vendored源码，上游922513f中是gitlink；整目录显示A不等于这些SP文件全是xpilot新改。`8b8bc63f4`是当前xpilot历史的无父提交导入快照，不能从其提交标题推断早期钩子的逐个作者/提交。早于它的xpilot细粒度历史不可由这条分支恢复；以保存的SP基线`922513f`做树比较，区分保留的扩展和Tesla精简删除。以下列出仍相关的功能点，不把删除其他车型/云服务/UI翻译等当作待增加的插件hook。

| SP调用/改动位置（S） | 内容与处理 |
|---|---|
| `openpilot/selfdrive/car/card.py:117-128`、`openpilot/selfdrive/car/card.py:175-181`、`openpilot/selfdrive/car/card.py:261-291` | radar_interface；setup/observe/publish/control_sends/core_alive/service_params。既有通用入口保留；G1/G3/G7b在正确阶段扩边界，不将功能逻辑塞port |
| `openpilot/selfdrive/controls/plannerd.py:27-45` | services、plan_filters、long_targets、long_planners、plan_sink；已有入口保留，G12收紧结果验证 |
| `openpilot/selfdrive/controls/lib/longitudinal_planner.py:122`、`openpilot/sunnypilot/selfdrive/controls/lib/longitudinal_planner.py:30-33`、`openpilot/sunnypilot/selfdrive/controls/lib/longitudinal_planner.py:65` | long plan替换及目标合并；不是动态long mode切换接口 |
| `openpilot/selfdrive/controls/controlsd.py:46-63`、`openpilot/selfdrive/controls/controlsd.py:125-126` | 插件额外服务、lateral_controller、lat_correction；Tesla-only angle控制器精简保留，不为新控制算法再改这里 |
| `openpilot/selfdrive/controls/lib/desire_helper.py:33`、`openpilot/selfdrive/controls/lib/desire_helper.py:74-75` | lane policy；只替换免推判断。G13确需abort时才加调用 |
| `openpilot/selfdrive/selfdrived/selfdrived.py:102-118`、`openpilot/selfdrive/selfdrived/selfdrived.py:182-189`、`openpilot/selfdrive/selfdrived/selfdrived.py:282` | services、alert_source/update_alerts、install_core；DM/CAN/存活检测继续原栈；G4/G7a在宿主窄口处理 |
| `openpilot/sunnypilot/system/fork_processes.py:22-35`、`openpilot/system/manager/process_config.py:164-165` | 地图source让位、模型role进程、插件process注册；同一通路供内置/商店使用。37fcd2bb3/45f25c75a是指定区间仅有的fork_processes改动 |
| `openpilot/sunnypilot/selfdrive/selfdrived/events.py:270-271`、`openpilot/tesla/alerts.py:1-41` | 通用插件报警映射；新文字不需新增cereal事件。新**控制语义**事件仍需宿主合同，不能把alert_source当退出接口 |
| `openpilot/selfdrive/ui/ui.py:11-20`、`openpilot/system/ui/lib/application.py:684-697`、`openpilot/selfdrive/ui/sunnypilot/mici/onroad/hud_renderer.py:18-35` | 大屏宿主、viewport/触摸和overlay边界；46603250e/3986876e9/dabf4316c等早期改动归宿主；55a1f3773把HUD布置改走render_hud。现有插件图块/overlay无需再改SP |
| `openpilot/selfdrive/ui/mici/onroad/cameraview.py:323-326` | 55a1f3773直接固定camera色彩；仅可能迁到未来显示策略，当前YAGNI。Outline主题及HUD布局文件本来属于tesla宿主，不是SP控制逻辑 |
| `opendbc_repo/opendbc/sunnypilot/car/tesla/carstate_ext.py:25-27` | a497ef963全局清invalidLkasSetting；应随G4回迁为mixed插件可撤销的策略，原车信号保持真实；不是runtime规则已解决 |
| `opendbc_repo/opendbc/safety/safety.h:332-340` | 同一a497ef963修正ignore_frequency_check；属于核心RX校验bug修复，保留系统，不开放插件改时效/checksum/counter |
| `openpilot/sunnypilot/selfdrive/car/interfaces.py:31-39`、`openpilot/sunnypilot/selfdrive/car/car_specific.py:15-21` | 早期移除车型专用/旧Tesla功能参数接线，现仅原生TeslaCoopSteering/Mads选项；功能参数归商店ParamsView。不要恢复按feature名硬编码的分支 |
| `openpilot/sunnypilot/models/manager.py:140-158`、`openpilot/sunnypilot/models/manager.py:180-236`、`openpilot/sunnypilot/models/manager.py:245-280`、`openpilot/sunnypilot/mapd/live_map_data/base_map_data.py:61-72` | 早期保留了模型分块下载/重试/进度/hash检查、地图source与targetVelocities发布；新模型栈已可通过perception.model自带进程替换，地图数据通过source:map，不必逐个改SP。既有下载器修复属于默认sp_models维护 |
| `openpilot/sunnypilot/system/fork_processes.py:24-33`、`openpilot/system/hardware/hardwared.py:380-382`、`openpilot/sunnypilot/hardware/agnos.py:1-112` | C3XL DM/音频/告警硬件与onroad门控、65bb09e AGNOS发布策略属于平台层。新硬件支持仍改系统，不为这些造任意插件hook |

上述`plugins\.`检索在`openpilot/system`和`opendbc_repo/opendbc/sunnypilot`没有直接调用；前者经manager/application/UI等入口，后者存在的是carstate_ext政策特例。不能因直接grep为空便断言无系统耦合。

指定`65bb09e..55a1f37736`共17个提交：落在上述SP/port路径的仅`37fcd2bb3`、`45f25c75a`、`a497ef963`、`55a1f3773`；其余为store/manifest/core_host/检查/文档/UI宿主改动。更早的保留扩展包括`14b9e86c3`雷达/地图/provider、`7095968d8`模型默认role、`e60f99c0d`engagement/fallback、`ea6530f1a`分仓；这些是一次宿主基础，不应在每插件重复。

## 实施顺序、范围与验证门槛

1. **先P0**：G14位域编码 + G1基础规则/loader + G9能力预检；G5本地broker + G2统一网络入口复用一条投递链；G12在对应边界实守floor。先以auto_speed/ambient_light逐帧等价及navassist新增0x3E9验收。G6随配对界面补齐。
2. **路径1先发插件**：先做实际100/50Hz控制回放，满足时序/限幅即可，不等全部框架。路径2再做G3和固件固定owner/FWD能力，G4仅有多策略共存需求才共享化；单插件可先复用现有fallback。
3. **纵向另成完整切片**：G8复用单源执行和固件原语，必须同时验证CP/CC/PID/feedback/Panda TX+FWD。G7a/b按首个具体DM/车辆参数插件字段落地。G7c/d与G13、相机/HUD替换、直接ELF支持暂缓。

未来改动触及：系统manifest/Plugin/CarPlugins/core_host/消息服务、card/controlsd/DM初始化处的薄调用、pandad传输与Panda dispatch/loader、两套设置UI/store/updater预检、现有检查与fixture；插件仓库负责规则数据、算法/协议/UI、tests、dist/index。系统和插件分提交/PR，系统先合；**本次不修改这些实现、配置、导出或fixture**。

先固定失败矩阵：未知类型/旧FW/规则截断与超量、伪造来源/seq回绕/重放/TTL/洪泛、两插件发布及丢broker、双CAN来源/非发送周期/异常恢复跳角、AP/ACC在先与SP在先、AEB/Autopark/relay/刹车取消/DM锁定、mode reset与Panda重连、安装中断/磁盘满/ABI变化/参数缓存漂移。不能在代码之后补单元测试。

| 未来E2E | 可重复通过条件与产物（不是本次已执行结果） |
|---|---|
| store→host→各插件→消息/真实控制链 | 从同一索引安装、重启发现/claims/参数、并行navassist+另一producer；注入认证失败/旧包/坏消息/进程故障，核对实际状态与发帧。产出固定fixture、系统/插件SHA、包/规则hash、参数快照、消息/owner/carOutput/CAN时间线、result.json及SHA256SUMS |
| host→编译libsafety RX/TX/FWD | 同一CAN输入逐帧对比旧header与runtime规则；自有mode地址始终满足base TX，验证无state副作用提前提交，owner切换不双发、原车AEB优先、reset/reload/超时拒用。完整命令/退出码/构建与输入hash/MISRA和固件结果单列 |
| 实物 | 另行授权后才做停车安装/能力读回/总线和闭场交接；主机replay、编译成功、规则已上传与设备实际激活是不同证据。本次设备active commit未知，未改变 |

本次验证：固定版本引用/路径/行号、SP调用点与历史范围、单文件diff、父提交与远端推送一致；另用真实converter复现G14。检查文件和JSON在`/private/tmp/X-20261025-21-audit/`，不进仓库。重复命令：

```sh
python3 /private/tmp/X-20261025-21-audit/verify.py
PYTHONDONTWRITEBYTECODE=1 /Users/mile/Desktop/mo-op/.venv/bin/python /private/tmp/X-20261025-21-audit/safety_param_check.py
```

`result.json`记录文档hash、各固定来源SHA与引用文件/片段hash；`safety-param-serialization.json`记录6个输入、真实往返值或异常，`SHA256SUMS`覆盖产物。主机转换检查只确认现有缺陷，不将源码推断或该检查写成控制/网络/车辆E2E通过。
