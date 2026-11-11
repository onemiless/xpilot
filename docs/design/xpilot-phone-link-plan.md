# 手机 NPU 推理链路：优化与兜底执行方案

日期 2026-11-11。给执行者（worker）用。目标三条：**快**（车端发出时间追平原生 bigmodeld）、**准**、**有兜底**（手机链路掉了能自己回来）。

先读 `AGENTS.md` 和 `XPILOT.md` 第 2 节、4.3a 节。每个任务包是一个独立任务，按第 3 节的顺序做。

## 1. 基线

| 项 | 值 |
|---|---|
| 系统仓库 | `onemiless/xpilot`，分支 `xpilot`，本地 `97295d9`（`origin/xpilot` 是 `e3eea72`，本地多一个未推送的 capnp 泄露修复） |
| 插件仓库 | `onemiless/xpilot-plugins`，`origin/main` = `6d1451d`，`lean_mici` 0.8.12 |
| 原生参考 | `onemiless/dev-sp-npu`，分支 `dev-sp-npu`，`openpilot/selfdrive/bigmodeld/`（提交 `13509a03a`） |
| 手机 App | `onemiless/xlink`（监听 TCP 7070，BGM1 v4） |

本机的插件检出 `/home/martin/work/wt/xp-V07` 停在旧分支，以 `origin/main` 为准。

推送规则（`AGENTS.md`）：插件改动只进 `xpilot-plugins`，系统改动只进 `xpilot`；两边都要改的功能先合系统。插件每次发版要 `python3 tools/build_index.py` 并提交 `dist/` 和 `index.json`。

**native 构建限制**：`plugins/lean_mici/libcamera.so` 是 aarch64 产物，只能在设备上、对着匹配的已构建 xpilot 检出跑 `python3 <plugin>/build_native.py`。改 `camera.cc` 的任务包（P2、P3）必须有设备。

## 2. 现状和依据

### 2.1 链路

```
camerad ──VisionIPC──▶ worker 取帧线程 ×2（warp 成 512×256 NV12，按 SOF 配对）
                          └▶ lean_capture：两路 Venus HEVC 硬编 ──▶ Python 组 FRAME ──TCP──▶ 手机
modeld（lean_modeld 进程）每帧跑本机模型 ──▶ ModelSource.pick 等回复到 EOF+60 ms ──▶ 发布
手机 REPLY ──TCP──▶ worker 回复线程 ──▶ /dev/shm 邮箱 ──▶ ModelSource
```

- 宿主：`openpilot/tesla/model_source.py`，`openpilot/selfdrive/modeld/modeld.py`。
- 插件：`runner.py`（lean_modeld 进程，起一个 worker 子进程）、`worker.py`（主循环 + 回复线程）、`camera.cc` / `camera.py`（取帧、warp、编码）、`wire.py`（BGM1 编解码）、`network.py`（发现）。

### 2.2 耗时（都是 p50，ms）

| 路径 | 记录 | EOF→可发送 | 往返 | EOF→回复 |
|---|---|---|---|---|
| 原生 bigmodeld | Mac `/Users/mile/work/artifacts/V-20261107-03/analysis.md` | 5.31（capture 1.43 + warp 1.46 + pairWait 0.15 + encode 2.27），再加 send 0.53 | replyWait 22.07（手机内 19.08） | 29.18（p95 31.78） |
| lean_mici 新版 | Mac `/Users/mile/work/artifacts/T-20261110-14/round1/analysis.txt` | 8.3（`eof_to_send`，计到 `sendall` 之前） | 23.6 | 32.2 |
| lean_mici 0.8.8 | `/home/martin/work/claude-codex-workspace/reports/T-20261110-01/final-acceptance.txt` | 12.1 | 24.2 | 36.7 |

- **要追的差距**：EOF→可发送 8.3 对 5.31，约 3 ms。往返那 1.5 ms 的差里手机 APK 不同（T-14 用的是 0.4.36 导航预览版），不可直接比。
- **手机是大头**：纯手机 USB 实测 NPU 14.3 ms，解码 + 预处理 + 打包约 4 ms（相减得出）；Wi-Fi 传输加车端收包约 3 ms。
- **超时来自 Wi-Fi 重传突发**：V-20261107-03 里 8 次超时全落在 3 个相邻的 socket 重传秒内，手机耗时正常。
- **差距落在哪一段没有记录**：插件只记 `capture`（0.8.9 起包含等下一帧，T-14 里是 48.6，没用）、`eof_to_send`、`rtt`、`eof_to_reply`，没有分段。所以 P2 先加分段计时。

