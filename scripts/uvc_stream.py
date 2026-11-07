#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module: uvc_stream - USB UVC 实时推理推流（摄像头 → NPU 推理 → 带框图 → UVC 节点 → Type-C → 平板）
============================================================
依据（官方/网络搜索背书）：
  1. Radxa 官方文档《Linux USB Gadget - UVC》：UVC gadget 输出节点 /dev/video20，
     用户态应用直接 V4L2 write 帧（VIDIOC_S_FMT + write），无需 GStreamer
  2. 香橙派官方手册 3.34：OpenCV 打开 MIPI 摄像头（rkisp_mainpath /dev/video11）
  3. 官方 rknn_model_zoo yolov8 python demo：预处理/后处理（与 video_infer.py 同源）
  4. 网络搜索 10 号 #1：RK3588S 边缘 AI 摄像头（推理叠加 → 编码 → 输出预览）

用法：
  python3 uvc_stream.py               # UVC 推流（带框）
  python3 uvc_stream.py --no-infer    # 原始画面直推（不带框，验证链路用）
"""
import cv2
import os
import sys
import time
import struct
import fcntl
import argparse
import numpy as np
from rknnlite.api import RKNNLite

# ---------------- 可调参数 ----------------
MODEL = "/home/orangepi/trash_detect/model/best_opt.rknn"
CAM = "/dev/video11"            # rkisp_mainpath（官方手册 3.34）
UVC_DEV = "/dev/video20"        # dwc3-gadget UVC 输出节点（Radxa UVC 文档）
FOURCC = "UYVY"                 # 摄像头显式 UYVY，否则 OpenCV 误按 YUYV 出纯绿
WIDTH = 640
HEIGHT = 480                    # UVC gadget 支持 640x480/720p/1080p YUYV
WARMUP = 20                     # 丢弃前 N 帧，等 3A 的 AE/AWB 收敛
FLIP_180 = True                 # 摄像头安装方向（capture.py 同款）
FPS = 30
OBJ_THRESH = 0.25
NMS_THRESH = 0.45
IMG_SIZE = (640, 640)           # 模型输入 (width, height)
UYVY_FOURCC = 0x56595559        # 'YUYV'（UVC gadget 配的是 YUYV，Radxa 官方示例用 V4L2_PIX_FMT_YUYV）
V4L2_BUF_TYPE_VIDEO_OUTPUT = 9
VIDIOC_S_FMT = 0xC0CC5605       # _IOWR('V', 5, struct v4l2_format) 64位：sizeof=204
LOG = "/home/orangepi/trash_detect/output/uvc_stream.log"
# -----------------------------------------

# 官方 80 类（照抄 rknn_model_zoo yolov8.py）
CLASSES = ("person", "bicycle", "car", "motorbike", "aeroplane", "bus", "train", "truck", "boat",
           "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
           "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
           "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
           "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
           "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
           "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
           "sofa", "pottedplant", "bed", "diningtable", "toilet", "tvmonitor", "laptop", "mouse",
           "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
           "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush")


# ---------------- 后处理（照官方 yolov8.py，dfl 改 numpy 免 torch） ----------------
def dfl(position):
    n, c, h, w = position.shape
    p_num = 4
    mc = c // p_num
    x = position.reshape(n, p_num, mc, h, w)
    x = x - x.max(axis=2, keepdims=True)
    e = np.exp(x)
    y = e / e.sum(axis=2, keepdims=True)
    acc = np.arange(mc, dtype=np.float32).reshape(1, 1, mc, 1, 1)
    return (y * acc).sum(axis=2)


def box_process(position):
    grid_h, grid_w = position.shape[2:4]
    col, row = np.meshgrid(np.arange(0, grid_w), np.arange(0, grid_h))
    col = col.reshape(1, 1, grid_h, grid_w)
    row = row.reshape(1, 1, grid_h, grid_w)
    grid = np.concatenate((col, row), axis=1)
    stride = np.array([IMG_SIZE[1] // grid_h, IMG_SIZE[0] // grid_w]).reshape(1, 2, 1, 1)
    position = dfl(position)
    box_xy = grid + 0.5 - position[:, 0:2, :, :]
    box_xy2 = grid + 0.5 + position[:, 2:4, :, :]
    xyxy = np.concatenate((box_xy * stride, box_xy2 * stride), axis=1)
    return xyxy


def filter_boxes(boxes, box_confidences, box_class_probs):
    box_confidences = box_confidences.reshape(-1)
    candidate, class_num = box_class_probs.shape
    class_max_score = np.max(box_class_probs, axis=-1)
    classes = np.argmax(box_class_probs, axis=-1)
    _class_pos = np.where(class_max_score * box_confidences >= OBJ_THRESH)
    scores = (class_max_score * box_confidences)[_class_pos]
    boxes = boxes[_class_pos]
    classes = classes[_class_pos]
    return boxes, classes, scores


def nms_boxes(boxes, scores):
    x = boxes[:, 0]
    y = boxes[:, 1]
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]
    areas = w * h
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x[i], x[order[1:]])
        yy1 = np.maximum(y[i], y[order[1:]])
        xx2 = np.minimum(x[i] + w[i], x[order[1:]] + w[order[1:]])
        yy2 = np.minimum(y[i] + h[i], y[order[1:]] + h[order[1:]])
        w1 = np.maximum(0.0, xx2 - xx1 + 0.00001)
        h1 = np.maximum(0.0, yy2 - yy1 + 0.00001)
        inter = w1 * h1
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= NMS_THRESH)[0]
        order = order[inds + 1]
    return np.array(keep)


def post_process(input_data):
    boxes, scores, classes_conf = [], [], []
    defualt_branch = 3
    pair_per_branch = len(input_data) // defualt_branch
    for i in range(defualt_branch):
        boxes.append(box_process(input_data[pair_per_branch * i]))
        classes_conf.append(input_data[pair_per_branch * i + 1])
        scores.append(np.ones_like(input_data[pair_per_branch * i + 1][:, :1, :, :], dtype=np.float32))

    def sp_flatten(_in):
        ch = _in.shape[1]
        _in = _in.transpose(0, 2, 3, 1)
        return _in.reshape(-1, ch)

    boxes = [sp_flatten(_v) for _v in boxes]
    classes_conf = [sp_flatten(_v) for _v in classes_conf]
    scores = [sp_flatten(_v) for _v in scores]
    boxes = np.concatenate(boxes)
    classes_conf = np.concatenate(classes_conf)
    scores = np.concatenate(scores)

    boxes, classes, scores = filter_boxes(boxes, scores, classes_conf)

    nboxes, nclasses, nscores = [], [], []
    for c in set(classes):
        inds = np.where(classes == c)
        b = boxes[inds]
        c = classes[inds]
        s = scores[inds]
        keep = nms_boxes(b, s)
        if len(keep) != 0:
            nboxes.append(b[keep])
            nclasses.append(c[keep])
            nscores.append(s[keep])

    if not nclasses and not nscores:
        return None, None, None
    return np.concatenate(nboxes), np.concatenate(nclasses), np.concatenate(nscores)


# ---------------- 预处理（照官方 coco_utils.letter_box） ----------------
def letter_box(im, new_shape, pad_color=(0, 0, 0)):
    shape = im.shape[:2]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw /= 2
    dh /= 2
    if shape[::-1] != new_unpad:
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=pad_color)
    return im, r, (dw, dh)


def get_real_box(box, dw, dh, ratio, origin_shape):
    bbox = box.copy()
    bbox[:, 0] = np.clip((bbox[:, 0] - dw) / ratio, 0, origin_shape[1])
    bbox[:, 1] = np.clip((bbox[:, 1] - dh) / ratio, 0, origin_shape[0])
    bbox[:, 2] = np.clip((bbox[:, 2] - dw) / ratio, 0, origin_shape[1])
    bbox[:, 3] = np.clip((bbox[:, 3] - dh) / ratio, 0, origin_shape[0])
    return bbox


def draw(image, boxes, scores, classes):
    for box, score, cl in zip(boxes, scores, classes):
        top, left, right, bottom = [int(_b) for _b in box]
        cv2.rectangle(image, (top, left), (right, bottom), (255, 0, 0), 2)
        cv2.putText(image, '{0} {1:.2f}'.format(CLASSES[int(cl)], score),
                    (top, left - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)


# ---------------- 推理容器（与 video_infer.py 同源） ----------------
class Detector:
    def __init__(self, model_path):
        self.rknn = RKNNLite()
        if self.rknn.load_rknn(model_path) != 0:
            print("ERROR: load_rknn failed:", model_path)
            sys.exit(1)
        if self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0) != 0:
            print("ERROR: init_runtime failed")
            sys.exit(1)
        print("Detector ready:", model_path)

    def detect(self, img_bgr):
        h, w = img_bgr.shape[:2]
        lb, ratio, (dw, dh) = letter_box(img_bgr, IMG_SIZE, pad_color=(0, 0, 0))
        rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB)
        outputs = self.rknn.inference(inputs=[np.expand_dims(rgb, 0)])
        boxes, classes, scores = post_process(outputs)
        if boxes is None:
            return None, None, None
        real = get_real_box(boxes, dw, dh, ratio, (h, w))
        return real, classes, scores

    def release(self):
        self.rknn.release()


# ---------------- UVC 输出（Radxa 官方：VIDIOC_S_FMT + write） ----------------
def uvc_set_format(fd, w, h, fourcc):
    # struct v4l2_format { __u32 type; struct v4l2_pix_format pix; }
    # v4l2_pix_format: width,height,pixelformat,field,bytesperline,sizeimage,
    #                  colorspace,priv,flags,ycbcr_enc,quantization,xfer_func  (12 x u32)
    fmt = struct.pack('13I', V4L2_BUF_TYPE_VIDEO_OUTPUT, w, h, fourcc,
                      0, 0, 0, 0, 0, 0, 0, 0, 0) + b'\x00' * 152  # 补齐到 sizeof(v4l2_format)=204
    ret = fcntl.ioctl(fd, VIDIOC_S_FMT, fmt)
    type_, width, height, pf = struct.unpack('IIII', ret[:16])
    print("UVC fmt set: %dx%d fourcc=%08x" % (width, height, pf))
    return width == w and height == h and pf == fourcc


def main():
    ap = argparse.ArgumentParser(description="uvc_stream - USB UVC 实时推理推流")
    ap.add_argument("--no-infer", action="store_true", help="原始画面直推（不带框）")
    args = ap.parse_args()

    det = None
    if not args.no_infer:
        det = Detector(MODEL)

    cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("ERROR: cannot open", CAM)
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

    # 预热：丢前 WARMUP 帧等 AE/AWB 收敛
    for _ in range(WARMUP):
        ok, f = cap.read()
        if ok:
            frame = f

    # 打开 UVC 输出节点（非阻塞，host 未开流时 write 返回 EAGAIN 不卡死）
    try:
        fd = os.open(UVC_DEV, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        print("ERROR: cannot open", UVC_DEV, e)
        sys.exit(1)
    uvc_set_format(fd, WIDTH, HEIGHT, UYVY_FOURCC)

    frame_size = WIDTH * HEIGHT * 2  # YUYV
    print("streaming: %s -> %s (%dx%d YUYV @%dfps, infer=%s)"
          % (CAM, UVC_DEV, WIDTH, HEIGHT, FPS, "on" if det else "off"))

    n = 0
    written = 0
    t_start = time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            if FLIP_180:
                frame = cv2.flip(frame, -1)

            out = frame
            if det is not None:
                t0 = time.time()
                boxes, classes, scores = det.detect(frame)
                infer_ms = (time.time() - t0) * 1000
                out = frame.copy()
                if boxes is not None:
                    draw(out, boxes, scores, classes)
            else:
                infer_ms = 0.0

            # BGR -> YUYV（cv2 直接支持，布局与 UVC YUYV 一致）
            yuyv = cv2.cvtColor(out, cv2.COLOR_BGR2YUV_YUY2)
            buf = yuyv.tobytes()
            if len(buf) != frame_size:
                print("WARN: frame size mismatch %d != %d" % (len(buf), frame_size))
                continue
            try:
                w = os.write(fd, buf)
                written += 1
            except (BlockingIOError, OSError) as e:
                # host（平板）未打开流时的正常现象，跳过该帧
                pass

            n += 1
            el = time.time() - t_start
            fps = n / el
            det_info = ""
            if det is not None and boxes is not None and len(boxes) > 0:
                det_info = " | det=%d" % len(boxes)
            print("frame %d | infer %.0fms | %.1f fps | uvc_written %d%s"
                  % (n, infer_ms, fps, written, det_info), end="\r")
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        cap.release()
        if det is not None:
            det.release()
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == "__main__":
    main()
