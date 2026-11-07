# 02 · 系统与环境准备

## 1. 系统基线（实测）

| 项 | 值 |
|---|---|
| 发行版 | Ubuntu 22.04.5 LTS（Orange Pi 1.2.4 Jammy 镜像） |
| 内核 | `6.1.99-rockchip-rk3588` |
| glibc | 2.35 |
| Python | 3.10（系统自带） |

```bash
uname -r          # 6.1.99-rockchip-rk3588
lsb_release -ds   # Ubuntu 22.04.5 LTS
```

## 2. 打开设备树 overlay（关键第一步）

编辑 `/boot/orangepiEnv.txt`：

```text
overlays=i2c1-m4 ov13855-c3 wifi-ap6275p
```

| overlay | 作用 |
|---|---|
| `ov13855-c3` | 摄像头接 Cam3 |
| `i2c1-m4` | I2C1 复用组（16/18 引脚），OLED 用 |
| `wifi-ap6275p` | AP6275P WiFi 模块 |

改完重启，验证：

```bash
ls /dev/video11 /dev/i2c-1
```

> 仓库 `configs/orangepiEnv.txt` 是本项目实际使用的完整文件，可直接对照（注意 `rootdev` 的 UUID 每张卡不同，不要整体覆盖）。

## 3. ISP 3A 服务（画面偏绿的根因）

OV13855 出流必须由 `rkaiq_3A_server` 接管自动曝光/白平衡；服务停了画面立刻偏绿。

安装包（**必须用 0.4 版**）：

```text
camera-engine-rkaiq_0.4-5.0x4.1_arm64.deb
```

> 0.7 版（`0.7-6.0x8.0`）依赖 glibc 2.38，Jammy 是 2.35，装了起不来 —— 这是本项目踩过的坑。

安装与服务：

```bash
sudo dpkg -i camera-engine-rkaiq_0.4-5.0x4.1_arm64.deb
sudo systemctl enable --now rockchip-isp.service
systemctl status rockchip-isp.service   # active (running)
```

服务单元见 `deploy/rockchip-isp.service`，实际执行 `/usr/local/bin/rockchip-isp.sh start`。

## 4. 系统依赖（apt，实测版本）

```bash
sudo apt-get install -y \
  ffmpeg \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  python3-gi gir1.2-gstreamer-1.0 \
  gstreamer1.0-rockchip1 \
  librockchip-mpp1 librockchip-mpp-dev librockchip-vpu0 \
  librga2 libv4l-rkmpp rockchip-multimedia-config \
  fonts-wqy-zenhei \
  mosquitto mosquitto-clients
```

| 包 | 实测版本 | 用途 |
|---|---|---|
| `camera-engine-rkaiq` | 0.4-5.0x4.1 | ISP 3A（单独装，见上） |
| `ffmpeg` | 7:6.0.1-1+git20240229 | 备用（**v4l2 取帧时间戳异常，不用于推流**） |
| `gstreamer1.0-rockchip1` | 1.14-4+git240417 | MPP 插件（`mpph264enc` 硬编） |
| `librockchip-mpp1` / `-dev` | 1.5.0-1+git20240612 | 媒体处理平台 |
| `librockchip-vpu0` | 1.5.0-1+git20240612 | VPU |
| `librga2` | 2.2.0-1+git20231208 | RGA 2D 加速 |
| `libv4l-rkmpp` | 1.7.0-1+git240515 | v4l2 rkmpp 包装 |
| `fonts-wqy-zenhei` | 0.9.45-8 | OLED 中文字体 |
| `mosquitto` | 2.0.11-1ubuntu1.2 | MQTT broker |

## 5. Python 依赖（pip，实测版本）

```bash
pip3 install --user -r requirements.txt
```

`requirements.txt` 内容（板端 `runuser -u orangepi -- python3 -m pip list` 实查）：

```text
cbor2==6.1.5
lap==0.5.14
luma.core==2.6.0
luma.oled==3.16.0
numpy==2.2.6
opencv-python-headless==5.0.0.93
paho-mqtt==2.2.0
pillow==12.3.0
psutil==7.2.2
rknn-toolkit-lite2==2.3.2
scipy==1.15.3
smbus2==0.6.1
```

> `lap`、`scipy` 是 ByteTrack 的依赖；`luma.*` + `smbus2` + `pillow` + `cbor2` 是 OLED 的依赖。

## 6. NPU 运行时替换（版本必须与模型转换工具一致）

```bash
sudo cp lib/librknnrt.so /usr/lib/
sudo ldconfig
```

验证：

```bash
python3 -c "from rknnlite.api import RKNNLite; print('lite2 ok')"
```

> `rknn_server` **不需要常驻**：日常推理走 `rknn-toolkit-lite2`（lite2）即可，只有在「连板调试」（PC 侧 toolkit 驱动板端推理）时才需要启动。本仓库不依赖它。

## 7. 离线安装（板端没有外网时必看）

板端 apt/pip 全部超时是本项目最常见的阻塞。做法是**在电脑侧下好 aarch64 包，再推到板子安装**。

### 7.1 Python wheel（在 WSL / Linux 电脑上）

```bash
pip download -r requirements.txt \
  --platform manylinux2014_aarch64 \
  --only-binary=:all: --python-version 310 \
  -d wheels_aarch64/
```

推到板子并安装：

```bash
adb push wheels_aarch64/ /home/orangepi/
adb shell "runuser -u orangepi -- pip3 install --user --no-index --find-links /home/orangepi/wheels_aarch64 -r requirements.txt"
```

### 7.2 deb 包

```bash
# 电脑侧（同版本 Jammy aarch64）
apt-get download <包名>
# 推过去
adb push *.deb /home/orangepi/
adb shell "sudo dpkg -i /home/orangepi/*.deb"
```

> 本项目实测：mosquitto 及其 5 个依赖 deb、rkaiq 0.4 deb、luma/scipy/lap 等 wheel 全部走这条路装成功。注意 deb 文件名里的 `%3a`/`%3A` 编码问题，下载后先重命名成正常的 `包名_版本_arm64.deb` 再 `dpkg -i`。

## 8. 网络与外网配置（可选）

板子自建热点需要 `create_ap`（官方镜像预装于 `/usr/local/bin/create_ap`）：

```bash
sudo systemctl enable --now create_ap.service
# SSID orangepi / 密码 orangepi，ap0 = 192.168.12.1
```

## 9. 验证环境是否就绪

```bash
# 摄像头
python3 scripts/capture.py                 # 应打印 captured frame: 640x640

# ISP 3A
systemctl is-active rockchip-isp.service   # active

# 模型运行时
python3 -c "from rknnlite.api import RKNNLite; print('ok')"

# GStreamer 硬编插件
gst-inspect-1.0 mpph264enc | head -5

# MQTT
mosquitto -h 2>&1 | head -2                # mosquitto version 2.0.11
```

全部通过后，进入 [03_model.md](03_model.md)。
