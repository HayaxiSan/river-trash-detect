# 03 · 模型：来源、指标与转换链路

## 1. 部署模型

| 项 | 值 |
|---|---|
| 文件 | `models/best_opt.rknn` |
| 大小 | 9,210,895 字节（约 8.8 MB） |
| 结构 | YOLOv8n（nano） |
| 精度 | FP16（RKNN） |
| 类别 | 7 类：`塑料瓶 / 塑料杯 / 泡沫 / 塑料袋 / 纸盒 / 易拉罐 / 树叶` |
| 输入 | 640×640 |
| mAP50 | **0.8303**（v1.2 蒸馏版，验证集） |

推理阈值（脚本内可调）：

| 参数 | 值 | 位置 |
|---|---|---|
| `OBJ_THRESH` | 0.25 | `scripts/video_infer.py`、`scripts/rtsp_infer_stream.py` |
| `NMS_THRESH` | 0.45 | 同上 |
| `IMG_SIZE` | (640, 640) | 同上 |
| NPU 核心 | `NPU_CORE_0`（单核） | 同上 |

> 三核 AUTO 实测 27fps，仅比单核（26fps）多 1 帧且发热更高，已回退单核。

## 2. 训练（可选，不是复现必需）

| 文件 | 内容 |
|---|---|
| `notebooks/tud-yolov8n.ipynb` | 训练主 notebook（Colab，YOLOv8n 微调） |
| `notebooks/tud-exam-compare.ipynb` | 实测对比 notebook（考试集评估） |

> 训练数据集（约 5.9 GB 图片素材）**不在本仓库内**，属于项目私有数据。复现部署不需要它；要重新训练需自备数据集并按 notebook 内路径组织。

## 3. pt → onnx → rknn 转换链路（官方推荐路线）

```text
best.pt
  │  ① 用 airockchip 版 ultralytics 导出「优化版 ONNX」
  ▼
best_opt.onnx
  │  ② RKNN-Toolkit2 2.3.2 转换（v1.2 distill, FP16, 目标 rk3588）
  ▼
best_opt.rknn  ──► 本仓库 models/
```

### 3.1 为什么用 airockchip 版导出

官方 `rknn_model_zoo` 的 YOLOv8 转换文档要求使用 Rockchip 维护的 ultralytics 分支导出 ONNX（其输出头与标准 ultralytics 不同），否则后处理对不上、框错位。参见：

- https://github.com/airockchip/rknn_model_zoo （examples/yolov8）
- https://github.com/airockchip/ultralytics_yolov8

### 3.2 转换环境

| 项 | 值 |
|---|---|
| 环境 | Linux x86_64（本项目在 WSL Ubuntu 内） |
| RKNN-Toolkit2 | **2.3.2** |
| 目标平台 | `rk3588` |
| 量化 | FP16（distill 版） |

> Toolkit2 2.3.2 的安装包与 rknn_model_zoo v2.3.2 需配套下载，版本号必须一一对应。

### 3.3 转 RKNN 的核心调用（示意）

```python
from rknn.api import RKNN

rknn = RKNN()
rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]],
            target_platform='rk3588')
rknn.load_onnx(model='best_opt.onnx')
rknn.build(do_quantization=False)      # FP16，不做 int8 量化
rknn.export_rknn('best_opt.rknn')
rknn.release()
```

> 具体参数以 rknn_model_zoo `examples/yolov8/convert.py` 为准；本项目产物即按该脚本的流程生成。

## 4. 版本一致性（最容易踩的坑）

| 环节 | 版本 | 必须匹配的对象 |
|---|---|---|
| 模型转换 | RKNN-Toolkit2 **2.3.2** | — |
| 板端运行时 | librknnrt **2.3.2**（`lib/librknnrt.so`） | 与转换工具**同版本** |
| Python 推理 | rknn-toolkit-lite2 **2.3.2** | 与运行时同版本 |

版本错配的典型表现：`rknn_init` 失败、加载成功但输出全零、或直接段错误。

## 5. 换模型（换类别、换精度）怎么做

1. 新模型转好后覆盖 `models/best_opt.rknn`；
2. 如果类别名变了，只改脚本顶部 `CLASSES` 元组（**顺序必须与训练时类别索引一致**）；
3. 重启推流服务：

```bash
sudo systemctl restart rtsp-stream.service
```

> 后处理已按模型输出自动识别类别数，换任意类别数的 RKNN 模型无需改推理代码。

## 6. C demo 与 Python 两条推理通道

| 通道 | 入口 | 特点 |
|---|---|---|
| C（官方 demo） | `bin/rknn_yolov8_demo` + `scripts/infer.sh` | 单图推理，交叉编译产物，验证官方链路 |
| Python（lite2） | `scripts/video_infer.py`、`scripts/rtsp_infer_stream.py` | 视频流/推流/跟踪/告警，功能完整 |

两条通道都直接读 `models/best_opt.rknn`，互不影响。

C demo 由官方 `rknn_model_zoo/examples/yolov8/cpp` 用 Rockchip 交叉编译器（`gcc-buildroot-9.3.0-2020.03`）编译，目标 `aarch64`；仓库内已提供编译好的可执行文件，直接可用。
