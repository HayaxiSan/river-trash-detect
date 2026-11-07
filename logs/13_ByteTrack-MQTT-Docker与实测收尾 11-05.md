# 项目日志 13：ByteTrack / MQTT / Docker 与实测收尾 11-05

## 项目：河道漂浮垃圾检测 v1

## 今日日志

| 日期 | 阶段 | 任务 | 完成内容 | 结果/证据 | 问题/阻塞 | 下一步 |
|------|------|------|----------|-----------|-----------|--------|
| 2026-11-05 | 跟踪 | #15 ByteTrack 落地 | v8.2.0 基线本地化生成 tracker 4 文件（basetrack/byte_tracker/kalman_filter/matching）推板端；离线装 scipy 1.15.3、lap 0.5.14 wheel；集成进 rtsp_infer_stream.py 与 video_infer.py | 板端离线依赖安装成功；跟踪输出行序 [x1,y1,x2,y2,track_id,score,cls,idx] | 集成后暴露 matching.py `bbox_ioa` 旧版返回二元组 bug（iou=True），容器/宿主推流遇目标即崩 `TypeError: int - tuple` | — |
| 2026-11-05 | 跟踪 | 修复 bbox_ioa bug | 对照 ultralytics main 分支 metrics.py/matching.py 官方源码重写为单值返回 | 板端 matching.py 修复版 + 备份 matching.py.bak_iou；容器/宿主推流不再崩溃 | — | — |
| 2026-11-05 | 告警 | #16 MQTT 全离线落地 | mosquitto 2.0.11 + 5 依赖 .deb（jammy arm64，避开 %3a/%3A 编码坑）dpkg 安装；paho-mqtt 2.2.0 wheel 用户级 --no-deps；scripts/alert_mqtt.py（7 类中文 JSON，TOPIC=trash_detect/alert）集成两推流管线（3 秒节流 + finally stop） | systemctl active 监听 127.0.0.1:1883；端到端订阅验证收到 `{"event":"trash_detected","counts":{"塑料瓶":1}}` | 板端无外网，全部走 WSL 代理下载 + 离线 dpkg/pip | 外部 PC 订阅需改监听 0.0.0.0（未要求未做） |
| 2026-11-05 | 容器化 | #17 Docker 容器化 rtsp | Docker CE 29.5.3 预装；镜像 trash-rtsp:v1 用 tar 排除虚拟/缓存目录 + docker import 自建；docker_build.sh / docker_verify.sh / docker_rtsp.sh 双端同步 | 容器内 NPU 推理 + H264 推流 485+ 帧无崩溃（infer 50ms / 18fps）；mediamtx `stream is available and online, 1 track (H264)` | **卡点**：容器内 NPU 报缺 renderD129 与 /proc/device-tree；单文件 -v/--mount/bind/无 privileged/挂 /proc 全失效 → 最终 `--privileged + -v /proc/device-tree:/proc/device-tree 整目录挂载` 有效 | 演示后恢复宿主 rtsp-stream active |
| 2026-11-05 | 实测 | #18 考试集图标注对比 | 13 张图（1 正样本 v12_test + 12 负样本），板端 exam_eval.py 逐图推理，真值经模型框画回原图核对 | 正样本 3/3 命中（conf 0.90/0.79/0.78）、漏检 0；负样本 6/12 误报共 11 框（0.29~0.65）；框级 Precision 3/14=21.4%、Recall 3/3=100%；告警漏报率 0%、误报率 50% | 误报全为室内杂乱/暗光低置信框 | OBJ_THRESH 0.25→0.5 复跑（消 7/11 误报）用户未拍板 |
| 2026-11-05 | 产物治理 | 用户拍板：删除 docker | 容器 trash-rtsp/trash-infer-test 删除 + 镜像 trash-rtsp:v1 rmi | 板端 `docker images`/`docker ps -a` 均为空；宿主闭环 rtsp-stream + mediamtx 双 active；output/ 日志（mjpeg/thermal/uvc .log）与产物文件保留 | — | 产物不落盘：单次产物默认不存、仅 live_latest.jpg 覆盖写（待上电执行脚本调整） |
| 2026-11-05 | 产物治理 | 用户拍板：只保留日志落盘 | output/ 清空全部非日志文件（13 个产物：bus_out/cap_*_out/check_bus/track_v12×2/v12_check×2/v12_gt_check×2/live_latest/exam_results.json） | `ls output/` 仅剩 mjpeg.log、thermal_monitor.log、uvc_stream.log；exam_results.json 本地已有备份 D:\AIEnv\ | — | 单次产物不落盘脚本化（待拍板） |
| 2026-11-05 | 产物治理 | 脚本落盘代码删除 | video_infer.py 删 4 处 imwrite（check/单图/live_latest 覆盖/每秒归档）+ --save/--archive 参数停用；capture.py/live_check.py 删存图改打印；infer.sh 改"推理后即时删 out.png"；删除纯产物脚本 exam_draw.py/exam_eval.py（本地有备份） | 板端 py_compile/bash -n 全过；全量 grep 无 imwrite/live_latest 残留；--mode check 自检检出 bus 塑料袋 0.569（CHECK OK）；跑后 output/ 仍仅 3 个 log；rtsp-stream/mediamtx 双 active | — | — |
| 2026-11-05 | MQTT 告警改造 | 有垃圾→发 active 告警（新增 status + totals 各类别累计，按 track_id 去重）；无垃圾→不发送（警报取消）；累计存进程内存、板子重启清零 | alert_mqtt.py 重构（totals/_seen 去重 + 去掉 clear 消息）；rtsp_infer_stream.py / video_infer.py 集成点改"首次出现立即发、持续 3 秒一条、无目标复位" | 板端 py_compile 全过；自测 2 条 active：塑料瓶 ID1 出现 2 次只累计 1 + 纸盒 ID2 累计 1（去重正确）；listen_alert 端到端收到 status/totals；rtsp-stream 重启后 journalctl 推理/硬编正常、无目标 20 秒静默 | — | — |
| 2026-11-05 | 收尾 | 全部任务归档 | #1-18 全 completed | 板端闭环"拍照→推理→产物"宿主模式运行中；磁盘 29G 用 8.0G 剩 20G | — | 阈值调优拍板、产物不落盘脚本化 |

