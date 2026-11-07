# -*- coding: utf-8 -*-
"""ByteTrack 板端冒烟测试：验证 scipy/lap/BYTETracker 可导入且可实例化"""
import numpy as np
import scipy
import lap
print("numpy", np.__version__, "| scipy", scipy.__version__, "| lap", lap.__version__)

from types import SimpleNamespace
from tracker.byte_tracker import BYTETracker

# 官方 BYTETrackerArgs 默认值
ARGS = SimpleNamespace(
    track_high_thresh=0.5, track_low_thresh=0.1,
    new_track_thresh=0.6, track_buffer=30, match_thresh=0.8,
)
t = BYTETracker(ARGS, frame_rate=30)
print("BYTETracker instance OK")

# 造一个假检测（1 个框）喂给 tracker，验证 update 全链路
res = SimpleNamespace(
    conf=np.array([0.9], dtype=np.float32),
    xywh=np.array([[320.0, 320.0, 100.0, 200.0]], dtype=np.float32),
    cls=np.array([0], dtype=np.int32),
)
frame = np.zeros((640, 640, 3), dtype=np.uint8)
out = t.update(res, frame)
print("update ->", None if out is None else out.shape, out)
print("SMOKE OK")
