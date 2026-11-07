#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module: video_infer - 视频实时推理模块（MIPI 摄像头 → NPU 推理 → 带框产物）
============================================================
依据（官方/网络搜索背书）：
  1. 香橙派官方手册 3.34：OpenCV 打开 MIPI 摄像头（rkisp_mainpath /dev/video11），
     官方自带 v4l2_opencv_demo 即用 OpenCV 实时预览摄像头
  2. 官方 rknn_model_zoo yolov8 python demo（yolov8.py）：
     预处理 letter_box(pad=0)+BGR2RGB，后处理 dfl/box_process/filter/nms 全照官方
  3. 网络搜索 10 号 #2（香橙派 RK3588 yolov5s 教程）：
     "把抓一帧改成循环连续取流"+ FPS 计时
  4. 摄像头参数（FOURCC=UYVY / 640x640 / FLIP_180）与 capture.py 保持一致

用法：
  python3 video_infer.py --mode check              # 自检：bus.jpg 验证推理+后处理链路
  python3 video_infer.py --mode live               # 实时：摄像头循环推理，产物不落盘（仅打印检测信息）
  python3 video_infer.py --mode image --img xxx.jpg  # 单图推理，产物不落盘
"""
import cv2
import os
import sys
import time
import argparse
import numpy as np
from rknnlite.api import RKNNLite
from types import SimpleNamespace
from tracker.byte_tracker import BYTETracker
from alert_mqtt import AlertPublisher

# ---------------- 可调参数 ----------------
MODEL = "/home/orangepi/trash_detect/model/best_opt.rknn"
CAM = "/dev/video11"            # rkisp_mainpath（官方手册 3.34）
FOURCC = "UYVY"                 # 必须显式指定，否则 OpenCV 误按 YUYV 解析出纯绿
WIDTH = 640
HEIGHT = 640
WARMUP = 20                     # 丢弃前 N 帧，等 3A 的 AE/AWB 收敛
FLIP_180 = True                 # 摄像头安装方向（capture.py 同款）
OBJ_THRESH = 0.25
NMS_THRESH = 0.45
IMG_SIZE = (640, 640)           # (width, height)
OUT_DIR = "/home/orangepi/trash_detect/output"

# ByteTrack 参数：ultralytics 官方 BYTETrackerArgs 默认值（官方 track 文档/modes/track）
TRACK_ARGS = SimpleNamespace(
    track_high_thresh=0.5, track_low_thresh=0.1,
    new_track_thresh=0.6, track_buffer=30, match_thresh=0.8,
)
# -----------------------------------------

# 类别名：本模型为单类河道垃圾（litter）
# 说明：后处理已按模型输出自动识别类别数，这里只影响画面标签文字；
#       以后换多类模型时把元组按顺序改成对应类别名即可（不会影响推理）
CLASSES = ("塑料瓶", "塑料杯", "泡沫", "塑料袋", "纸盒", "易拉罐", "树叶")


# ---------------- 后处理（照官方 yolov8.py，dfl 改 numpy 免 torch） ----------------
def dfl(position):
    n, c, h, w = position.shape
    p_num = 4
    mc = c // p_num
    x = position.reshape(n, p_num, mc, h, w)
    x = x - x.max(axis=2, keepdims=True)          # softmax 数值稳定
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


# ---------------- 画框（照官方 draw） ----------------
def draw(image, boxes, scores, classes, ids=None):
    for k, (box, score, cl) in enumerate(zip(boxes, scores, classes)):
        top, left, right, bottom = [int(_b) for _b in box]
        idx = int(cl)
        label = CLASSES[idx] if idx < len(CLASSES) else "class%d" % idx  # 类别数超表时显示 classN，不崩
        if ids is not None:
            label = "%s#%d" % (label, int(ids[k]))  # ByteTrack 轨迹 ID
        cv2.rectangle(image, (top, left), (right, bottom), (255, 0, 0), 2)
        cv2.putText(image, '{0} {1:.2f}'.format(label, score),
                    (top, left - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)




def track_dets(tracker, frame, boxes, classes, scores):
    """检测结果 (boxes,classes,scores) → ByteTrack 跟踪
    返回 [N,8]：x1,y1,x2,y2, track_id, score, cls, idx；无目标返回 None
    """
    if boxes is None:
        return None
    conf = np.asarray(scores, dtype=np.float32)
    cls = np.asarray(classes, dtype=np.int32)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    xywh = np.stack([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1], axis=1).astype(np.float32)
    res = SimpleNamespace(conf=conf, xywh=xywh, cls=cls)  # 官方 Results 同款接口
    out = tracker.update(res, frame)
    if out is None or len(out) == 0:
        return None
    return out

# ---------------- 推理容器 ----------------
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
        """img_bgr: 任意尺寸 BGR 图 → (boxes, classes, scores) 原图坐标"""
        h, w = img_bgr.shape[:2]
        lb, ratio, (dw, dh) = letter_box(img_bgr, IMG_SIZE, pad_color=(0, 0, 0))
        rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB)
        # 官方 rknn 平台输入：RGB uint8 HWC；本模型为 NCHW 布局需 4 维，补 batch 维
        outputs = self.rknn.inference(inputs=[np.expand_dims(rgb, 0)])
        boxes, classes, scores = post_process(outputs)
        if boxes is None:
            return None, None, None
        real = get_real_box(boxes, dw, dh, ratio, (h, w))
        return real, classes, scores

    def release(self):
        self.rknn.release()


# ---------------- 各模式 ----------------
def mode_check(det):
    """自检：跑官方测试图 bus.jpg，验证 推理+后处理 链路"""
    img = cv2.imread("/home/orangepi/trash_detect/images/bus.jpg")
    if img is None:
        print("ERROR: bus.jpg not found")
        sys.exit(1)
    boxes, classes, scores = det.detect(img)
    if boxes is None:
        print("FAIL: no detection on bus.jpg")
        sys.exit(1)
    print("DETECTIONS:")
    for b, s, c in zip(boxes, scores, classes):
        print("  %s @ (%d %d %d %d) %.3f" % (CLASSES[int(c)], int(b[0]), int(b[1]), int(b[2]), int(b[3]), s))
    print("CHECK OK (产物不落盘)")


def mode_image(det, tracker, img_path):
    img = cv2.imread(img_path)
    if img is None:
        print("ERROR: cannot read", img_path)
        sys.exit(1)
    boxes, classes, scores = det.detect(img)
    tracks = track_dets(tracker, img, boxes, classes, scores)
    if tracks is None or len(tracks) == 0:
        print("detections done, 0 objects (产物不落盘)")
        return
    print("DETECTIONS:")
    for t in tracks:
        x1, y1, x2, y2, tid, score, cls = t[:7]
        print("  [%d] %s @ (%d %d %d %d) %.3f" % (int(tid), CLASSES[int(cls)],
              int(x1), int(y1), int(x2), int(y2), score))
    print("detections done, %d objects (产物不落盘)" % len(tracks))


def mode_live(det, tracker, archive):
    """实时：摄像头循环取流 → 推理 → ByteTrack 跟踪 → 检测信息打印（产物不落盘）"""
    if archive:
        print("提示：产物不落盘，--archive 已停用（不再按秒存档）")

    cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("ERROR: cannot open", CAM)
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

    # 预热：丢弃前 WARMUP 帧，等 AE/AWB 收敛（capture.py 同款）
    frame = None
    for _ in range(WARMUP):
        ok, f = cap.read()
        if ok:
            frame = f

    print("live running: %s (ctrl-c to stop)" % CAM)
    n = 0
    t_start = time.time()
    last_alert = 0
    detecting = False       # 当前是否处于告警状态（active 中）
    alert = AlertPublisher()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("read failed, retrying...")
                time.sleep(0.05)
                continue
            if FLIP_180:
                frame = cv2.flip(frame, -1)

            t0 = time.time()
            boxes, classes, scores = det.detect(frame)
            infer_ms = (time.time() - t0) * 1000

            img_p = frame.copy()
            tracks = track_dets(tracker, frame, boxes, classes, scores)
            now = time.time()
            if tracks is not None and len(tracks) > 0:
                draw(img_p, tracks[:, :4], tracks[:, 5], tracks[:, 6], ids=tracks[:, 4])
                # 去抖：首次出现立即发，持续存在时每 3 秒一条 active
                if (not detecting) or (now - last_alert > 3):
                    alert.publish(tracks, source="photo")
                    last_alert = now
                detecting = True
            else:
                # 无目标：取消警报（不再发送任何消息），下次有目标时重新告警
                detecting = False

            n += 1
            el = time.time() - t_start
            fps = n / el

            det_info = ""
            if tracks is not None and len(tracks) > 0:
                det_info = " | trk=%d" % len(tracks)
            print("frame %d | infer %.0fms | %.1f fps%s" % (n, infer_ms, fps, det_info), end="\r")
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        cap.release()
        alert.stop()
        print("live stopped (产物不落盘)")


def main():
    ap = argparse.ArgumentParser(description="video_infer - 视频实时推理模块")
    ap.add_argument("--mode", choices=["check", "live", "image"], default="check")
    ap.add_argument("--img", default=None, help="image mode: 输入图片路径")
    ap.add_argument("--archive", action="store_true", help="已停用：产物不落盘")
    args = ap.parse_args()

    det = Detector(MODEL)
    tracker = BYTETracker(TRACK_ARGS, frame_rate=30)
    if args.mode == "check":
        mode_check(det)
    elif args.mode == "image":
        if not args.img:
            print("--mode image 需要 --img 参数")
            sys.exit(1)
        mode_image(det, tracker, args.img)
    elif args.mode == "live":
        mode_live(det, tracker, args.archive)
    det.release()


if __name__ == "__main__":
    main()