## 里程碑

| 日期 | 里程碑 | 状态 |
|------|--------|------|
| 2026-11-05 | #15 ByteTrack 跟踪器落地 | ✅ |
| 2026-11-05 | #16 MQTT 告警全离线闭环 | ✅ |
| 2026-11-05 | #17 Docker 容器化 rtsp 验证 | ✅（验证后按用户决定删除） |
| 2026-11-05 | #18 实测数据交付 | ✅ |
| 2026-11-05 | 板端闭环定案 = 宿主服务模式（rtsp-stream + mediamtx） | ✅ |
| 待办 | OBJ_THRESH 0.25→0.5 阈值调优复跑 | 待拍板 |
| 2026-11-05 | 产物治理执行 | 历史产物清理（只留日志） | ✅ |

## 关键结论备忘

- **Docker 容器化可行但不再采用**：容器内 NPU 挂载卡点已攻克（`--privileged` + device-tree 整目录挂载 + renderD129 直通），但用户拍板板端闭环以宿主服务为准，镜像/容器已删除释放空间。
- **ByteTrack 集成隐患已根除**：`bbox_ioa` 旧版语义（返回二元组）与官方新版不一致，按 ultralytics main 分支源码重写，双端（宿主/容器）受益。
- **实测结论**：真实垃圾场景检出准（Recall 100%），误报集中在室内杂乱/暗光低置信框（11/11 均 <0.65），阈值 0.5 是下一步优化点。
- **产物治理方向**：用户确认"不要存放产物也可以"——单次产物默认不落盘，保留 live_latest.jpg 与日志。