### 2.3 读代码找到的可疑开销（P2 量完再定，不要先改）

`camera.cc` 和 `camera.py`、`worker.py`（0.8.12）：

1. 取帧线程把帧 warp 进 `staged[]`，配对后 `lean_capture` 再 `memcpy` 到 `input`（每路一次）。原生直接 warp 进编码缓冲。
2. `Stream::collect` 等的是 `released && !packet.empty()`：除了出包，还要等 Venus 归还输入缓冲。只有一个 `input` 缓冲，所以必须等。
3. 配对在取帧线程里发现，编码却要唤醒 Python 主线程（阻塞在 `lean_capture` 的条件变量上）去提交。
4. 编码完成后的 Python 路径：
   - `camera.py:51` `b.raw[:n]` 每路先拷整个 1 MiB 缓冲再切片（x86 实测 33 µs 对 `ctypes.string_at(b, n)` 的 0.5 µs，C3 更慢）。
   - `idr()` 在 Python 里扫 Annex-B 找关键帧，而 C++ 回调里已经有 `key` 标志。
   - `sm.update(0)`、`vehicle()`（numpy 3×3 矩阵乘）、`frame()`（两次 bytes 拼接）都排在编码之后、发送之前。
   - `sendall` 两次。
5. Venus 争用：xpilot-plugins PR #46 记录过 encoderd 全分辨率编码占着 Venus 时，road 出包 9.3 ms，停掉 encoderd 降到 1.95 ms。之后加了 `XP_ENCODERD_DEFER_MS`（默认 10），但这个延后没有设备验证记录。

### 2.4 兜底和准确性的缺口（已对最新代码核对）

- **worker 只启动一次**（`runner.py:56`）。崩溃（`V4LEncoder` 里是 assert）、卡死、`RLIMIT_AS` 超限之后，本趟手机链路不再回来。0.8.10（`5ff515d`）和 0.8.11（`25ec4f0`）两次事故都是 worker 卡在 `V4LEncoder::encoder_close` 后不再重连；`encoder_close`（`openpilot/system/loggerd/encoder/v4l_encoder.cc:332`）在 Venus 不回 EOS 或不归还输入缓冲时仍会永久阻塞。原生 bigmodeld 也没有这层保护。
- **每次重连必等 2 s**：`network.py` 的 `discover()` 用 `avahi-browse -rp` 不带 `-t`，一定跑满超时；没有地址缓存。原生是缓存地址优先，连败 3 次才重新发现（`server_locator.cpp:69-88`）。
- **网络断开也重建编码器**：`worker.py` 每次断链都 `camera.close()` 再新建。
- **socket 超时 0.2 s**：超过 200 ms 的 Wi-Fi 卡顿就断链，之后是发现 + 新编码器 + 双 IDR + 5 帧暖机 + 10 帧淡入。手机侧的读超时已经在 `eb1eb1c` 改成 1 s。
- **desire 脉冲可能丢**：宿主在 `model_source.py:103` 做上升沿检测，脉冲只出现在一帧的 META 里；META 是单槽覆盖，worker 在 `worker.py:215` 读到后先记 `used`，随后取帧失败、等双 IDR 或该帧被跳过，脉冲就没了。原生是在上行进程里对 desire **电平**做边沿检测，新序列时重置锁存（`frame_meta.cpp:173-200`），desire 还在就会补发。
- **已修、不用再做**：回复超前宿主导致整趟锁死，`b6ca4bf` 已加 `ahead` 判定。

### 2.5 内存

