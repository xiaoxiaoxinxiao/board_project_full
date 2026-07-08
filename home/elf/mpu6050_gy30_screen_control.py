#!/usr/bin/env python3
import fcntl
import time
from datetime import datetime

# ===================== 基本配置 =====================

MPU_I2C_BUS = "/dev/i2c-3"
MPU_ADDR = 0x68

GY30_I2C_BUS = "/dev/i2c-4"
GY30_ADDR = 0x23

BACKLIGHT_DIR = "/sys/class/backlight/backlight-dsi"
BRIGHTNESS_FILE = BACKLIGHT_DIR + "/brightness"
MAX_BRIGHTNESS_FILE = BACKLIGHT_DIR + "/max_brightness"

LIGHT_VALUE_FILE = "/home/elf/gy30_light_value.txt"

# ===================== 平移动作识别配置 =====================

# 方向修正：
# 如果向右平移变成关闭、向左平移变成打开，就把 1 改成 -1
DIRECTION = 1

# 启动后静止校准时间
CALIBRATION_TIME = 2.0

# 平移触发阈值，单位 g
# 这个值越小越灵敏，越大越保守
RIGHT_MOVE_THRESHOLD = 0.060
LEFT_MOVE_THRESHOLD = -0.060

# 触发后冷却时间，忽略停止时的反向脉冲
COOLDOWN_TIME = 0.8

# 静止死区，小于这个值认为是噪声
AY_DEADBAND = 0.015

# 连续满足几次才触发，防止单点毛刺
TRIGGER_COUNT = 2

# ===================== 亮度规则 =====================

LUX_LOW = 20.0
LUX_HIGH = 50.0
BRIGHTNESS_TARGET_MAX = 200
BRIGHTNESS_OFF = 0

# 启动默认位置
# 0：启动后强制灭屏
# 1：启动后亮度跟随 GY-30
START_POSITION = 0

SLEEP_TIME = 0.05
DEBUG_PRINT = True

# ===================== I2C 固定配置 =====================

I2C_SLAVE = 0x0703

MPU_PWR_MGMT_1 = 0x6B
MPU_ACCEL_CONFIG = 0x1C
MPU_ACCEL_XOUT_H = 0x3B
MPU_ACCEL_SCALE = 16384.0

