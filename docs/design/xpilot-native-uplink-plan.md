# 手机 NPU 链路：原生 bigmodeld 进插件（方案 A）

日期 2026-11-17。接替 `xpilot-phone-link-plan.md` 的 P3（逐段追平原生），P3 停止。

## 1. 已定的事（车主 2026-11-17）

| 项 | 决定 |
|---|---|
| 路面流分辨率 | 不恢复 1344x760，保持官方原生分辨率（`b07641bf5`） |
| 链路实现 | 方案 A：dev-sp-npu 的 `openpilot/selfdrive/bigmodeld/` 原样作为 lean_mici 的上行进程，不再维护 Python 上行 |
| AGNOS | 跟进 20.1，已提交 `4a8d9bb88`（只改版本号，清单仍是 19.9 镜像；仍在 19.9 的设备会反复刷写重启，见 XPILOT.md） |

## 2. 为什么是方案 A

- 速度：lean_mici 0.8.14 车端 `eof_to_send` p50 7.5–8.8 ms，原生 5.31 ms。剩下的差距在 `recv`、`encode`、`wake`，继续逐段移植就是把 bigmodeld 再写一遍。
- 同步成本：dev-sp-npu 一周内改了 6 次 bigmodeld（版本检查、deviceMotion、导航输入、发布时间判迟到）。两份实现意味着每次都要在 `wire.py`/`worker.py` 重做。方案 A 下同步 = 拷目录 + 设备上重编。
- 插件原则不变：宿主只认 `ModelSource` 邮箱合同（XPILOT.md 4.3a），不认识 bigmodeld。

## 3. 目标结构

```
lean_modeld（runner.py，不变）
  ├─ modeld 每帧跑本机模型 → ModelSource.send_frame(META) / pick(回复)
  └─ supervise()（不变：心跳 5 s、退避 1–30 s、PDEATHSIG）
        └─ exec bigmodeld --mailbox <邮箱文件> --host <pin/auto> --bitrate <N>   ← 原来是 fork Python worker
              取帧/warp/配对/V4L 双路硬编/TCP/REPLY：全部是 dev-sp-npu 原代码
```

### 3.1 bigmodeld 的 mailbox 构建（改在 dev-sp-npu，单一源码）

编译宏 `BIGMODELD_MAILBOX` 打开，运行时必须带 `--mailbox`。不定义宏时行为不变；新代码在 `mailbox_io.{h,cpp}`，`main.cc` 只在 I/O 边界分支（xpilot 的 cereal 没有 `bigModelReply`/`navStateSP`，所以必须是编译期分支）。

| 边界 | 默认模式（dev-sp-npu） | `--mailbox` 模式（xpilot） |
|---|---|---|
| desire / action_t | `modelDataV2SP.desireClass/bigActionT` | 邮箱 META（ABI 2，desire 电平；`frame_meta` 的边沿锁存不变） |
| session | 无 | 取 META 的 session，原样写回信封 |
| 回复 | `bigModelReply` | 信封 `<QQQII>`（session、epoch 见下、arrival=`reply_ns`、paired=3（发送侧序列头门只放双 IDR 开头的序列）、length=8320）+ BGM1 v2 REPLY（`frame`=road VisionIPC frame_id，`eof`=tEof，内容同 `wire.to_mailbox`）；先清长度、写正文、最后写长度，`fcntl` 阻塞锁 `[256,16384)`（宿主只在锁内 memcpy，等待不丢回复，同 lean_mici `64bf25c`）；宿主会锁死的回复（找不到帧、全零、非有限）直接丢弃 |
| 导航 NavExt | `navStateSP` + mem Params `NavMapTargetVelocities` | ≤2 Hz 写 `<邮箱目录>/nav.bin`（原始 8480 B REPLY，tmp + rename）；runner 用现有 `wire.nav_ext` 解码后发 `pluginMsg lean_mici.navigation`，`CurveTarget` 不变 |
| 心跳 | 无 | META 偏移 160 写 u64 BOOTTIME = 两路取帧循环较旧的一次（只有它们会永久卡住：Venus 不还编码缓冲）；writer/reply 最多被发现 + 建连阻塞约 4 s，不计入，免得 5 s 监督误杀重连 |
| epoch | 无 | 跟随手机：回复带 SEQ_RESET 或换连接就 +1；起点是进程启动的 BOOTTIME ns，进程重启后仍高于宿主已见过的 epoch（宿主拒收更低的 epoch，lean_mici 0.8.x worker 重启从 0 计有这个隐患） |
| Params | `BigmodelServerHost/EncoderBitrate/LinkState` | 一律不读写（xpilot 没有这些 key）；码率只认 `--bitrate`，改码率下次启动生效；链路状态由 runner 从邮箱推出 |
| 手机固定 | 无 | `--pin NAME` 只连该 mDNS 实例；HELLO 成功后写 `<邮箱目录>/peer`，runner 首次学到（不是 `gateway`）时存进 `ExtLeanMiciPeer` |
| 分段计时 | `modelDataV2SP.bigStages` | 每秒一行 `bigmodeld.stages`（各段 p50/p95） |
| 编码器 | — | 构建带上 dev-sp-npu 的 `system/loggerd/encoder/{encoder,v4l_encoder}.{h,cc}`（`output_callback` 接缝 xpilot 没有） |
| 相机 | 已支持 OS04C10 1344x760、AR0231/OX03C10 1928x1208 | 同左，原生分辨率不用改 |