- **已确认的泄露**：pycapnp 2.2.0 / 2.2.1 用 kwargs 构造 struct 会留循环引用；实时进程 `gc.disable()`，永不回收。`gc.disable()` 下实测每条 `Event.new_message` 156 个对象（约 30 KB），`ModelDataV2.Action` 5 个。2.1.0（仓库钉的版本）和 2.2.4 不漏。
- **已修**：本地提交 `97295d9` 移植了上游 `294cb687f6`、`0f17a98793`，并改了 `sunnypilot/modeld_v2/modeld.py`。
- **没修**：`openpilot/selfdrive/car/helpers.py:45-47` 的 `custom.CarStateSP.new_message(**struct_dict)`，card 里 100 Hz 调用，card 同样关 GC。2.2.0 实测每次 8 个循环对象；改成 `new_message()` 后逐字段 `setattr` 为 0，`from_dict` 仍是 8。
- **其余路径实测为零**：`ModelSource.send_frame` + `pick`、插件 `decode` + `reject_reason`、`wire` 编解码、`CurveTarget.update`。
- **读代码未见泄露**：重连路径上 `VisionBuf`、编码器输出缓冲和 fd、VisionIPC 导入缓冲的分配和释放成对。
- **没有记录能回答的**：设备 `/usr/local/venv` 的 pycapnp 实际版本；Venus 驱动反复开关会话；长时间 RSS。所有记录里最长的运行是 870 s。V-20261107-08 那次 C3 意外重启的 ramoops 里有 lowmemorykiller，原因未定。

## 3. 任务包

顺序：P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7。P2、P3 需要设备；P1、P4、P5、P6 可以先在本机做完再上设备验证。

每个任务包都按 `AGENTS.md`：先在检查脚本的 docstring 里列失败用例；修复必须让至少一个新用例在基线提交上失败，跑一次基线证明；最后跑一次完整的 `tools/sp_tesla_e2e/*_check.py`。

### P0 推送 capnp 泄露修复（系统）

- 本地 `xpilot` 的 `97295d9` 还没推送。推送后看 CI。
- 本机没有编译好的 msgq，`plugin_host_check.py`、`phase2_check.py` 跑不了，只跑过 `static_imports.py`（通过）。
- 验证脚本（scratch，会被清掉，需要就照着重写）：在 `gc.disable()` 下调真实函数，数 `gc.collect()` 返回值。pycapnp 2.2.0 下基线 `907d153` 漏、新代码为 0，消息字节不变。

### P1 card 的同类泄露（系统）

- **文件**：`openpilot/selfdrive/car/helpers.py`，`convert_to_capnp`。
- **改法**：`custom.CarStateSP.new_message()` / `custom.CarParamsSP.new_message()` 之后逐字段赋值；值是 dict 的字段递归进子 struct（取出或 `init` 后再逐字段赋值），列表字段照 capnp 的 list 赋值方式处理。不要走 kwargs 或 `from_dict`。
- **失败用例**：(1) `CarStateSP` 每次调用留循环垃圾；(2) `CarParamsSP` 同；(3) 嵌套字段（如 `teslaLongControl`）丢值；(4) 新旧写法 `to_bytes()` 不一致；(5) `safetyParam` 的有符号转换变了。
- **验证**：pycapnp 2.2.0 下 `gc.disable()` 跑 5000 次，`gc.collect() == 0`；对一个填满字段的 dataclass，新旧写法字节相同。基线上用例 (1)(2) 失败。
- **完成标准**：以上通过，`static_imports.py` 通过。

### P2 分段计时（插件，需要设备）

目的：把 2.2 节那 3 ms 定位到具体段。**这一步不做任何优化。**

- **文件**：`camera.cc`、`camera.py`、`worker.py`。
- **做法**：每帧记下面这些时刻（都用 `CLOCK_BOOTTIME`，和 `timestamp_eof` 同源），随 `lean_capture` 返回一个定长数组，`link_stats` 每秒输出各段 p50 / p95：

  | 段 | 起止 | 对应原生 `bigStages` |
  |---|---|---|
  | `recv` | EOF → VisionIPC 收到（两路取晚的） | captureMs |
  | `warp` | 收到 → warp 完成 | warpMs |
  | `pair` | warp 完成 → 配对成立 | pairWaitMs |
  | `wake` | 配对成立 → 两路都已提交给 Venus | （原生没有这段） |
  | `encode` | 提交 → 两路都出包 | encodeMs |
  | `release` | 出包 → 输入缓冲归还（`released`） | （原生没有这段） |
  | `py_post` | `lean_capture` 返回 → `sendall` 之前 | （原生没有这段） |
  | `send` | `sendall` 两次的耗时 | sendMs |

