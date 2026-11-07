# 项目日志 2026-10-23

## 项目：河道漂浮垃圾检测 v1

## 今日工作

| 日期 | 阶段 | 任务 | 完成内容 | 结果/证据 | 问题/阻塞 | 下一步 |
|------|------|------|----------|-----------|-----------|--------|
| 2026-10-23 | 网络修复 | 板端 apt 断网排障 | 板端 apt 报 DNS 解析失败、ping 223.5.5.5 全丢；实查：电脑 WLAN=172.20.172.1（上网源）、以太网口=192.168.137.1（板子网关）、板子 eth0=192.168.137.159，ICS 转发丢失 + 板端 resolv.conf 为空 → 电脑共享 WLAN→以太网 + 板端 `nmcli connection modify "Wired connection 1" ipv4.dns "223.5.5.5 114.114.114.114" ipv4.ignore-auto-dns yes` + `nmcli connection up` | 板端 `curl -I http://repo.huaweicloud.com` 返回 301，apt 恢复 | 用户曾提议开代理，已说明板子包出不去需先修转发；厘清 192.168.137.1（电脑口）≠ 192.168.137.159（板子） | — |
| 2026-10-23 | 多媒体组件 | 板端装 MPP/硬编插件 | 按用户自搜文档（`网络搜索\12-精选文章-香橙派5-GStreamer与MPP插件.md`）加 PPA `liujianfeng1994/rockchip-multimedia`，apt 装 `gstreamer1.0-rockchip1 librockchip-mpp1 librockchip-mpp-dev librockchip-vpu0 librga2 libv4l-rkmpp rockchip-multimedia-config`（含 ffmpeg 6.0.1-1+git） | `gst-inspect-1.0 \| grep mpp` 确认 mpph264enc/mpph265enc/mppvideodec/mppjpegenc 全套在；ffmpeg 有 h264_rkmpp 硬编 | 首次 apt 直装找不到包（官方源无）→ PPA 解决 | — |
| 2026-10-23 | WiFi 部署 | AP6275P 模块接入 + 热点 | 官方手册 3.6.4：`/boot/orangepiEnv.txt` 加 `overlays=wifi-ap6275p`（勿同选 ssd-sata）→ 重启 wlan0 出现；`sudo create_ap -n wlan0 orangepi orangepi` 开纯热点（-m nat wlan0 eth0 报错 `'orangepi' is not an interface`，闭环场景无需 NAT） | ap0=192.168.12.1/24；平板小米 Xiaomi-14 DHCP=192.168.12.242（MAC 36:d0:70:0a:e4:9f）；`ip link` 见 wlan0+ap0 | — | — |
| 2026-10-23 | RTSP 服务器 | MediaMTX v1.21.2 部署 | 电脑走本地代理下载官方 release（`mediamtx_v1.21.2_linux_arm64.tar.gz`，30.5MB，D:\Downloads）→ pscp 传板 → /opt/mediamtx 解压；systemd 服务（ExecStart=/opt/mediamtx/mediamtx mediamtx.yml，Restart=always，enabled+active）；yml 开 api:true | `ss -tlnp \| grep 8554` LISTEN；`curl :9997/v3/paths/list` 可查流状态 | 板端 wget GitHub 太慢（~7MB 弃）→ 电脑代理下+pscp（~10MB/s） | — |
| 2026-10-23 | 推流链路 | ffmpeg RTSP 推流失败排查 | 逐步对照：裸读 /dev/video11 17fps 正常；lavfi 测试源→libx264→RTSP 成功（证明 MediaMTX 无问题）；但 video11→libx264/h264_rkmpp→任何输出都只出 2-3 帧、drop 暴涨、`time=-577014:32` 巨大负时间戳 → **根因：板端定制 ffmpeg 读 v4l2 帧时间戳异常**；`-timestamps abs`/`-use_wallclock_as_timestamps`/`setpts` 均无效 | 对比实验链完整（`-f null -` 17fps vs 编码 3 帧） | ffmpeg v4l2→编码组合不可用（定制版 bug） | 换 gst 路线 |
| 2026-10-23 | 推流打通 | gst 硬编 RTMP → MediaMTX → RTSP | gst `v4l2src device=/dev/video11 ! video/x-raw,format=UYVY,width=640,height=640 ! mpph264enc ! h264parse ! flvmux ! rtmp2sink location=rtmp://127.0.0.1:1935/live/myStream`（rtmp2sink location 必须带 playpath，默认 `rtmp://localhost/live/myStream`）→ MediaMTX RTMP:1935 收 → RTSP:8554 发 | API 确认 `live/myStream ready=true online=true`（H264 640x640），25s 稳定无断连；**平板 VLC 填 `rtsp://192.168.12.1:8554/live/myStream` 能看** | — | 延迟/推理框优化 |
| 2026-10-23 | 开机自启 | 三服务固化 | `create_ap.service`（-n wlan0 纯热点）+ `mediamtx.service` + `rtsp-stream.service`（ExecStart=/home/orangepi/trash_detect/scripts/rtsp_stream.sh，gst 硬编）全部 enabled+active，Restart=always | `systemctl is-active` 三服务均 active；gst-stream.service（旧 UDP 推流）已停用保留 | — | 断电重启全流程验证 |

