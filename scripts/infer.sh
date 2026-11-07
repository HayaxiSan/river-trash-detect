#!/bin/bash
# Module 2: inference module - standalone runnable
# usage: bash infer.sh <image_name_in_images/>
# 产物不落盘：推理后仅打印结果，C demo 生成的 out.png 即时删除
cd /home/orangepi/trash_detect
export LD_LIBRARY_PATH=./lib
IMG="$1"
if [ -z "$IMG" ]; then echo "usage: bash scripts/infer.sh <image_in_images/>"; exit 1; fi
./bin/rknn_yolov8_demo model/best_opt.rknn "images/$IMG"
if [ -f out.png ]; then
  rm -f out.png
  echo "inference done (产物不落盘)"
else
  echo "inference failed: no out.png"
  exit 1
fi