- 去掉现在的 `capture` 字段（0.8.9 起它包含等下一帧，没有意义）。
- **失败用例**：(1) 各段之和与 `eof_to_send` + `send` 对不上（差超过 0.2 ms）；(2) 计时本身让 `eof_to_send` p50 变差超过 0.1 ms；(3) 跳帧、重连后数组里留着上一帧的时刻。
- **验证**：设备台架（方法见 `/home/martin/work/claude-codex-workspace/docs/bench-onroad.md` 和 `reports/T-20261110-01/capture.py`），冷态（cpuT < 65 °C）5 分钟，报告各段 p50 / p95 的表，和 2.2 节原生那一行并排。
- **完成标准**：表出来，差距落在哪几段有数字。把表写进本文件第 5 节。

### P3 追平原生（插件，需要设备）

**目标**：同一台架、冷态 5 分钟，`eof_to_send` p50 ≤ 5.8 ms（原生 5.31 + 0.5 余量），p95 ≤ 原生 EOF→可发送 p95 + 1 ms；`rtt` 相对当天基线不变（±0.5 ms）；采纳率不降。

**方法**：按 P2 的表从大到小改，每改一步跑 A/B/A 交替（各 5 分钟，对 0.8.12 加 P2 的基线），有数字再进下一步。**达到目标就停，后面的步骤不做。** 某一步收益小于 0.1 ms 就撤掉。

候选步骤，按预估收益排（P2 的数字会改这个顺序）：

1. **`py_post` 段**
   - `camera.py:51` 改成 `ctypes.string_at(b, n)`。
   - 关键帧标志由 C++ 返回（`packet_callback` 的 `key`），删掉 Python 的 `idr()` 扫描。宿主和手机仍要求双 IDR 是实测值，C++ 的 `key` 就是 Venus 的实测标志。
   - `lean_capture` 直接把两路包写进一块预分配的 FRAME 缓冲：road 从偏移 152 开始，wide 从 `152 + road_len + 4` 开始，尾部 16 字节零。Python 只用 `pack_into` 填 152 字节头和 4 字节 `wide_len`，一次 `sendall(memoryview)`。线长是 `172 + road_len + wide_len`，布局不变。
   - `sm.update(0)` 和 `vehicle()` 的旋转计算挪到 `camera.capture` 之前（那时主线程本来就在等下一帧）；编码后只做依赖 EOF 的新鲜度判断（`eof > timestamp + 100 ms`）。
2. **`release` 段和那次 `memcpy`**
   - 每路改成 3 个编码输入缓冲轮转：取帧线程直接 warp 进一个既不是"最新完成帧"也不是"Venus 占用中"的缓冲；配对后直接提交那个缓冲，不再 `memcpy`。
   - `collect` 只等出包，不再等 `released`。`input_done_callback` 只用来清"占用中"标记。
   - `V4LEncoder` 有 9 个输入槽（`BUF_IN_COUNT`），多个 `VisionBuf` 同时在途是允许的。
3. **`wake` 段**：配对成立后在取帧线程里直接提交两路编码（原生的做法），Python 主线程只等出包。注意 `reset` / 关键帧请求要在提交前生效，wide 在序列第 10 帧补 I 帧的逻辑要保留。
4. **`encode` 段**（只有 P2 显示它明显大于原生的 2.27 ms 才做）
   - 先确认是不是 Venus 争用：`XP_ENCODERD_DEFER_MS` 取 0 / 10 / 15 各跑一轮，看 `encode` 段。
   - 核对编码器参数和原生一致：HEVC Main、VBR、无 B 帧、GOP 20、`max_performance = true`、每路 2 Mb/s。
   - 核对线程位置：原生是 road 取帧线程绑 core 2、wide 绑 core 6、FIFO 50。插件现在是整个进程 `{2,6}` + FIFO 50，两条取帧线程没有分开绑。
5. **`recv` / `warp` 段**：和原生是同一份 camerad 和同一份 `WarpLut`，理论上一致。只有 P2 显示有差才看（先查线程绑核）。

