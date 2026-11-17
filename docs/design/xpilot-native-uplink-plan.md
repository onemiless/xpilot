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

### 3.1 bigmodeld 增加 `--mailbox` 模式（改在 dev-sp-npu，单一源码）

不带 `--mailbox` 时行为逐字节不变；新代码放 `mailbox_io.{h,cpp}`，`main.cc` 只在 I/O 边界分支。

| 边界 | 默认模式（dev-sp-npu） | `--mailbox` 模式（xpilot） |
|---|---|---|
| desire / action_t | `modelDataV2SP.desireClass/bigActionT` | 邮箱 META（ABI 2，desire 电平；`frame_meta` 的边沿锁存不变） |
| session | 无 | 取 META 的 session，原样写回信封 |
| 回复 | `bigModelReply` | 信封 `<QQQII>`（session、epoch=`conn_epoch`、arrival=`reply_ns`、paired=该帧 FRAME 头 IDR 位、length=8320）+ BGM1 v2 REPLY（`frame`=road VisionIPC frame_id，`eof`=tEof，内容同 `wire.to_mailbox`）；先清长度、写正文、最后写长度，`fcntl` 非阻塞锁 `[256,16384)` |
| 导航 NavExt | `navStateSP` + mem Params `NavMapTargetVelocities` | 2 Hz 原子替换写 `<邮箱目录>/nav.bin`（原始 160 B NavExt + tEof）；runner 用现有 `wire.nav_ext` 解码后发 `pluginMsg lean_mici.navigation`，`CurveTarget` 不变 |
| 心跳 | 无 | writer 线程每圈写 u64 BOOTTIME 到 META 偏移 160 |
| Params | `BigmodelServerHost/EncoderBitrate/LinkState` | 只用 CLI；链路状态写 `<邮箱目录>/link.json`。xpilot 没有这些 key，C++ `Params` 读未知 key 会出错，`--mailbox` 模式一律不读写 |
| 相机 | 已支持 OS04C10 1344x760、AR0231/OX03C10 1928x1208 | 同左，原生分辨率不用改 |

### 3.2 失败模式（先写进检查，再实现）

1. 默认模式输出变了（`bigModelReply`、FRAME 字节、`navStateSP`）。
2. 信封 frame/eof 和 modeld 当前帧对不上（host 判 `frame_eof` 锁定）。
3. 写到一半进程被杀，host 读到非零长度的半条回复。
4. 锁被 host 占着时阻塞了 reply 线程（必须非阻塞，忙就丢这一条）。
5. 重连后 epoch 没递增，或新连接首帧不是双 IDR（host 判 `need_dual_idr`）。
6. META ABI 不是 2 或 session 变了仍按旧 session 写。
7. 心跳只在 writer 线程写，取帧线程卡死时仍有心跳（需要取帧线程也报活，取最旧）。
8. desire 电平在跳帧/双 IDR 等待时丢脉冲（`frame_meta` 已有用例，补邮箱输入路径）。
9. META 缺失（modeld 未起）时发帧。
10. `nav.bin` 写一半被读到（必须写临时文件再 `rename`）。

## 4. 任务包

| 包 | 仓库 | 内容 | 需要设备 |
|---|---|---|---|
| N1 | dev-sp-npu | `--mailbox` 模式 + `test_bigmodeld.cc` 用例覆盖 3.2 的 1–10；默认模式回归 | 否 |
| N2 | xpilot-plugins | lean_mici 0.9.0：`native/bigmodeld/` 收录 N1 源码（记源提交和 sha256）；`build_native.py` 在设备上编出 `bigmodeld`；`runner.supervise` 改为 exec；删除 `worker.py`、`camera.cc/.py`、上行部分 `wire.py`、`network.py`；`status` 读 `nav.bin`/`link.json` | 编译要 |
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
