# 06 · MQTT 告警：板端配置与手机订阅

## 1. 链路总览

```text
检测到垃圾 ──► alert_mqtt.py ──► 板端 mosquitto:1883 ──► 手机/电脑订阅端（弹告警）
无垃圾     ──► 不发消息 ─────► 手机端告警自动消失
```

## 2. 板端 mosquitto 配置

### 2.1 主配置 `/etc/mosquitto/mosquitto.conf`

```text
pid_file /run/mosquitto/mosquitto.pid
persistence true
persistence_location /var/lib/mosquitto/
log_dest file /var/log/mosquitto/mosquitto.log
include_dir /etc/mosquitto/conf.d
```

### 2.2 开放外部访问 `/etc/mosquitto/conf.d/external.conf`

```text
listener 1883 0.0.0.0
allow_anonymous true
```

> 默认只监听 `127.0.0.1`，**手机连不上**就是因为缺这个文件。仓库里是 `configs/mosquitto_external.conf`，改名放到位即可。

### 2.3 生效与验证

```bash
sudo systemctl restart mosquitto
systemctl status mosquitto
ss -lntp | grep 1883        # 应显示 0.0.0.0:1883
```

## 3. 消息格式

| 项 | 值 |
|---|---|
| Topic | `trash_detect/alert` |
| QoS | 0 |
| 编码 | UTF-8 JSON（`ensure_ascii=False`，中文原样） |

检测到垃圾时：

```json
{
  "event": "trash_detected",
  "status": "active",
  "time": "2026-11-05 22:13:05",
  "source": "rtsp",
  "counts": {"塑料瓶": 1, "塑料袋": 1},
  "totals": {"塑料瓶": 3, "纸盒": 2, "塑料袋": 1},
  "items": [
    {"class": "塑料瓶", "conf": 0.903, "track_id": 12, "bbox": [270, 220, 370, 420]},
    {"class": "塑料袋", "conf": 0.781, "track_id": 13, "bbox": [410, 300, 520, 400]}
  ],
  "image": ""
}
```

| 字段 | 含义 |
|---|---|
| `counts` | **当前帧**各类别框数 |
| `totals` | **本次开机以来**各类别累计（按 `(类别, track_id)` 去重） |
| `items` | 当前帧每个框的类别/置信度/跟踪 ID/坐标 |
| `image` | 保留字段，当前为空（产物不落盘） |

**没有垃圾时不发任何消息** —— 订阅端收不到新消息，警报自然消失（不做 clear 双状态）。

累计数存在**进程内存**，板子重启即清零（符合"重启清空"要求）。

## 4. 命令行验证（板端或电脑）

```bash
# 板端自测：发两条 active（同 ID 只累计一次）
python3 scripts/alert_mqtt.py

# 板端订阅
python3 scripts/listen_alert.py

# 或直接命令行订阅（电脑上，假设板子 IP 为 192.168.12.1）
mosquitto_sub -h 192.168.12.1 -p 1883 -t trash_detect/alert -v
```

## 5. 手机订阅（IoT MQTT Panel，Android）

### 5.1 先连上板子的 WiFi

板子自建热点：**SSID `orangepi` / 密码 `orangepi`**（5GHz 信道 36）。
连上后手机应在 `192.168.12.x` 网段，网关 `192.168.12.1`。

> 手机要连的是**板子发的热点**，不是电脑热点、也不是家里路由器。

### 5.2 添加连接（Connections）

| 项 | 值 |
|---|---|
| 连接名 | `orangepi`（随意） |
| Protocol | `TCP` |
| Broker URL / Host | `192.168.12.1` |
| Port | `1883` |
| Client ID | 随意（如 `phone1`，不要与其他端重复） |
| Username / Password | **留空**（板端 `allow_anonymous true`） |
| Keep Alive | 60 |

保存后点连接，状态应变为已连接。

### 5.3 添加面板（Panels → 文本输出）

| 字段 | 填什么 |
|---|---|
| 面板类型 | **文本输出**（不要选"文本输入"，那是发送用的） |
| 面板名称 | `垃圾告警` |
| 禁用仪表板前缀主题 | 不勾 |
| **主题** | `trash_detect/alert`（**注意是 alert，不是 slert**） |
| 显示历史记录 | 勾（最大持续性 20） |
| 负载是 JSON 数据 | **勾选** |
| **订阅的 JsonPath** | `$`（显示整条 JSON） |
| 显示接收时间戳 | 可选勾选 |
| QoS | 0 |

创建后，板子检测到垃圾时面板就会刷新出一条 JSON；无垃圾时不再有消息进来（警报停止）。

### 5.4 只显示累计数量（可选）

把 JsonPath 改成 `$.totals` 只显示各类累计；改成 `$.counts` 只显示当前帧。

## 6. 常见连不上原因

| 现象 | 原因 / 解决 |
|---|---|
| 手机连不上 broker | 缺 `conf.d/external.conf`，mosquitto 只听 127.0.0.1 |
| 手机填了 `192.168.137.159` 连不上 | 那是 eth0（网线）地址；手机走 WiFi 必须用 `192.168.12.1` |
| 主题填 `trash_detect/slert` 收不到 | 拼写错，正确是 `trash_detect/alert` |
| 面板一直空白 | 没有目标经过镜头（无目标不发消息），或 JsonPath 没填 `$` |
| 收到消息但一直是旧的 | Broker 里残留 retained 消息，用 `mosquitto_pub -t trash_detect/alert -r -n` 清一次 |