BH1750_POWER_ON = 0x01
BH1750_RESET = 0x07
BH1750_CONT_H_RES_MODE = 0x10


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_max_brightness():
    try:
        with open(MAX_BRIGHTNESS_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 255


MAX_BRIGHTNESS = get_max_brightness()


def set_brightness(value):
    value = int(value)

    if value < 0:
        value = 0

    if value > MAX_BRIGHTNESS:
        value = MAX_BRIGHTNESS

    with open(BRIGHTNESS_FILE, "w") as f:
        f.write(str(value))


def open_i2c(bus, addr):
    f = open(bus, "r+b", buffering=0)
    fcntl.ioctl(f, I2C_SLAVE, addr)
    return f


def write_reg(f, reg, value):
    f.write(bytes([reg, value]))


def read_regs(f, reg, length):
    f.write(bytes([reg]))
    return f.read(length)


def read_word_2c(data, index):
    value = (data[index] << 8) | data[index + 1]
    if value >= 0x8000:
        value -= 65536
    return value


def init_mpu6050(f):
    write_reg(f, MPU_PWR_MGMT_1, 0x00)
    time.sleep(0.2)

    # 加速度计 ±2g
    write_reg(f, MPU_ACCEL_CONFIG, 0x00)
    time.sleep(0.1)


def read_ay(f):
    data = read_regs(f, MPU_ACCEL_XOUT_H, 6)
    ay_raw = read_word_2c(data, 2)
    ay = ay_raw / MPU_ACCEL_SCALE
    return ay_raw, ay


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
    return raw, lux


def lux_to_brightness(lux):
    if lux < LUX_LOW:
        return 0

    if lux > LUX_HIGH:
        return BRIGHTNESS_TARGET_MAX

    ratio = (lux - LUX_LOW) / (LUX_HIGH - LUX_LOW)
    return int(ratio * BRIGHTNESS_TARGET_MAX)


def write_ui_data(lux, brightness, position, ay_logic):
    lcd_percent = brightness * 100.0 / MAX_BRIGHTNESS if MAX_BRIGHTNESS > 0 else 0.0
    percent = min(100.0, brightness * 100.0 / BRIGHTNESS_TARGET_MAX)

    # 保持你原 UI 前 6 项兼容：
    # Lux,百分比,时间,LCD百分比,实际Brightness,最大Brightness
    # 后面额外追加 position 和 AY_LOGIC
    line = "%.2f,%.2f,%s,%.2f,%d,%d,%d,%.6f\n" % (
        lux,
        percent,
        now_str(),
        lcd_percent,
        brightness,
        MAX_BRIGHTNESS,
        position,
        ay_logic
    )

    with open(LIGHT_VALUE_FILE, "w") as f:
        f.write(line)


def calibrate_ay(mpu):
    print("开始 AY 零漂校准，请保持 MPU6050 静止 %.1f 秒..." % CALIBRATION_TIME)

    values = []
    start = time.time()

    while time.time() - start < CALIBRATION_TIME:
        _, ay = read_ay(mpu)
        values.append(ay)
        time.sleep(SLEEP_TIME)

    ay_zero = sum(values) / len(values)
    print("AY_ZERO = %.6f g" % ay_zero)

    return ay_zero


def main():
    print("MPU6050 + GY-30 平移控制程序启动")
    print("逻辑：识别平移开始时的首个加速度脉冲，触发后冷却，忽略停止反向脉冲")
    print("DIRECTION =", DIRECTION)
    print("RIGHT_MOVE_THRESHOLD =", RIGHT_MOVE_THRESHOLD)
    print("LEFT_MOVE_THRESHOLD  =", LEFT_MOVE_THRESHOLD)
    print("COOLDOWN_TIME =", COOLDOWN_TIME)
    print("TRIGGER_COUNT =", TRIGGER_COUNT)
    print()

    mpu = open_i2c(MPU_I2C_BUS, MPU_ADDR)
    gy30 = open_i2c(GY30_I2C_BUS, GY30_ADDR)

    init_mpu6050(mpu)
    init_gy30(gy30)

    ay_zero = calibrate_ay(mpu)

    position = START_POSITION
    last_brightness = None

    if position == 0:
        set_brightness(BRIGHTNESS_OFF)
        last_brightness = BRIGHTNESS_OFF
        print("初始 position=0，强制灭屏")
    else:
        print("初始 position=1，亮度跟随 GY-30")

    right_count = 0
    left_count = 0
    last_trigger_time = 0.0

    try:
        while True:
            _, ay = read_ay(mpu)

            # 去零漂 + 方向修正
            ay_logic = (ay - ay_zero) * DIRECTION

            # 死区过滤，减少静止抖动
            if abs(ay_logic) < AY_DEADBAND:
                ay_effective = 0.0
            else:
                ay_effective = ay_logic

            now = time.time()
            can_trigger = (now - last_trigger_time) >= COOLDOWN_TIME

            old_position = position

            if can_trigger:
                if ay_effective > RIGHT_MOVE_THRESHOLD:
                    right_count += 1
                    left_count = 0
                elif ay_effective < LEFT_MOVE_THRESHOLD:
                    left_count += 1
                    right_count = 0
                else:
                    right_count = 0
                    left_count = 0

                if right_count >= TRIGGER_COUNT:
                    position = 1
                    right_count = 0
                    left_count = 0
                    last_trigger_time = now
                    print("触发：向右平移，position=1，亮度跟随 GY-30，AY_LOGIC=%.6f" % ay_logic)

                elif left_count >= TRIGGER_COUNT:
                    position = 0
                    right_count = 0
                    left_count = 0
                    last_trigger_time = now
                    print("触发：向左平移，position=0，强制灭屏，AY_LOGIC=%.6f" % ay_logic)
            else:
                right_count = 0
                left_count = 0

            _, lux = read_lux(gy30)

            if position == 0:
                brightness = BRIGHTNESS_OFF
                mode = "FORCE_OFF"
            else:
                brightness = lux_to_brightness(lux)
                mode = "GY30_AUTO"

            if brightness != last_brightness:
                set_brightness(brightness)
                last_brightness = brightness

            write_ui_data(lux, brightness, position, ay_logic)

            if DEBUG_PRINT:
                print(
                    "AY=% .6f  AY_LOGIC=% .6f  position=%d  Lux=%7.2f  Brightness=%3d  Mode=%s  Rcnt=%d Lcnt=%d"
                    % (
                        ay,
                        ay_logic,
                        position,
                        lux,
                        brightness,
                        mode,
                        right_count,
                        left_count
                    )
                )

            time.sleep(SLEEP_TIME)

    except KeyboardInterrupt:
        print("\n程序退出")

    finally:
        mpu.close()
        gy30.close()


if __name__ == "__main__":
    main()
