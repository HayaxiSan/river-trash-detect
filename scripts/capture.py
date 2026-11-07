#!/usr/bin/env python3
# Module 1: capture - standalone camera module
# - grabs one frame from MIPI camera (rkisp_mainpath /dev/video11) to images/
#
# 2026-10-16 修订（偏绿根因修复后，见《偏绿修复方案_证据链与执行计划.md》）
#   1) 删除写死的 analogue_gain=1000 / exposure=3000
#      —— rkaiq_3A_server 的 AE 已接管曝光（实测会自动设为 exposure=5785 / gain=1528），
#         脚本里再写固定值会与 AE 互相打架。
#   2) 补上 FOURCC=UYVY
#      —— rkisp_mainpath 输出 UYVY；不显式指定时 OpenCV 会误按 YUYV 解析，产生纯绿伪彩
#         （这是与 ISP 无关的第二条偏绿路径）。
#   3) 增加 WARMUP 预热帧
#      —— 视频流刚启动时 3A 的 AE/AWB 尚未收敛，需丢弃前若干帧再取图。
#   4) 增加 FLIP_180（2026-10-15 补）
#      —— 摄像头安装在支架上画面上下颠倒，需 180° 翻转；若方向正确置 False。
import cv2
import time
import os

# ---- editable parameters ----
CAM = "/dev/video11"      # rkisp_mainpath
FOURCC = "UYVY"           # 必须显式指定，见上
WIDTH = 640
HEIGHT = 640
WARMUP = 20               # 丢弃前 N 帧，等 AE/AWB 收敛
FLIP_180 = True           # 画面倒置时置 True（180° 翻转），方向正确置 False
# 产物不落盘：拍照仅验证摄像头出流，不再写文件
# -----------------------------

cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
if not cap.isOpened():
    print("ERROR: cannot open", CAM)
    exit(1)

cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

frame = None
for _ in range(WARMUP):
    ok, f = cap.read()
    if ok:
        frame = f
cap.release()

if frame is None:
    print("ERROR: read frame failed")
    exit(1)

if FLIP_180:
    frame = cv2.flip(frame, -1)

print("captured frame: %dx%d, %.1f KB in RAM (产物不落盘)" %
      (frame.shape[1], frame.shape[0], frame.nbytes / 1024.0))
