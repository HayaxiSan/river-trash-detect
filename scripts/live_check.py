#!/usr/bin/env python3
# Grab a truly live frame: reset params, flush buffers (drop 4 frames), take 5th
import cv2, subprocess, time
subprocess.run(["v4l2-ctl","-d","/dev/v4l-subdev2","--set-ctrl","analogue_gain=1000"],capture_output=True)
subprocess.run(["v4l2-ctl","-d","/dev/v4l-subdev2","--set-ctrl","exposure=3000"],capture_output=True)
time.sleep(0.5)
cap = cv2.VideoCapture("/dev/video11", cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"UYVY"))
for i in range(4):          # flush stale buffers
    cap.grab()
ok, frame = cap.read()       # take live frame
cap.release()
if not ok:
    print("READ FAILED"); exit(1)
b, g, r = frame[:,:,0].mean(), frame[:,:,1].mean(), frame[:,:,2].mean()
print("B=%.1f G=%.1f R=%.1f (产物不落盘)" % (b,g,r))