### 3.2 失败模式（先写进检查，再实现）

1. 默认模式输出变了（`bigModelReply`、FRAME 字节、`navStateSP`）。
2. 信封 frame/eof 和 modeld 当前帧对不上（host 判 `frame_eof` 锁定）。
3. 写到一半进程被杀，host 读到非零长度的半条回复。
4. 宿主占着回复槽时丢了回复（改为等锁；宿主锁内只有 memcpy）。
5. 手机标 SEQ_RESET 时 epoch 没递增（host 判 `reset_without_epoch` 锁死），或进程重启后 epoch 低于宿主（`old_epoch`）。
6. META ABI 不是 2 或 session 变了仍按旧 session 写。
7. 取帧/编码卡死时仍有心跳，或重连阻塞被当成卡死（只取两路取帧循环）。
8. desire 电平在跳帧/双 IDR 等待时丢脉冲（`frame_meta` 已有用例，补邮箱输入路径）。
9. 没读到 META session 就写回复（宿主判 session 锁死）。
10. `nav.bin` 写一半被读到（必须写临时文件再 `rename`）。

## 4. 任务包

| 包 | 仓库 | 内容 | 需要设备 |
|---|---|---|---|
| N1 | dev-sp-npu | mailbox 构建 + `test_mailbox.cc`；默认构建回归。**已完成**：dev-sp-npu `76848305f`。`test_mailbox` 59/59、`test_bigmodeld` 5277/5277；C++ 正文与 `wire.to_mailbox` 逐字节相同，xpilot `ModelSource` 接受到解码之前的所有检查；`main.cc` 两种构建 `g++ -fsyntax-only -Wall` 0 错误 0 警告（mailbox 构建用 capnpc 生成的 xpilot cereal，`msm_media_info.h` 为桩）；尚未链接、未上设备，留给 N2 | 否 |
| N2 | xpilot-plugins | lean_mici 0.9.0：`native/bigmodeld/` 收录 N1 源码和 4 个编码器文件（记源提交和 sha256），`-DBIGMODELD_MAILBOX` 编译；`subprocess` 启动时 `preexec_fn` 设 `PR_SET_PDEATHSIG`；`build_native.py` 在设备上编出 `bigmodeld`；`runner.supervise` 改为 exec；删除 `worker.py`、`camera.cc/.py`、上行部分 `wire.py`、`network.py`；`status` 读 `nav.bin`/`link.json` | 编译要 |
| N3 | xpilot | `sunnypilot/modeld_v2/modeld.py` 加 `run_with_model_source`（一行钩子，同 stock modeld），runner 按当前选中的模型类型选入口，保留 SP 选模型和 chestnut 行为 | 否 |
| N4 | 设备 | 同一台 C3、同一手机 APK、`/data/bench-PL/fast.sh` 方式：0.8.14 与 0.9.0 A/B/A/B 各 120 s；同机再跑一轮 dev-sp-npu 原生 bigmodeld 做参照 | 是 |
| N5 | 流程 | 写进 lean_mici README：dev-sp-npu 每次改 bigmodeld → 拷目录、更新源提交、设备重编、跑 N1 宿主测试、发版 | 否 |

### 验收（N4）

- 0.9.0 各段（`captureMs/warpMs/pairWaitMs/encodeMs/sendMs`）与同机 dev-sp-npu bigmodeld 差 ≤0.3 ms（同代码，只剩环境差）。
- `eof_to_send` p50 ≤5.8 ms；如果原生分辨率让 `recv`/`warp` 超出，按段列出，并注明来自分辨率（车主已接受原生分辨率）。
- 采纳率、`rtt` 相对 0.8.14 不降；`kill -9 bigmodeld` 10 s 内恢复采纳；断 Wi-Fi 5 s 后 ≤1.5 s 恢复（沿用 P4 标准）。
- 本地提交和设备生效提交分开报告。

## 5. 其他同步项（不阻塞方案 A）

- 宿主挑修复：sunnypilot `#2100`、`f0e240ab2`、`0ba289b6c`、`ec35bfb68`；`48ff07aac` 先确认 xpilot modeld 是否同样 `freq_ok` 失败。
- 新通用钩子：变道否决（只能收紧），用于 CH 报障碍物时拦住推方向盘发起的变道。
- 插件：`tesla_ble_ch`（`process:ble_turn_intentd`，`msg:ch.intent`、`msg:ch.lane_block`）；`nav_lane_change` 回补 `2e1d27cac`、`43cac6f45`、`f25b60516` 的增量；lean_mici `CurveTarget` 回补 `3aa69910f`。
- 不跟：sunnylink、Toyota、上游 navd（依赖 comma API）。
