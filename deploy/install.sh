#!/bin/bash
# 一键部署：把仓库内容装到板端固定路径 /home/orangepi/trash_detect
# 用法：sudo bash deploy/install.sh
# 说明：脚本内所有路径与板端脚本中的硬编码路径保持一致，改动需同步改脚本。
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="/home/orangepi/trash_detect"
OWNER="orangepi"

echo "[1/6] 创建板端目录 $DEST"
mkdir -p "$DEST"/scripts/tracker "$DEST"/model "$DEST"/bin "$DEST"/lib "$DEST"/images "$DEST"/output "$DEST"/logs

echo "[2/6] 复制脚本 / 模型 / 二进制 / 库"
cp -f "$REPO_DIR"/scripts/*.py "$DEST"/scripts/
cp -f "$REPO_DIR"/scripts/*.sh "$DEST"/scripts/
cp -f "$REPO_DIR"/scripts/tracker/*.py "$DEST"/scripts/tracker/
cp -f "$REPO_DIR"/models/best_opt.rknn "$DEST"/model/
cp -f "$REPO_DIR"/models/coco_80_labels_list.txt "$DEST"/model/
cp -f "$REPO_DIR"/bin/rknn_yolov8_demo "$DEST"/bin/
cp -f "$REPO_DIR"/lib/librknnrt.so "$DEST"/lib/
cp -f "$REPO_DIR"/lib/librga.so "$DEST"/lib/
chmod +x "$DEST"/bin/rknn_yolov8_demo "$DEST"/scripts/*.sh
chown -R "$OWNER":"$OWNER" "$DEST"

echo "[3/6] 安装 NPU 运行时到系统库"
cp -f "$REPO_DIR"/lib/librknnrt.so /usr/lib/
ldconfig

echo "[4/6] 安装 systemd 服务"
cp -f "$REPO_DIR"/deploy/*.service /etc/systemd/system/
systemctl daemon-reload

echo "[5/6] 安装 MQTT 配置并开放 1883"
if [ -d /etc/mosquitto/conf.d ]; then
  cp -f "$REPO_DIR"/configs/mosquitto.conf /etc/mosquitto/mosquitto.conf
  cp -f "$REPO_DIR"/configs/mosquitto_external.conf /etc/mosquitto/conf.d/external.conf
  systemctl restart mosquitto 2>/dev/null || echo "  (mosquitto 未安装，跳过)"
else
  echo "  (mosquitto 未安装，跳过)"
fi

echo "[6/6] 启用并启动服务"
if [ ! -x /opt/mediamtx/mediamtx ]; then
  echo "  ⚠ /opt/mediamtx/mediamtx 不存在：请先按 docs/04_deploy.md 下载 MediaMTX 官方 release 并解压到 /opt/mediamtx"
else
  systemctl enable --now mediamtx.service
fi
systemctl enable --now rockchip-isp.service 2>/dev/null || echo "  (rockchip-isp 需先装 camera-engine-rkaiq，见 docs/02)"
systemctl enable --now rtsp-stream.service 2>/dev/null || echo "  (rtsp-stream 启动失败，检查 mediamtx 与摄像头)"
systemctl enable --now thermal-monitor.service 2>/dev/null || echo "  (thermal-monitor 启动失败，检查 OLED 接线)"

echo
echo "部署完成。验证："
echo "  systemctl status rtsp-stream mediamtx thermal-monitor mosquitto"
echo "  python3 $DEST/scripts/capture.py"
echo "  RTSP: rtsp://<板子IP>:8554/live/myStream"
