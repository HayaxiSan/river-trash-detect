#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thermal_monitor.py — OLED 温度+电压显示（单页，不翻页）
读取 SoC 温度（thermal_zone0）+ 电源电压（hwmon tcpm_source_psy in0_input），
单页显示在 0.96" SSD1306 (128x64) 上：
  左上: 设备温度    右上: 12.0V（电源电压）
  第二行: 芯片温度 45°C
依据：/sys/class/thermal/thermal_zone0（官方 sysfs）；电压 /sys/class/hwmon/hwmon*/in0_input（Type-C PD 电源）
用法: python3 thermal_monitor.py [--interval 1]
"""
import time, argparse, glob
from luma.oled.device import ssd1306
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from PIL import ImageFont

FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

# 温度区中文名映射（实测 type 字符串 -> 显示名，type 均带 -thermal 后缀）
NAME_MAP = {
    "soc-thermal":        "芯片",    # SoC = 整颗芯片
    "bigcore0-thermal":   "大核",    # 大核集群 0
    "bigcore1-thermal":   "大核2",   # 大核集群 1
    "littlecore-thermal": "小核",    # 小核集群
    "center-thermal":     "中枢",    # 芯片中央互联
    "gpu-thermal":        "GPU",     # 图形处理器
    "npu-thermal":        "NPU",     # 神经网络处理器
}

def read_temps():
    """读取 /sys/class/thermal/thermal_zone0~7 温度，返回 [(中文名, 温度°C), ...]"""
    rows = []
    for i in range(8):
        try:
            ztype = open(f"/sys/class/thermal/thermal_zone{i}/type").read().strip()
            raw = int(open(f"/sys/class/thermal/thermal_zone{i}/temp").read().strip())
        except (FileNotFoundError, ValueError, OSError):
            continue
        name = NAME_MAP.get(ztype, ztype)
        rows.append((name, raw / 1000.0))
    return rows

def read_soc_temp():
    """读取 SoC 温度（thermal_zone0/soc-thermal），返回 °C 或 None"""
    try:
        raw = int(open("/sys/class/thermal/thermal_zone0/temp").read().strip())
        return raw / 1000.0
    except (FileNotFoundError, ValueError, OSError):
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=1, help="刷新间隔秒数")
    args = ap.parse_args()

    serial = i2c(port=1, address=0x3C)
    device = ssd1306(serial)
    font12 = ImageFont.truetype(FONT, 12)
    font16 = ImageFont.truetype(FONT, 16)   # 右侧状态字放大到 16px，笔画更清晰

    # 单页显示的必要温度（thermal_zone 索引）：芯片 / 大核1 / 小核 / GPU / NPU
    ZONE_ROWS = [0, 1, 3, 5, 6]
    ROW_Y = [0, 13, 26, 39, 52]   # 5 行，每行 13px，正好 128x64 一屏

    while True:
        temps = read_temps()
        # 状态判定：按所有温度的最高值（>105 停止使用 / >85 请降温 / >75 高温 / 正常）
        try:
            hottest = max(t for zi, (_, t) in enumerate(temps) if zi in ZONE_ROWS)
            if hottest > 105:
                status = "停止使用"
            elif hottest > 85:
                status = "请降温"
            elif hottest > 75:
                status = "高温"
            else:
                status = "正常"
        except ValueError:
            status = "正常"
        with canvas(device) as draw:
            # 左侧：5 行温度（横排）
            for zi, y in zip(ZONE_ROWS, ROW_Y):
                if zi >= len(temps):
                    continue
                name, t = temps[zi]
                draw.text((2, y), f"{name}温度 {t:.0f}°C", font=font12, fill="white")
            # 右侧：状态文字竖写（16px，每字一行垂直堆叠，垂直居中）
            sx = 128 - 16 - 2
            sy0 = (64 - len(status) * 16) // 2
            for i, ch in enumerate(status):
                draw.text((sx, sy0 + i * 16), ch, font=font16, fill="white")
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
