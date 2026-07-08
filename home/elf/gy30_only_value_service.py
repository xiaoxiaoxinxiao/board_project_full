#!/usr/bin/env python3
import fcntl
import time
from datetime import datetime

# GY-30 / BH1750 配置
GY30_I2C_BUS = "/dev/i2c-4"
GY30_ADDR = 0x23

# 输出给 UI / MQTT UI 使用的数据文件
LIGHT_VALUE_FILE = "/home/elf/gy30_light_value.txt"

# 保持和你原来 UI 文件格式兼容
# Lux,百分比,时间,LCD百分比,实际Brightness,最大Brightness,position,AY_LOGIC,MPU状态,剩余秒数

I2C_SLAVE = 0x0703

BH1750_POWER_ON = 0x01
BH1750_RESET = 0x07
BH1750_CONT_H_RES_MODE = 0x10

SLEEP_TIME = 1.0


def open_i2c(bus, addr):
    f = open(bus, "r+b", buffering=0)
    fcntl.ioctl(f, I2C_SLAVE, addr)
    return f


def init_gy30(f):
    f.write(bytes([BH1750_POWER_ON]))
    time.sleep(0.05)

    f.write(bytes([BH1750_RESET]))
    time.sleep(0.05)

    f.write(bytes([BH1750_CONT_H_RES_MODE]))
    time.sleep(0.2)


def read_lux(f):
    data = f.read(2)

    if len(data) != 2:
        raise RuntimeError("GY-30 读取失败")

    raw = (data[0] << 8) | data[1]
    lux = raw / 1.2
    return lux


def lux_to_percent(lux):
    # 这里只是给 UI 显示用，不控制屏幕亮度
    # <60 低，60~150 正常，>150 高
    if lux <= 0:
        return 0.0
    if lux >= 150:
        return 100.0
    return max(0.0, min(100.0, lux / 150.0 * 100.0))


def write_light_file(lux):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    percent = lux_to_percent(lux)

    # 不再控制屏幕亮度，所以 brightness 写 0，max 写 255
    brightness = 0
    max_brightness = 255
    lcd_percent = 0.0

    # position 固定写 -1，表示当前没有启用 MPU6050
    position = -1
    ay_logic = 0.0
    mpu_state = "DISABLED"
    remain = 0.0

    line = "%.2f,%.2f,%s,%.2f,%d,%d,%d,%.6f,%s,%.2f\n" % (
        lux,
        percent,
        now,
        lcd_percent,
        brightness,
        max_brightness,
        position,
        ay_logic,
        mpu_state,
        remain
    )

    with open(LIGHT_VALUE_FILE, "w") as f:
        f.write(line)


def main():
    print("GY-30 only value service start")
    print("GY30_I2C_BUS =", GY30_I2C_BUS)
    print("GY30_ADDR    = 0x%02X" % GY30_ADDR)
    print("OUTPUT_FILE  =", LIGHT_VALUE_FILE)

    gy30 = open_i2c(GY30_I2C_BUS, GY30_ADDR)
    init_gy30(gy30)

    try:
        while True:
            lux = read_lux(gy30)
            write_light_file(lux)
            print("Lux=%.2f lx" % lux, flush=True)
            time.sleep(SLEEP_TIME)

    finally:
        gy30.close()


if __name__ == "__main__":
    main()