**不做**：把发送搬进 C++（`send` 段原生也要 0.53 ms，Python 的两次 `sendall` 合成一次后差距应该在 0.1 ms 量级）；road 先发（V-20261107-10 已确认 0 收益）。如果前 3 步做完还差 0.5 ms 以上且 P2 指向 `send`，再回来评估。

- **失败用例**（列在检查里）：(1) FRAME 字节和旧实现对同一对输入不一致；(2) 关键帧标志和 Annex-B 实际 NAL 不一致；(3) 取帧线程写进 Venus 还占着的缓冲；(4) 未配对的帧占住缓冲不归还；(5) 重连后旧连接的出包混进新连接；(6) 链路在首帧前断开时关闭编码器卡死（`25ec4f0` 的回归）；(7) 标定未就绪时提交了帧（`5ff515d` 的回归）；(8) 队列满时仍然编码（0.8.12 的回归）。
- **验证**：现有 `plugins/lean_mici/tests/` 的 e2e（`e2e.py`、`recovery_e2e.py`、`phone_replay.py`）加上面的用例；设备 A/B/A。
- **完成标准**：达到本节开头的目标，或者列出每一步的实测收益并说明剩余差距在哪一段。

### P4 自愈和恢复速度（插件）

四项都在重连路径上，一个版本发。

1. **worker 监督**
   - `runner.py` 起的子进程改成一个很小的监督循环：`while 父进程还在: 起 worker → 等它退出或心跳超时 → 杀掉 → 退避后重起`。监督进程是在模型加载之前 fork 出来的，它再 fork worker 很便宜。**不要在已加载模型的 lean_modeld 进程里 fork**（页表大、会卡住 modeld，还会把 GPU 的 fd 带进子进程）。
   - 心跳：worker 主循环每圈写一个 `CLOCK_BOOTTIME` 时间戳到邮箱元数据槽的空闲区（`[160, 256)`，现在没人用；同步更新 `XPILOT.md` 4.3a 的 IPC 说明），超过 5 s 没更新就 `SIGKILL`。阻塞在 `encoder_close` 里的 worker 就靠这个回收。
   - 退避：重起间隔从 1 s 起翻倍到 30 s；每次重起记一条 `cloudlog`。
   - 保留现有的 `PR_SET_PDEATHSIG`，监督进程和 worker 都要设。
2. **地址缓存**：上次成功连上的 `(name, address, port)` 留在内存里，重连先试它；连续 3 次失败再 `discover()`（和原生一致）。固定（pin）的 NSD 名字检查不能绕过：缓存的地址必须来自通过了 pin 检查的那次发现。
3. **网络断开不重建编码器**：只有 `camera.capture` 抛 `RuntimeError`（编码器故障）才 `camera.close()` 重建；socket 异常只断连接、清队列、下次连接要求双 IDR 并递增 `epoch`。重连窗口里旧连接的在途出包不能发到新连接上。
4. **socket 超时 0.2 s → 1.0 s**：取 `origin/task/T-20261110-08`（`b98c491`）里的超时改动（`worker.py` 两处 `settimeout`、`wire.py` 的 `receive_reply` 截止）。**不取它的 `IN_FLIGHT = 20`**，保留 0.8.12 的 `IN_FLIGHT = 2` 加"队列满跳过编码"。那个分支分叉在 0.8.12 之前，不能直接合并；做完后关掉它。

- **失败用例**：(1) worker 被 `SIGKILL` / `SIGABRT` 后不再有回复；(2) worker 卡在一个永不返回的 native 调用里；(3) 监督进程在父进程死后还活着；(4) 手机重启后地址变了，缓存地址连不上却不重新发现；(5) 300 ms 的 TCP 卡顿导致断链和重新暖机；(6) 网络断开后编码器被重建；(7) 编码器故障后没有重建；(8) 换了手机（NSD 名字不匹配）却连上了；(9) 反复重起时重起间隔不退避。
- **验证**：`recovery_e2e.py` 扩展上述用例（假 peer、假 Camera 已有）；设备上手动：`kill -9` worker 一次、开关手机 Wi-Fi 一次，各记从事件到 `Phone active` 的秒数。
- **完成标准**：`kill -9` 后 10 s 内恢复采纳；手机 Wi-Fi 断 5 s 再回来，从链路恢复到采纳 ≤ 1.5 s（现在约 3.5 s）；300 ms 卡顿不断链。

