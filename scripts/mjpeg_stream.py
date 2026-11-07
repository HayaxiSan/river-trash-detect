#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module: mjpeg_stream - 推理视频 MJPEG 网络推流（平板浏览器直接看）
============================================================
依据：
  1. 香橙派官方手册 3.34：OpenCV 打开 MIPI 摄像头 /dev/video11
  2. 官方 rknn_model_zoo yolov8 python demo：预处理/后处理
  3. 网络搜索 10 号 #2：循环连续取流 + 推理；#6：流媒体服务器/浏览器观看模式

用法：
  python3 mjpeg_stream.py [--port 8080] [--no-infer]
  平板浏览器打开: http://<板子IP>:8080
"""
import cv2
import os
import sys
import time
import socket
import argparse
import threading
import numpy as np
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from rknnlite.api import RKNNLite

MODEL = "/home/orangepi/trash_detect/model/best_opt.rknn"
CAM = "/dev/video11"
FOURCC = "UYVY"
WIDTH = 640
HEIGHT = 640
WARMUP = 20
FLIP_180 = True
OBJ_THRESH = 0.25
NMS_THRESH = 0.45
IMG_SIZE = (640, 640)

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

latest_frame = None
frame_lock = threading.Lock()


# ---- 后处理（照官方 yolov8.py，dfl numpy 化） ----
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
    class_max_score = np.max(box_class_probs, axis=-1)
    classes = np.argmax(box_class_probs, axis=-1)
    _class_pos = np.where(class_max_score * box_confidences >= OBJ_THRESH)
    scores = (class_max_score * box_confidences)[_class_pos]
    return boxes[_class_pos], classes[_class_pos], scores


def nms_boxes(boxes, scores):
    x = boxes[:, 0]; y = boxes[:, 1]
    w = boxes[:, 2] - boxes[:, 0]; h = boxes[:, 3] - boxes[:, 1]
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
        b = boxes[inds]; c = classes[inds]; s = scores[inds]
        keep = nms_boxes(b, s)
        if len(keep) != 0:
            nboxes.append(b[keep]); nclasses.append(c[keep]); nscores.append(s[keep])
    if not nclasses and not nscores:
        return None, None, None
    return np.concatenate(nboxes), np.concatenate(nclasses), np.concatenate(nscores)


def letter_box(im, new_shape, pad_color=(0, 0, 0)):
    shape = im.shape[:2]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw /= 2; dh /= 2
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


class Detector:
    def __init__(self, model_path):
        self.rknn = RKNNLite()
        if self.rknn.load_rknn(model_path) != 0:
            print("ERROR: load_rknn failed"); sys.exit(1)
        if self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0) != 0:
            print("ERROR: init_runtime failed"); sys.exit(1)
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


class MjpegHandler(BaseHTTPRequestHandler):
    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        # 客户端 5 秒不读流就断开，防止单连接卡死服务器
        self.connection.settimeout(5)

    def do_GET(self):
        global latest_frame
        if self.path != '/stream':
            body = b"<html><body><h2>MJPG Stream</h2><img src='/stream'></body></html>"
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        try:
            while True:
                with frame_lock:
                    f = latest_frame
                if f is None:
                    time.sleep(0.05)
                    continue
                ok, jpg = cv2.imencode('.jpg', f, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    continue
                data = b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + \
                       str(len(jpg.tobytes())).encode() + b'\r\n\r\n' + jpg.tobytes() + b'\r\n'
                self.wfile.write(data)
                self.wfile.flush()
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError):
            pass

    def log_message(self, fmt, *args):
        pass


def capture_loop(det, no_infer):
    global latest_frame
    cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("ERROR: cannot open", CAM); sys.exit(1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    for _ in range(WARMUP):
        ok, f = cap.read()
    print("camera opened:", CAM)
    n = 0
    t0 = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.02)
            continue
        if FLIP_180:
            frame = cv2.flip(frame, -1)
        if det is not None:
            boxes, classes, scores = det.detect(frame)
            if boxes is not None:
                draw(frame, boxes, scores, classes)
        with frame_lock:
            latest_frame = frame
        n += 1
        if n % 60 == 0:
            print("capture %d | %.1f fps" % (n, n / (time.time() - t0)), end="\r")


def main():
    ap = argparse.ArgumentParser(description="mjpeg_stream - 推理视频 MJPEG 推流")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-infer", action="store_true")
    args = ap.parse_args()

    det = None
    if not args.no_infer:
        det = Detector(MODEL)

    t = threading.Thread(target=capture_loop, args=(det, args.no_infer), daemon=True)
    t.start()
    time.sleep(2)

    ip = socket.gethostbyname(socket.gethostname())
    print("MJPEG stream: http://%s:%d/stream" % (ip, args.port))
    server = ThreadingHTTPServer(('0.0.0.0', args.port), MjpegHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
