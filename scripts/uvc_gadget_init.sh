#!/bin/bash
# uvc_gadget_init.sh - 初始化 USB UVC Gadget（依据 Radxa 官方文档 Linux USB Gadget - UVC）
# 用法: sudo bash uvc_gadget_init.sh
set -euo pipefail
GADGET_NAME="fc000000.usb"
G="/sys/kernel/config/usb_gadget/${GADGET_NAME}"

if ! mountpoint -q /sys/kernel/config; then
  mount -t configfs none /sys/kernel/config
fi
modprobe libcomposite 2>/dev/null || true

if [[ -d "${G}" ]]; then
  echo "gadget already exists: ${GADGET_NAME}"
  exit 0
fi

mkdir -p "${G}"
cd "${G}"
echo 0x1d6b > idVendor
echo 0x0104 > idProduct
mkdir -p strings/0x409
echo "1234567890"        > strings/0x409/serialnumber
echo "OrangePi"          > strings/0x409/manufacturer
echo "OrangePi UVC Cam"  > strings/0x409/product
mkdir -p configs/r.1
mkdir -p configs/r.1/strings/0x409
echo "UVC Config" > configs/r.1/strings/0x409/configuration
echo "INIT OK: ${GADGET_NAME}"
