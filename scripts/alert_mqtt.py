#!/usr/bin/env python3
# MQTT 告警模块：检测结果 -> 板端本地 broker 告警发布（独立可运行，供 video_infer / rtsp_infer_stream 集成）
# 依据：Eclipse Paho MQTT Python 客户端（pypi.org/project/paho-mqtt）+ 板端本机 mosquitto 2.0.11（127.0.0.1:1883）
# 行为：有目标 -> 发布 event=trash_detected(active)；无目标 -> 不发送任何消息（警报自动取消）
#       totals 为各类别累计数量（按跟踪 ID 去重，同一目标只计一次）；累计存进程内存，板子重启即清零
import json
import time
import paho.mqtt.client as mqtt

BROKER = "127.0.0.1"
PORT = 1883
TOPIC = "trash_detect/alert"
CLASSES = ["塑料瓶", "塑料杯", "泡沫", "塑料袋", "纸盒", "易拉罐", "树叶"]


class AlertPublisher:
    """告警发布器：把检测结果组装成 JSON 告警发到本地 broker"""

    def __init__(self, broker=BROKER, port=PORT):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.connect(broker, port, 60)
        self.client.loop_start()
        self.totals = {}        # 各类别累计数量（进程内存，重启清零）
        self._seen = set()      # 已计数的 (类别, track_id)，同一目标只累计一次

    def publish(self, detections, source="rtsp", image_path=""):
        """detections 非空（有目标）时调用：累计各类别数量并发布 active 告警
        detections: [[x1,y1,x2,y2, track_id, score, cls, idx], ...]（ByteTrack 输出行序）"""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        items = []
        counts = {}
        for d in detections:
            x1, y1, x2, y2, tid, score, cls, _idx = d
            name = CLASSES[int(cls)] if int(cls) < len(CLASSES) else str(cls)
            counts[name] = counts.get(name, 0) + 1
            key = (name, int(tid))
            if key not in self._seen:          # 同 ID 目标只累计一次
                self._seen.add(key)
                self.totals[name] = self.totals.get(name, 0) + 1
            items.append({
                "class": name,
                "conf": round(float(score), 3),
                "track_id": int(tid),
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
            })
        payload = {
            "event": "trash_detected",
            "status": "active",
            "time": now,
            "source": source,
            "counts": counts,
            "totals": dict(self.totals),
            "items": items,
            "image": image_path,
        }
        self.client.publish(TOPIC, json.dumps(payload, ensure_ascii=False), qos=0)
        return payload

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


if __name__ == "__main__":
    # 独立运行自测：发两条 active（同 ID 只累计一次），验证累计数量
    a = AlertPublisher()
    p1 = a.publish([[270, 220, 370, 420, 1, 0.90, 0, 0]], source="selftest")
    print("published:", json.dumps(p1, ensure_ascii=False))
    p2 = a.publish([[270, 220, 370, 420, 1, 0.90, 0, 0],
                    [100, 100, 200, 260, 2, 0.80, 4, 0]], source="selftest")
    print("published:", json.dumps(p2, ensure_ascii=False))
    a.stop()