### P5 desire 不丢（系统 + 插件，先系统）

做法和原生一致：宿主发**电平**，worker 做边沿检测。

- **系统**（`openpilot/tesla/model_source.py`，`XPILOT.md` 4.3a）
  - META 的 desire 字段改成发 `inputs['desire_pulse']` 的电平（通道 0 仍清零），不再发上升沿。
  - META 的 ABI 版本从 1 升到 2。旧插件的 `metadata()` 只认 `(b'XMS1', 1)`，读不到 META 就不发帧，结果是一直用本机：**版本错配时安全失败**。不升版本的话旧插件会把电平当脉冲每帧都发。
  - `tools/sp_tesla_e2e/model_source_e2e.py` 相应更新。
- **插件**（`worker.py`、`camera.cc` 里的 `Meta` 断言不涉及这个字段）
  - 认 ABI 1 和 2。ABI 2：worker 记上一帧**实际发出**的 desire 类别，当前类别非零且和上次不同才发脉冲；新序列（双 IDR）时重置锁存，desire 还在就在新序列首帧补发。ABI 1（旧宿主）保持现在的行为。
  - 手机侧要求每帧最多一个脉冲、`desire[0] == 0`（`xlink chipmunk_server.cpp:160-188`），这个约束不变。
- **失败用例**：(1) desire 上升沿那一帧被跳过（队列满），手机永远收不到；(2) 那一帧取帧失败；(3) 那一帧在等双 IDR；(4) desire 持续期间每帧都发脉冲；(5) 序列重置后 desire 仍在却不补发；(6) 新宿主 + 旧插件发出了帧；(7) 旧宿主 + 新插件行为变了。
- **验证**：宿主 e2e 加 (6)；插件 e2e 加其余。
- **完成标准**：以上通过；设备上打一次转向灯，`lean_mici.link` 或抓包里看到恰好一个脉冲。

### P6 记录手机分段计时（插件）

- REPLY（v4）偏移 8288 起有 4 个 u32：`srv_recv_us`、`srv_prep_us`、`srv_htp_us`、`srv_total_us`（`bigmodeld/frame_codec.h:16`）。`wire.py` 解出来，`link_stats` 每秒输出各自的 p50 / p95。
- 用途：以后每次台架都能把 `rtt` 拆成网络和手机，不用再靠相减。所有现存记录里 Wi-Fi 下的 NPU 时间都"拿不到"。
- **失败用例**：(1) 偏移错位读到输出或 NavExt；(2) 手机填 0 时算出负的网络时间。
- **完成标准**：台架日志里 `srv_htp_us` p50 在 13–15 ms 之间（纯手机实测 14.3 ms）。

### P7 设备上的三项测量（不改代码）

1. **pycapnp 版本**：`/usr/local/venv/bin/python -c "import capnp; print(capnp.__version__)"`。2.2.0–2.2.3 说明之前真在漏；记到本文件第 5 节。
2. **60 ms 截止 + 最新插件的热态台架**：`b6ca4bf` 把截止从 48 恢复成 60，理由是 0.8.9 起按相机到达发帧、不会再进慢态，但没有这个组合的热态记录。按 T-20261110-01 的方法 48 / 60 交替各两轮、每轮 5 分钟、热态（cpuT > 70 °C），看采纳率、发布 p50 / p95、`eof_to_send`。60 ms 采纳率不低于 48 ms 且 `eof_to_send` 不涨就保持 60。
3. **30 分钟内存浸泡**：采集脚本每分钟加一行 `lean_modeld`、worker、card 的 `VmRSS` / `VmPeak`（`/proc/<pid>/status`）和 `/proc/meminfo` 的 `MemAvailable`；期间开关手机 Wi-Fi 20 次（触发重连路径）。任何一项有持续斜率就单独开任务定位。worker 的 `VmPeak` 接近 1 GiB（`RLIMIT_AS`）也要报。

## 4. 不做的事和原因

