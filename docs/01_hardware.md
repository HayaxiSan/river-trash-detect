# 01 · 硬件清单与接线

## 1. 硬件清单

| 序号 | 部件 | 型号 / 规格 | 数量 | 备注 |
|---|---|---|---|---|
| 1 | 开发板 | 香橙派 5（RK3588S，NPU 6 TOPS） | 1 | 官方 Ubuntu 镜像 |
| 2 | 摄像头 | **OV13855** MIPI 摄像头模组 | 1 | 必须接 **Cam3** |
| 3 | 显示屏 | 0.96" SSD1306 OLED（128×64，I2C） | 1 | 地址 0x3C |
| 4 | 电源 | Type-C 5V / 3A 以上 | 1 | 建议官方电源 |
| 5 | 网线 | 任意 Cat5e | 1 | 电脑管理口（可选） |
| 6 | 散热 | 官方一体式风扇（可选） | 1 | 长时间推流建议加装 |
| 7 | 终端设备 | 手机 / 平板 / 电脑 | 1 | VLC 看 RTSP、MQTT 客户端收告警 |

> 摄像头不是 OV13855 时：本项目的推理链路不依赖具体 sensor，但 overlay 名、IQ 校准文件名、`/dev/video*` 编号都会变，需按所用 sensor 的官方 Wiki 调整（见 docs/02）。

## 2. 接线

### 2.1 摄像头（MIPI CSI）

```text
OV13855 排线 ──► 板子 Cam3 接口
```

- 对应设备树 overlay：`ov13855-c3`
- 取流节点：`/dev/video11`（`rkisp_mainpath`），别名 `/dev/video-camera0`
- sensor 控制口：`/dev/v4l-subdev2`
- ISP 校准文件：`/etc/iqfiles/ov13855_CMK-OT2016-FV1_default.json`（其中 `FpsValue=30`，实测输出 26fps）

### 2.2 OLED（I2C）

```text
OLED 引脚        26-pin 排针
VCC  ──────────  1  (3.3V)
GND  ──────────  9  (GND)
SDA  ──────────  16 (SDA)
SCL  ──────────  18 (SCL)
```

- 对应 overlay：`i2c1-m4`（I2C1 的 M4 复用组）
- I2C 总线：`/dev/i2c-1`，从机地址 `0x3C`
- 系统侧：`orangepi` 用户需加入 `i2c` 组才能访问

### 2.3 网线 / WiFi

| 接口 | 地址 | 用途 |
|---|---|---|
| `eth0` | `192.168.137.159/24` | 电脑直连管理（adb / SSH） |
| `ap0`（板子自建热点） | `192.168.12.1/24` | 手机/平板接入看视频、收 MQTT |

板子热点：`create_ap -n wlan0 -c 36 --freq-band 5 orangepi orangepi`
（SSID = `orangepi`，密码 = `orangepi`，5GHz 信道 36）

> 手机连上板子热点后，RTSP 与 MQTT 都用 **`192.168.12.1`**，不是 eth0 的 137 段地址。

## 3. 硬件参数速查

| 项 | 值 |
|---|---|
| 摄像头分辨率/帧率 | 640×640 / 26 fps（9 档 FpsValue=30） |
| 摄像头输出格式 | UYVY（必须显式指定，否则 OpenCV 会误解析成纯绿） |
| 摄像头安装方向 | 上下颠倒 → 脚本内 `FLIP_180 = True` |
| OLED 分辨率 | 128×64，SSD1306 |
| 中文字体 | `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`（`fonts-wqy-zenhei` 包） |
| WiFi 模块 | AP6275P（overlay `wifi-ap6275p`） |

## 4. 上电检查清单

```bash
# 1) 设备节点
ls /dev/video11 /dev/video-camera0 /dev/i2c-1

# 2) 摄像头能否出流（需要 ISP 3A 服务在跑）
v4l2-ctl -d /dev/video11 --info

# 3) OLED 是否在 I2C 总线上
i2cdetect -y -r 1     # 应能看到 0x3c

# 4) 网络
ip -br addr show
```

四项都正常，再进入 [02_environment.md](02_environment.md)。
