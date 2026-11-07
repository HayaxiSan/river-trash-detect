# 05 · 各模块独立运行与验证

> 设计原则：**每个环节都能单独跑**，不依赖上一个环节。这样任何一步出问题都能快速定位。

工作目录统一：

```bash
cd /home/orangepi/trash_detect
```

## 1. 模块 1：拍照（capture.py）

```bash
python3 scripts/capture.py
```

| 项 | 说明 |
|---|---|
| 输入 | `/dev/video11`（MIPI 摄像头） |
| 参数 | UYVY / 640×640 / WARMUP=20 / FLIP_180=True |
| 输出 | 终端打印 `captured frame: 640x640, xxx KB in RAM` |
| 产物 | **不落盘**（只验证出流） |

常见失败：

| 报错 | 原因 |
|---|---|
| `ERROR: cannot open /dev/video11` | overlay 没开、摄像头没接 Cam3 |
| 画面全绿 | ISP 3A 没跑（`systemctl status rockchip-isp`） |
| 画面上下颠倒 | 摄像头装反，`FLIP_180` 改 `False` |

## 2. 模块 2：单图推理（infer.sh，走 C demo）

```bash
# 先把测试图放进 images/
cp bus.jpg images/
bash scripts/infer.sh bus.jpg
```

内部执行：

```bash
cd /home/orangepi/trash_detect
export LD_LIBRARY_PATH=./lib
./bin/rknn_yolov8_demo model/best_opt.rknn images/bus.jpg
rm -f out.png        # 产物即时删除，不落盘
```

| 项 | 说明 |
|---|---|
| 输入 | `images/` 下的任意 jpg |
| 输出 | 终端打印检测结果，末尾 `inference done (产物不落盘)` |
| 失败标志 | `inference failed: no out.png` |

## 3. 模块 3：视频实时推理（video_infer.py，走 lite2）

```bash
# a) 自检：用内置图验证「预处理→推理→后处理→画框」整条链路
python3 scripts/video_infer.py --mode check

# b) 实时：摄像头连续推理，终端打印每帧检测 + 跟踪结果（不落盘）
python3 scripts/video_infer.py --mode live

# c) 单图
python3 scripts/video_infer.py --mode image --img images/bus.jpg
```

| 模式 | 作用 | 预期输出 |
|---|---|---|
| `check` | 不碰摄像头，只用内置图 | 打印检出类别/置信度，`CHECK OK` |
| `live` | 摄像头实时 | 每秒打印 FPS 与检测框信息 |
| `image` | 单图 | 打印该图检测结果 |

关键参数（脚本顶部）：

| 参数 | 值 |
|---|---|
| `MODEL` | `/home/orangepi/trash_detect/model/best_opt.rknn` |
| `CAM` | `/dev/video11` |
| `OBJ_THRESH` / `NMS_THRESH` | 0.25 / 0.45 |
| `TRACK_ARGS` | high 0.5 / low 0.1 / new 0.6 / buffer 30 / match 0.8 |

## 4. 模块 4：推理 + 推流（rtsp_infer_stream.py，常驻服务）

```bash
# 前台试跑（看日志）
python3 scripts/rtsp_infer_stream.py

# 指定编码帧率
python3 scripts/rtsp_infer_stream.py --fps 30

# 平时用服务跑
sudo systemctl restart rtsp-stream
journalctl -u rtsp-stream -f
```

链路：

```text
OpenCV 取 /dev/video11 → RKNNLite 推理 → 画框
        → appsrc 注入 GStreamer → mpph264enc 硬编 → h264parse → flvmux → rtmp2sink
        → rtmp://127.0.0.1:1935/live/myStream
        → MediaMTX 分发 → rtsp://<板IP>:8554/live/myStream
```

| 项 | 值 |
|---|---|
| 编码 | MPP 硬编（`mpph264enc`） |
| GOP | 16（约 1 秒一个 I 帧） |
| 实测帧率 | 26 fps |
| NPU 核心 | 单核 |

> 为什么不用 ffmpeg：ffmpeg 的 v4l2 取帧时间戳异常，推出来的流时间轴错乱。推流必须走 GStreamer + MPP。

## 5. 模块 5：MQTT 告警（alert_mqtt.py）

```bash
# 自测：发两条 active 消息（验证发布 + 累计去重）
python3 scripts/alert_mqtt.py

# 另开一个终端订阅验证
python3 scripts/listen_alert.py
```

| 项 | 值 |
|---|---|
| Broker | `127.0.0.1:1883`（板端本机 mosquitto） |
| Topic | `trash_detect/alert` |
| 有目标 | 发 `status=active`，含 `counts`（本帧）+ `totals`（开机累计） |
| 无目标 | **不发消息**（手机端警报自然消失） |
| 累计口径 | 按 `(类别, track_id)` 去重；进程内存；**重启清零** |

集成方式：`video_infer.py` 与 `rtsp_infer_stream.py` 内 `from alert_mqtt import AlertPublisher`，检测到目标时首次立即发、持续时每 3 秒一条。

详见 [06_mqtt_alert.md](06_mqtt_alert.md)。

## 6. 模块 6：OLED 温度屏（thermal_monitor.py）

```bash
python3 scripts/thermal_monitor.py --interval 1
```

| 项 | 值 |
|---|---|
| 屏 | SSD1306，128×64，I2C1 `0x3C` |
| 温度来源 | `/sys/class/thermal/thermal_zone{0,1,3,5,6}`（芯片/大核/小核/GPU/NPU） |
| 状态字 | 右侧竖排：`正常` / `高温>75` / `请降温>85` / `停止使用>105` |
| 字体 | `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` |
| 服务间隔 | `--interval 4` |

报错 `PermissionError: /dev/i2c-1` → `sudo usermod -aG i2c orangepi` 后重登（服务里已配 `SupplementaryGroups=i2c`）。

## 7. 辅助脚本

| 脚本 | 用途 | 命令 |
|---|---|---|
| `smoke_tracker.py` | 验证 scipy/lap/BYTETracker 可导入可实例化 | `python3 scripts/smoke_tracker.py` |
| `listen_alert.py` | 订阅 12 秒打印告警（验证发布-订阅链路） | `python3 scripts/listen_alert.py` |
| `live_check.py` | 轻量取流检查 | `python3 scripts/live_check.py` |

## 8. 历史备用方案（非主链路）

| 脚本 | 方案 | 状态 |
|---|---|---|
| `mjpeg_stream.py` | HTTP MJPEG 网页推流 | 可用，非主链路 |
| `uvc_stream.py` + `uvc_gadget_init.sh` / `uvc_gadget_enable.sh` | USB UVC 摄像头模式（插电脑当摄像头） | 已验证，后因双 C 口供电问题改 WiFi RTSP |
| `rtsp_stream.sh` | 纯推流（不推理），验证摄像头→RTSP 通路 | 可用 |

## 9. 完整验收顺序

```bash
# 1) 硬件
python3 scripts/capture.py

# 2) 模型 + 官方 C 链路
bash scripts/infer.sh bus.jpg

# 3) Python 推理链路
python3 scripts/video_infer.py --mode check

# 4) 视频链路（服务方式）
sudo systemctl restart rtsp-stream && journalctl -u rtsp-stream -f
#    另一台设备：VLC 打开 rtsp://<板IP>:8554/live/myStream（缓存 50ms）

# 5) 告警链路
python3 scripts/listen_alert.py   # 终端看是否有 ALERT RECV
```

五步全过即为复现成功，与 [07_benchmark.md](07_benchmark.md) 的实测数据对标。