## 里程碑

| 日期 | 里程碑 | 状态 |
|------|--------|------|
| 2026-10-23 | 板端 apt 网络修复（ICS + DNS 223.5.5.5） | ✅ |
| 2026-10-23 | MPP 硬编插件全家桶装齐（gstreamer1.0-rockchip1 + ffmpeg 6.0.1） | ✅ |
| 2026-10-23 | **AP6275P WiFi 接入 + 热点 ap0=192.168.12.1（官方 overlay wifi-ap6275p）** | ✅ |
| 2026-10-23 | **MediaMTX RTSP 服务器 + gst 硬编 RTMP 推流打通，平板能看** | ✅ |
| 2026-10-23 | 热点+推流三服务开机自启 | ✅ |
| — | 平板实时显示**带推理框**的推理视频 | 进行中（明天） |
| — | 推流**延迟优化**（当前延迟高） | 待做（明天） |
| — | 断电重启 → 开机即用完整验证 | 待做 |
| — | 河道正式数据集训练（firc 2436 张 vs lonlonago 12694 张，待与用户拍板） | 未开始 |

## 遗留问题（明天修）

1. **延迟高**：平板画面延迟明显。待查方向：gst 推流侧 `mpph264enc` 关键帧间隔/码率、MediaMTX 缓存、VLC 端缓存（`rtsp_transport tcp` 已用；可试降码率、调 GOP、关 VLC 缓存）。
2. **画面没有推理框**：当前推流链路是纯摄像头视频（gst v4l2src → mpph264enc），未叠加 RKNN 推理结果。目标是把推理框叠加进推流画面（参考 mjpeg_stream.py/video_infer.py 的带框输出，与推流管线合并，全程内存不落盘）。

## 续接要点

1. 平板拉流地址：`rtsp://192.168.12.1:8554/live/myStream`（SSID orangepi / 密码 orangepi）
2. 板端推流脚本：`/home/orangepi/trash_detect/scripts/rtsp_stream.sh`（gst 硬编，改这里即改推流参数）
3. MediaMTX 配置：`/opt/mediamtx/mediamtx.yml`（rtsp :8554 / rtmp :1935 / api :9997）
4. ffmpeg 定制版 v4l2 时间戳异常（编码路线不可用）——后续如再走 ffmpeg 需查证补丁/换版
5. 操作纪律延续：每一步先给官方文档/网络搜索依据再执行；更新文件树前先实查、写全；临时文件即删；下载统一 D:\Downloads
