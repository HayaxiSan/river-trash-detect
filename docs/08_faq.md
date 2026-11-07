# 08 · 常见问题与踩坑记录

> 全部为本项目真实踩过的坑，附根因与解法。完整过程见 `logs/` 13 篇日志。

## 1. 画面整体偏绿

**两条独立的成因，都要处理：**

| 成因 | 表现 | 解决 |
|---|---|---|
| ISP 3A 没接管 | 全屏绿，AE 不收敛 | `systemctl status rockchip-isp`，没跑就装 `camera-engine-rkaiq` 0.4 并启用服务 |
| FOURCC 未显式指定 | 颜色错乱/纯绿 | OpenCV 打开后必须 `cap.set(CAP_PROP_FOURCC, VideoWriter_fourcc(*"UYVY"))` |

另外两个注意点：

- 不要在脚本里写死 `analogue_gain` / `exposure`：会和 `rkaiq_3A_server` 的自动曝光打架（实测 AE 会自己设成 exposure≈5785 / gain≈1528）。
- 取图前丢弃前 20 帧（`WARMUP=20`），等 AE/AWB 收敛，否则前几帧偏色。

## 2. 摄像头打不开 `/dev/video11`

| 检查 | 命令 |
|---|---|
| overlay 是否开 | `cat /boot/orangepiEnv.txt`，需含 `ov13855-c3` |
| 节点是否存在 | `ls /dev/video11 /dev/video-camera0` |
| 排线是否插 Cam3 | 硬件检查 |

## 3. 推流一遇到目标就崩：`TypeError: int - tuple`

**根因**：ByteTrack 的 `matching.py` 里 `bbox_ioa()` 旧实现返回 `(ioa, ...)` 二元组，而上层按单值使用，`int - tuple` 直接抛异常。只在有检测框时才触发，所以"空场景能跑、一有垃圾就崩"。

**解法**：按 ultralytics main 分支 `metrics.py` / `matching.py` 的官方实现重写为单值返回。仓库内的 `scripts/tracker/matching.py` **已是修复版**。

## 4. 板端 apt / pip 全部超时

**根因**：板子没有外网（DNS 与代理都不通）。

**解法**：电脑侧下载 aarch64 包 → `adb push` → 板端本地安装。详见 [02_environment.md](02_environment.md) 第 7 节。

额外坑：deb 文件名里的 `%3a` / `%3A` 编码会让 `dpkg -i` 报找不到文件，下载后重命名再装。

## 5. 用 ffmpeg 推流时间戳错乱

**现象**：`ffmpeg -f v4l2` 取帧后推流，客户端卡顿、时间轴跳。

**根因**：ffmpeg 的 v4l2 输入在 RK3588 上拿不到正确时间戳。

**解法**：推流走 **GStreamer + MPP**（`appsrc → videoconvert → mpph264enc → h264parse → flvmux → rtmp2sink`）。ffmpeg 只用于其它转码场景。

## 6. Docker 容器里 NPU 报错

**现象**：容器内 `rknn_init` 失败，提示缺 `renderD129` 或 `/proc/device-tree`。

**根因**：NPU 设备节点与 device-tree 没进容器。

**已验证解法**：

```bash
docker run --privileged \
  -v /proc/device-tree:/proc/device-tree \
  ... 
```

单挂 `-v /dev/renderD129`、`--mount`、bind 均无效，**必须整目录挂载 + privileged**。

> 本项目最终**不采用容器**（用户决定宿主直跑，省去挂载复杂度）。此条仅作记录。

## 7. 改了脚本但行为没变

**根因**：脚本由 systemd 常驻运行，改文件不会自动重载，跑的还是旧进程。

**解法**：

```bash
sudo systemctl restart rtsp-stream
journalctl -u rtsp-stream -f     # 确认新进程日志
```

## 8. OLED 报 `PermissionError: /dev/i2c-1`

```bash
sudo usermod -aG i2c orangepi
# 重新登录生效；服务单元里已配置 SupplementaryGroups=i2c
```

## 9. 手机连不上板端 MQTT

三件事按顺序查：

1. 手机连的是**板子热点**（SSID `orangepi`），IP 应为 `192.168.12.x`；
2. Broker 地址填 **`192.168.12.1`**（不是 eth0 的 `192.168.137.159`）；
3. 板端有 `/etc/mosquitto/conf.d/external.conf`：`listener 1883 0.0.0.0` + `allow_anonymous true`，且 `ss -lntp | grep 1883` 能看到 `0.0.0.0:1883`。

## 10. 主题拼写

正确主题：**`trash_detect/alert`**。
写成 `trash_detect/slert` 收不到任何消息（本项目实际踩过）。

## 11. `rknn_server` 需要常驻吗

**不需要**。日常推理用 `rknn-toolkit-lite2`（lite2）直接跑；只有「PC 侧 Toolkit 连板调试」才需要板端起 `rknn_server`。本项目全部功能都不依赖它。

## 12. 模型加载失败 / 输出全零

按顺序查：

1. `librknnrt.so` 与 RKNN-Toolkit2 是否**同版本**（本项目 2.3.2 ↔ 2.3.2）；
2. `/usr/lib/librknnrt.so` 是否是仓库里的那份（`ldconfig` 生效了吗）；
3. `ldd bin/rknn_yolov8_demo` 是否找不到库（需 `export LD_LIBRARY_PATH=./lib`）。

## 13. 摄像头装反 / 画面上下颠倒

脚本内 `FLIP_180 = True` 做 180° 翻转。若安装方向正确，改 `False`（`capture.py`、`video_infer.py`、`rtsp_infer_stream.py` 三处一致）。

## 14. 单核 vs 三核 NPU

三核 AUTO 实测 27 fps，单核 26 fps，差 1 帧但发热与功耗更高 → 回退单核（`NPU_CORE_0`）。追求极限帧率再改。