| 想法 | 原因 |
|---|---|
| 关 Wi-Fi 省电 | V-20261107-06：只有一个有效窗口，ping 无实质变化；更早的 OPIF 记录里 C3 侧本来就是关的 |
| 固定热点信道 36 | V-20261107-11：p50 28.95 → 29.29，p99 35.85 → 37.67，被否 |
| DSCP EF / `SO_PRIORITY 6` | xlink `8bee6dd` 撤掉：AC_VO 通常关 A-MPDU 聚合，8.5 KB 的回复被拆成 6 帧，重传更多 |
| TOS / quickack / rto_min 一揽子 | V-20261107-04、-05：p50 不变，尾部改善"未超出噪声" |
| 改 2 s 存活等待、0.5 s 淡入淡出、自适应帧延迟 | 用户 2026-11-08 定的口径，和 dev-sp-npu 一致 |
| `IN_FLIGHT = 20` | 卡顿后会补发最多 1 s 的旧帧；和 0.8.12 的"队列满跳过编码"相抵 |
| xpilot-plugins PR #47（按 META 唤醒 worker） | 0.8.9 起 worker 按相机到达发帧，不再等 META，这个 PR 已过时，关掉 |
| 手机图优化（Slice→Split、FP16 循环存储） | xlink 记录：前者更慢，后者只省 0.05–0.08 ms |

## 5. 结果记录

每个任务包做完在这里加一小节：提交号（本地和设备上生效的分开写）、命令和退出码、关键数字。

台架统一说明：C3（AGNOS 19.9）连手机热点，无车。模拟点火、`FAKESEND`、`FINGERPRINT=MOCK`，Panda 保持 `noOutput`。脚本和原始数据在设备 `/data/bench-PL/`（`bench.sh`、`an.py`、`<轮次>.jsonl/.events/.mem`）。设备系统是分支 `phone-link` 上的 `d82bf47`，内容等于 dev-sp-npu `xpilot` 的 `09ccbc3`（`e3eea72` 加上 `97295d9`、`fafa56a`、`a3aec45`、`09ccbc3`）。

- P0：`97295d9` 已推送到 dev-sp-npu `xpilot`，CI 通过。pycapnp 2.2.0 下基线 `907d153` 四处都漏（退出码 1），新代码为 0（退出码 0）；2.1.0 下新代码为 0。
- P7.1：设备 `/usr/local/venv` 的 pycapnp 是 **2.1.0**，没有在漏。P0、P1 是预防性修复。
- P1：`fafa56a`。`safety_manifest_check.py` 新增 G15（无循环垃圾）、G16（嵌套字段）。pycapnp 2.2.0 下基线失败，报 `G15 CarParamsSP: 32000 cyclic objects/2000 calls`，退出码 1；新代码在 2.1.0 和 2.2.0 下都通过。新旧写法 `to_bytes()` 相同，`static_imports.py` 通过。
- P5 系统部分：`a3aec45`（META ABI 2，发电平）。`model_source_e2e.py --baseline e3eea72` 通过；基线 `e3eea72` 在 ABI 断言处失败（fake peer stalled）。注意：默认 `--baseline 48c8d50` 在基线上也失败（`default modeld output changed`），这个检查不在 CI 里，早已过时，需要另开任务更新基线。
- P4、P5 插件部分、P6：lean_mici 0.8.13，xpilot-plugins PR #48（`task/phone-link-p4`）。新增 `tests/link_e2e.py`，S1–S4、R1–R5、D1–D5 全部通过；在 0.8.12 上于 S1 失败。`recovery_e2e` 的 fixture 从 0.8.10 起就会卡死（在 0.8.12 上同样失败），已一并修正。
  - 设备 R1（0.8.13，`avahi-browse` 不带 `-t`）：稳态采纳 100%；`port 7070` 断 5 s，断开结束后 1.84 s 首次采纳；300 ms 卡顿不断链。
  - 设备 R2（加 `-t` 和 P2 计时）：`kill -9` worker 后 3.0 s 首次采纳（连续 10 帧 3.44 s）；断 5 s 后 0.94 s 首次采纳（连续 10 帧 1.39 s）；300 ms 卡顿不断链。P4 的完成标准（10 s 内、≤1.5 s、不断链）全部达到。
  - P6：`srv_recv` 1.2、`srv_prep` 4.1、`srv_htp` **14.3**、`srv_total` 19.6、`net` 4.0 ms（p50），达标（13–15）。
