#!/bin/bash
# uvc_gadget_enable.sh - 启用 UVC 功能（依据 Radxa 官方文档，分辨率自定义 640x480/1280x720/1920x1080 YUYV）
# 用法: sudo bash uvc_gadget_enable.sh enable|disable
set -euo pipefail
ACTION=${1:-}
GADGET_NAME="fc000000.usb"
FUNCTION_NAME="uvc.0"
G="/sys/kernel/config/usb_gadget/${GADGET_NAME}"
F="${G}/functions/${FUNCTION_NAME}"

create_frame() {
  # create_frame <width> <height> <format> <name>
  local WIDTH=$1 HEIGHT=$2 FORMAT=$3 NAME=$4
  local wdir="${F}/streaming/${FORMAT}/${NAME}/${HEIGHT}p"
  mkdir -p "$wdir"
  echo "$WIDTH"  > "$wdir/wWidth"
  echo "$HEIGHT" > "$wdir/wHeight"
  echo $(( WIDTH * HEIGHT * 2 )) > "$wdir/dwMaxVideoFrameBufferSize"
  printf '333333\n166666\n666666\n100000\n' > "$wdir/dwFrameInterval"
}

disable_gadget() {
  [[ -d "${G}" ]] || return 0
  local cur
  cur=$(cat "${G}/UDC" 2>/dev/null || echo "")
  [[ -n "$cur" ]] && echo "" > "${G}/UDC" 2>/dev/null || true
  rm -f "${G}/configs/r.1/${FUNCTION_NAME}" 2>/dev/null || true
  [[ -e "${F}/streaming/header/h" ]] && {
    pushd "${F}/streaming/header/h"
    rm -f yuyv
    cd ../../class/fs && rm -f h
    cd ../../class/hs && rm -f h
    cd ../../class/ss && rm -f h
    cd ../../../control && rm -f class/fs/h class/ss/h && rmdir header/h 2>/dev/null || true
    popd
    rmdir "${F}/streaming/header/h" 2>/dev/null || true
  }
  [[ -e "${F}/streaming/uncompressed" ]] && {
    rmdir "${F}/streaming/uncompressed/yuyv/480p" 2>/dev/null || true
    rmdir "${F}/streaming/uncompressed/yuyv/720p" 2>/dev/null || true
    rmdir "${F}/streaming/uncompressed/yuyv/1080p" 2>/dev/null || true
    rmdir "${F}/streaming/uncompressed/yuyv" 2>/dev/null || true
  }
  rmdir "${F}" 2>/dev/null || true
}

enable_gadget() {
  UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
  if [[ -z "$UDC" ]]; then
    echo "ERROR: no UDC. 先切 device 模式: echo device > /sys/kernel/debug/usb/fc000000.usb/mode"
    exit 1
  fi
  disable_gadget
  mkdir -p "${G}/configs/r.1"
  mkdir -p "${F}"
  create_frame 640 480  uncompressed yuyv
  create_frame 1280 720 uncompressed yuyv
  create_frame 1920 1080 uncompressed yuyv
  mkdir -p "${F}/streaming/header/h"
  pushd "${F}/streaming/header/h"
  ln -sf ../../uncompressed/yuyv
  cd ../../class/fs && ln -sf ../../header/h
  cd ../../class/hs && ln -sf ../../header/h
  cd ../../class/ss && ln -sf ../../header/h
  cd ../../../control
  mkdir -p header/h
  ln -sf header/h class/fs
  ln -sf header/h class/ss
  popd
  echo 1    > "${F}/streaming_interval"
  echo 3072 > "${F}/streaming_maxpacket"
  echo 15   > "${F}/streaming_maxburst"
  ln -sf "${F}" "${G}/configs/r.1/${FUNCTION_NAME}"
  echo "${UDC}" > "${G}/UDC"
  echo "UVC GADGET ENABLED (UDC=${UDC})"
}

case "$ACTION" in
  enable)  enable_gadget ;;
  disable) disable_gadget; echo "UVC GADGET DISABLED" ;;
  *) echo "用法: sudo bash uvc_gadget_enable.sh enable|disable" ;;
esac
