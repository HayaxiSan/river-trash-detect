# 04 · 部署与开机自启

## 1. 部署目标结构（板端）

```text
/home/orangepi/trash_detect/
├── scripts/                 # 全部脚本 + tracker/
├── model/
│   ├── best_opt.rknn
│   └── coco_80_labels_list.txt
├── bin/rknn_yolov8_demo
├── lib/librknnrt.so, librga.so
├── images/                  # 测试图（放自己的 jpg 供 infer.sh 用）
├── output/                  # 只放日志（不落盘产物）
└── logs/
```

> 路径是**硬编码**在脚本里的（`/home/orangepi/trash_detect/...`）。换路径要同时改 `scripts/video_infer.py`、`scripts/rtsp_infer_stream.py`、`scripts/infer.sh` 里的 `MODEL` / `cd` 行。

## 2. 一键部署

```bash
cd river-trash-detect
sudo bash deploy/install.sh
```

`install.sh` 依次做 6 件事：

| 步骤 | 动作 |
|---|---|
| 1 | 建 `/home/orangepi/trash_detect/{scripts,scripts/tracker,model,bin,lib,images,output,logs}` |
| 2 | 复制脚本、模型（`models/` → 板端 `model/`）、C 二进制、两个 .so；加执行权限；改属主 orangepi |
| 3 | `librknnrt.so` 拷到 `/usr/lib` 并 `ldconfig` |
| 4 | 5 个 `.service` 拷到 `/etc/systemd/system`，`daemon-reload` |
| 5 | mosquitto 主配置 + `conf.d/external.conf`（开放 1883） |
| 6 | `enable --now` 各服务 |

## 3. MediaMTX（RTSP 服务器，需单独下载）

仓库**不含** MediaMTX 二进制（约 66 MB，官方 release 更稳妥）。

```bash
# 板子上（或电脑下载后 adb push）
cd /tmp
# 官方 release 地址：https://github.com/bluenviron/mediamtx/releases
# 选 linux_arm64v8 版本，例如 mediamtx_v1.x.x_linux_arm64v8.tar.gz
sudo mkdir -p /opt/mediamtx
sudo tar xzf mediamtx_*_linux_arm64v8.tar.gz -C /opt/mediamtx
sudo cp configs/mediamtx.yml /opt/mediamtx/mediamtx.yml
sudo systemctl enable --now mediamtx.service
```

服务单元：

```ini
[Service]
ExecStart=/opt/mediamtx/mediamtx /opt/mediamtx/mediamtx.yml
Restart=always
RestartSec=3
```

仓库里的 `configs/mediamtx.yml`（38,936 字节）是**实际在跑**的配置，默认已监听：

| 协议 | 端口 |
|---|---|
| RTSP | 8554 |
| RTMP | 1935 |
| HLS | 8888 |
| WebRTC | 8889 |

推流端推到 `rtmp://127.0.0.1:1935/live/myStream`，客户端从 `rtsp://<板IP>:8554/live/myStream` 拉流。

## 4. 五个 systemd 服务

| 服务 | 作用 | 关键内容 |
|---|---|---|
| `rockchip-isp.service` | ISP 3A（防偏绿） | `Type=forking`，`/usr/local/bin/rockchip-isp.sh start` |
| `mediamtx.service` | RTSP/RTMP 服务器 | `/opt/mediamtx/mediamtx /opt/mediamtx/mediamtx.yml` |
| `rtsp-stream.service` | 推理 + MPP 硬编 + 推流 | `User=orangepi`，`WorkingDirectory=/home/orangepi/trash_detect/scripts`，`Wants/After=mediamtx.service` |
| `thermal-monitor.service` | OLED 温度屏 | `User=orangepi`，`SupplementaryGroups=i2c`，`--interval 4` |
| `create_ap.service` | 板子自建 WiFi 热点 | `create_ap -n wlan0 -c 36 --freq-band 5 orangepi orangepi` |

### 启动顺序（依赖关系）

```text
rockchip-isp（ISP 出流前提）
      ↓
mediamtx（先有 RTSP 服务器）
      ↓
rtsp-stream（推流到 1935）
```

`rtsp-stream.service` 已写 `Wants=mediamtx.service` + `After=mediamtx.service`，顺序由 systemd 保证。

## 5. 常用运维命令

```bash
# 状态
systemctl status rtsp-stream mediamtx mosquitto thermal-monitor rockchip-isp

# 看推理日志
journalctl -u rtsp-stream -f

# 改完脚本必须重启服务（不重启跑的是旧进程）
sudo systemctl restart rtsp-stream

# 关掉/打开某个服务
sudo systemctl stop rtsp-stream
sudo systemctl start rtsp-stream
```

> **铁律**：脚本改了必须 `systemctl restart`，否则日志与行为都来自旧进程 —— 本项目曾因此误判"改动没生效"。

## 6. 卸载 / 回滚

```bash
sudo systemctl disable --now rtsp-stream thermal-monitor mediamtx create_ap
sudo rm -rf /home/orangepi/trash_detect
sudo rm -f /etc/systemd/system/{rtsp-stream,thermal-monitor,mediamtx,create_ap,rockchip-isp}.service
sudo systemctl daemon-reload
```