- P2：`task/phone-link-p2`，`camera.cc` 增加 BOOTTIME 打点。R2 的各段 p50（ms）：

  | 段 | lean_mici（R2） | 原生 bigmodeld（V-20261107-03） |
  |---|---|---|
  | recv | 2.1 | 1.43 |
  | warp | 1.2 | 1.46 |
  | pair | 0.0 | 0.15 |
  | wake | 0.8 | — |
  | encode | 2.2 | 2.27 |
  | release | 0.1 | — |
  | py_post | 1.9 | — |
  | send | 0.6 | 0.53 |
  | eof_to_send | 8.8 | 5.31 |

  差距主要在 `py_post`（1.9）、`wake`（0.8）和 `recv`（+0.7）。`release` 只有 0.1，2.3 节第 2 条（三缓冲）的收益上限很小。R2 开始时设备最高温区 75 °C，不是冷态；P2 失败用例 (2) 的计时开销没有单独做 A/B。
- 内存：R2 中 worker VmPeak 1013 MB，离 `RLIMIT_AS`（1 GiB）只剩 3%。RSS 是 59 MB。`/proc/<pid>/maps` 里匿名映射共 726 MB，其中 9 块 ≥60 MB，是每线程 64 MB 的 glibc malloc arena；dmabuf 169 MB；12 个线程。0.8.13 加 `mallopt(M_ARENA_MAX, 2)`（`bf20b60`），效果由 P7.3 验证。
- P3 第 1 步（`task/phone-link-p3`）：关键帧标志由 C++ 返回、`ctypes.string_at`、`sm.update`/`vehicle()` 挪到编码前、一次 `sendall`。设备 K 轮（60 s，1130 帧）：C++ 关键帧位和 Annex-B NAL 扫描结果全部一致。A/B/A/B 各 300 s（A = P2 版，B = 第 1 步）：

  | 轮次 | eof_to_send | py_post | recv | encode | wake | rtt | 采纳率 |
  |---|---|---|---|---|---|---|---|
  | A1 | 9.9 | 1.8 | 2.5 | 2.9 | 0.9 | 23.9 | 100% |
  | B1 | 8.8 | 0.3 | 2.7 | 3.0 | 0.9 | 23.7 | 100% |
  | A2 | 10.0 | 1.9 | 2.8 | 2.6 | 0.9 | 23.7 | 99.98% |
  | B2 | 8.5 | 0.3 | 2.9 | 2.6 | 0.9 | 23.6 | 99.97% |

  第 1 步省 1.1–1.5 ms，`rtt` 和采纳率不变。离目标 5.8 ms 还差约 3 ms，落在 `recv`（比原生多 1.1–1.5）、`encode`（多 0.3–0.7）和 `wake`（0.9）。设备一直在升温，`recv` 和 `encode` 随轮次变大。下一步候选：两条取帧线程分别绑 core 2 / core 6（代码已在分支上，未测）、`XP_ENCODERD_DEFER_MS` 0/10/15、三缓冲去掉 `memcpy`。
- **台架被内存泄露打断（原有问题，和本方案无关）**：每轮台架（停 manager → 模拟上车 → 恢复 manager）后 `MemAvailable` 永久少约 170 MB（R1 2322 → … → S 1131 MB），S 开始约 45 s 就因低于 400 MB 自动中止，后面 P7.2、P7.3、绑核、encoderd 延后各轮全部同样中止，没有数据。0.8.10–0.8.12 加旧系统的台架（`/data/bench-T-20261110-10`）同样每轮约 −170 MB。`/sys/kernel/debug/dma_buf/bufinfo`：530 个 ion buffer 共 2358 MB，几乎全部挂在 `mdss_mdp`（显示）上：180 × 9.18 MB（约一屏 RGBA）、162 × 4.7 MB。看起来是 UI 每次重启后显示缓冲没有释放。车上如果 manager 或 UI 重启也会累积；V-20261107-08 的意外重启（ramoops 里有 lowmemorykiller）可能是同一原因。需要单开任务查，C3 重启后才能继续台架。
- 清理：xpilot-plugins PR #47 已关闭；`task/T-20261110-08` 分支已删除（它的超时改动已并入 0.8.13）。
