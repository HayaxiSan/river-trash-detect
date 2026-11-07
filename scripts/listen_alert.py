#!/usr/bin/env python3
# 订阅监听 trash_detect/alert，打印收到的告警（验证发布-订阅链路）
import time
import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, reason_code, properties):
    print("sub connected rc=", reason_code)
    client.subscribe("trash_detect/alert")

def on_message(client, userdata, msg):
    print("ALERT RECV:", msg.payload.decode())

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_connect = on_connect
c.on_message = on_message
c.connect("127.0.0.1", 1883, 60)
c.loop_start()
time.sleep(12)
c.loop_stop()
print("listen done")
