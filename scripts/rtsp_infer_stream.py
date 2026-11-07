#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module: rtsp_infer_stream - 推理画框 + MPP 硬编 + RTSP 推流（复用闭环全环节）
============================================================================
依据（官方/网络搜索背书）：
  1. 推理/后处理/画框：完全复用 video_infer.py（照官方 rknn_model_zoo yolov8.py，
     letter_box + dfl/box_process/filter/nms + draw；RKNNLite 加载 best_opt.rknn）
  2. 摄像头参数：与 capture.py / video_infer.py 一致
     （rkisp_mainpath /dev/video11，FOURCC=UYVY，640x640，WARMUP=20，FLIP_180=True）
  3. 推流管线：与已跑通的 rtsp_stream.sh 一致
     （mpph264enc ! h264parse ! flvmux ! rtmp2sink，RTMP 推入 MediaMTX :1935）
  4. Python 注入帧：GStreamer 官方 appsrc 元素（gst-plugins-base 官方文档），
     外部数据 → 管线；videoconvert 官方格式转换 BGR→I420

用法：
  python3 rtsp_infer_stream.py            # 前台运行（推理+画框+推流）
  python3 rtsp_infer_stream.py --fps 30   # 指定编码帧率（appsrc caps）
"""
import cv2
import os
import sys
import time
import argparse
import numpy as np
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from rknnlite.api import RKNNLite
from types import SimpleNamespace
from tracker.byte_tracker import BYTETracker
from alert_mqtt import AlertPublisher

# ---------------- 可调参数（与闭环模块保持一致） ----------------
MODEL = "/home/orangepi/trash_detect/model/best_opt.rknn"
CAM = "/dev/video11"            # rkisp_mainpath（官方手册 3.34）
FOURCC = "UYVY"                 # 必须显式指定，否则 OpenCV 误按 YUYV 解析出纯绿
WIDTH = 640
HEIGHT = 640
WARMUP = 20                     # 丢弃前 N 帧，等 3A 的 AE/AWB 收敛
FLIP_180 = True                 # 摄像头支架安装方向（capture.py 同款）
OBJ_THRESH = 0.25
NMS_THRESH = 0.45
IMG_SIZE = (640, 640)           # (width, height)
RTMP_URL = "rtmp://127.0.0.1:1935/live/myStream"

# ByteTrack 参数：ultralytics 官方 BYTETrackerArgs 默认值（官方 track 文档/modes/track）
TRACK_ARGS = SimpleNamespace(
    track_high_thresh=0.5, track_low_thresh=0.1,
    new_track_thresh=0.6, track_buffer=30, match_thresh=0.8,
)
GOP = 16                        # 关键帧间隔（帧）；实际 fps≈16.6，16 帧≈1 秒一个 I 帧（13号文档B#3：1秒I帧）
# --------------------------------------------------------------

# 类别名：本模型为单类河道垃圾（litter）
# 说明：后处理已按模型输出自动识别类别数，这里只影响画面标签文字；
#       以后换多类模型时把元组按顺序改成对应类别名即可（不会影响推理）
CLASSES = ("塑料瓶", "塑料杯", "泡沫", "塑料袋", "纸盒", "易拉罐", "树叶")


# ---------------- 后处理（照官方 yolov8.py，与 video_infer.py 一致） ----------------
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


# ---------------- 预处理（照官方 coco_utils.letter_box，与 video_infer.py 一致） ----------------
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


# ---------------- 推理容器（与 video_infer.py 一致） ----------------
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

# ---------------- GStreamer 推流（官方 appsrc 注入） ----------------
def build_pipeline(fps):
    """appsrc(BGR) → videoconvert(I420) → mpph264enc → h264parse → flvmux → rtmp2sink"""
    Gst.init(None)
    pipeline = Gst.Pipeline.new("rtsp-infer-stream")

    appsrc = Gst.ElementFactory.make("appsrc", "src")
    caps = Gst.Caps.from_string(
        "video/x-raw,format=BGR,width=%d,height=%d,framerate=%d/1" % (WIDTH, HEIGHT, fps))
    appsrc.set_property("caps", caps)
    appsrc.set_property("is-live", True)
    appsrc.set_property("format", Gst.Format.TIME)
    appsrc.set_property("do-timestamp", True)  # live 源官方做法：按 running_time 自动打时间戳（GstBaseSrc 官方文档 "mostly useful for live sources"）

    videoconvert = Gst.ElementFactory.make("videoconvert", "vc")
    capsfilter = Gst.ElementFactory.make("capsfilter", "cf")
    capsfilter.set_property("caps",
        Gst.Caps.from_string("video/x-raw,format=I420,width=%d,height=%d" % (WIDTH, HEIGHT)))

    enc = Gst.ElementFactory.make("mpph264enc", "enc")
    enc.set_property("gop", GOP)          # 关键帧间隔（官方属性，gst-inspect 确认）
    enc.set_property("max-pending", 4)    # 编码器输入队列 16→4（官方属性；16 帧≈1s 恒定延迟，13号文档C#1最小缓存原则）
    enc.set_property("rc-mode", 2)        # fixqp 固定量化：去码率平滑缓冲（官方枚举 (2):fixqp；13号文档C#1）

    h264parse = Gst.ElementFactory.make("h264parse", "hp")
    h264parse.set_property("config-interval", 1)  # 周期性带 SPS/PPS（官方属性；13号文档B#3）
    flvmux = Gst.ElementFactory.make("flvmux", "mux")
    rtmp = Gst.ElementFactory.make("rtmp2sink", "out")
    rtmp.set_property("location", RTMP_URL)

    for e in (appsrc, videoconvert, capsfilter, enc, h264parse, flvmux, rtmp):
        if e is None:
            print("ERROR: element create failed")
            sys.exit(1)
        pipeline.add(e)
    appsrc.link(videoconvert)
    videoconvert.link(capsfilter)
    capsfilter.link(enc)
    enc.link(h264parse)
    h264parse.link(flvmux)
    flvmux.link(rtmp)

    ret = pipeline.set_state(Gst.State.PLAYING)
    # ASYNC 也是正常状态（live 源异步过渡），仅 FAILURE 才报错
    if ret == Gst.StateChangeReturn.FAILURE:
        print("ERROR: pipeline set PLAYING failed:", ret)
        sys.exit(1)
    return pipeline, appsrc


def main():
    ap = argparse.ArgumentParser(description="rtsp_infer_stream - 推理画框推流")
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()
    fps = max(5, min(30, args.fps))

    det = Detector(MODEL)
    tracker = BYTETracker(TRACK_ARGS, frame_rate=30)
    alert = AlertPublisher()
    cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("ERROR: cannot open", CAM)
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    # 注：V4L2 buffersize 实测在板端会掉帧（fps 20→6），保留默认缓冲不设置

    frame = None
    for _ in range(WARMUP):
        ok, f = cap.read()
        if ok:
            frame = f

    pipeline, appsrc = build_pipeline(fps)

    # bus 轮询：捕获管线错误（如编码/推流失败）
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    print("infer+stream running: %s → %s (ctrl-c to stop)" % (CAM, RTMP_URL))
    n = 0
    t_start = time.time()
    last_alert = 0
    detecting = False       # 当前是否处于告警状态（active 中）
    try:
        while True:
            # 非阻塞检查管线消息（错误即退出）
            while True:
                msg = bus.pop()
                if msg is None:
                    break
                if msg.type == Gst.MessageType.ERROR:
                    err, dbg = msg.parse_error()
                    print("\nPIPELINE ERROR:", err.message)
                    print("debug:", dbg)
                    sys.exit(1)
            ok, frame = cap.read()
            if not ok:
                print("read failed, retrying...")
                time.sleep(0.05)
                continue
            if FLIP_180:
                frame = cv2.flip(frame, -1)   # 画面方向修正（闭环同款）

            t0 = time.time()
            boxes, classes, scores = det.detect(frame)
            infer_ms = (time.time() - t0) * 1000

            img_p = frame
            tracks = track_dets(tracker, frame, boxes, classes, scores)
            now = time.time()
            if tracks is not None and len(tracks) > 0:
                draw(img_p, tracks[:, :4], tracks[:, 5], tracks[:, 6], ids=tracks[:, 4])
                # 去抖：首次出现立即发，持续存在时每 3 秒一条 active
                if (not detecting) or (now - last_alert > 3):
                    alert.publish(tracks, source="rtsp")
                    last_alert = now
                detecting = True
            else:
                # 无目标：取消警报（不再发送任何消息），下次有目标时重新告警
                detecting = False

            n += 1
            el = time.time() - t_start
            fps_now = n / el
            # 右上角实时帧率 OSD（绿色，叠加在推流画面）
            cv2.putText(img_p, "fps %.1f" % fps_now, (WIDTH - 170, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

            # 注入 appsrc（do-timestamp=True：GStreamer 按 running_time 自动打戳，消除手动 PTS 与真实帧率不匹配）
            buf = Gst.Buffer.new_allocate(None, img_p.nbytes, None)
            buf.fill(0, img_p.tobytes())
            appsrc.emit("push-buffer", buf)

            det_info = ""
            if tracks is not None and len(tracks) > 0:
                det_info = " | trk=%d" % len(tracks)
            print("frame %d | infer %.0fms | %.1f fps%s" % (n, infer_ms, fps_now, det_info), end="\r")
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        appsrc.emit("end-of-stream")
        pipeline.set_state(Gst.State.NULL)
        cap.release()
        det.release()
        alert.stop()
        print("pipeline stopped")


if __name__ == "__main__":
    main()
