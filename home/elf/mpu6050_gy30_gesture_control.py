#!/usr/bin/env python3
import fcntl
import time
from datetime import datetime

MPU_I2C_BUS = "/dev/i2c-3"
MPU_ADDR = 0x68

GY30_I2C_BUS = "/dev/i2c-4"
GY30_ADDR = 0x23

BACKLIGHT_DIR = "/sys/class/backlight/backlight-dsi"
BRIGHTNESS_FILE = BACKLIGHT_DIR + "/brightness"
MAX_BRIGHTNESS_FILE = BACKLIGHT_DIR + "/max_brightness"

LIGHT_VALUE_FILE = "/home/elf/gy30_light_value.txt"

LOG_FILE = "/home/elf/mpu6050_gy30_gesture_control.log"
ERROR_LOG_FILE = "/home/elf/mpu6050_gy30_gesture_control_error.log"

# 如果左右反了，把 1 改成 -1
DIRECTION = 1

CALIBRATION_TIME = 2.0

# 达到阈值就触发
RIGHT_PUSH_THRESHOLD = 0.045
LEFT_PUSH_THRESHOLD = -0.045
AY_DEADBAND = 0.012
TRIGGER_COUNT = 1

# 只有触发后，才 5 秒不读取 MPU6050
MPU_IGNORE_TIME_AFTER_TRIGGER = 5.0

START_POSITION = 0

SLEEP_TIME = 0.05
DEBUG_PRINT = True

LUX_LOW = 20.0
LUX_HIGH = 50.0
BRIGHTNESS_TARGET_MAX = 200
BRIGHTNESS_OFF = 0

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


def log_msg(msg):
    text = "[%s] %s" % (now_str(), msg)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(text + "\n")
    except Exception:
        pass
    if DEBUG_PRINT:
        print(text, flush=True)


def log_error(msg):
    text = "[%s] %s" % (now_str(), msg)
    try:
        with open(ERROR_LOG_FILE, "a") as f:
            f.write(text + "\n")
    except Exception:
        pass
    print(text, flush=True)


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


def write_ui_data(lux, brightness, position, ay_logic, mpu_state, remain_sec):
    lcd_percent = brightness * 100.0 / MAX_BRIGHTNESS if MAX_BRIGHTNESS > 0 else 0.0
    percent = min(100.0, brightness * 100.0 / BRIGHTNESS_TARGET_MAX) if BRIGHTNESS_TARGET_MAX > 0 else 0.0

    line = "%.2f,%.2f,%s,%.2f,%d,%d,%d,%.6f,%s,%.2f\n" % (
        lux,
        percent,
        now_str(),
        lcd_percent,
        brightness,
        MAX_BRIGHTNESS,
        position,
        ay_logic,
        mpu_state,
        remain_sec
    )

    with open(LIGHT_VALUE_FILE, "w") as f:
        f.write(line)


def calibrate_ay(mpu):
    print("开始 AY 零漂校准，请保持 MPU6050 静止 %.1f 秒..." % CALIBRATION_TIME, flush=True)

    values = []
    start = time.time()

    while time.time() - start < CALIBRATION_TIME:
        _, ay = read_ay(mpu)
        values.append(ay)
        time.sleep(SLEEP_TIME)

    ay_zero = sum(values) / len(values)
    print("AY_ZERO = %.6f g" % ay_zero, flush=True)
    return ay_zero


def main():
    log_msg("MPU6050 + GY-30 手势联合控制程序启动")
    log_msg("position=1：亮度跟随 GY-30")
    log_msg("position=0：强制亮度为 0")
    log_msg("触发后 %.1f 秒不读取 MPU6050" % MPU_IGNORE_TIME_AFTER_TRIGGER)
    log_msg("DIRECTION=%d RIGHT=%.3f LEFT=%.3f TRIGGER_COUNT=%d" %
            (DIRECTION, RIGHT_PUSH_THRESHOLD, LEFT_PUSH_THRESHOLD, TRIGGER_COUNT))

    mpu = None
    gy30 = None

    try:
        mpu = open_i2c(MPU_I2C_BUS, MPU_ADDR)
        gy30 = open_i2c(GY30_I2C_BUS, GY30_ADDR)

        init_mpu6050(mpu)
        init_gy30(gy30)

        ay_zero = calibrate_ay(mpu)

        position = START_POSITION
        last_brightness = None
        right_count = 0
        left_count = 0
        mpu_ignore_until = 0.0
        last_ay_logic = 0.0

        if position == 0:
            set_brightness(BRIGHTNESS_OFF)
            last_brightness = BRIGHTNESS_OFF
            log_msg("初始 position=0，强制灭屏")
        else:
            log_msg("初始 position=1，亮度跟随 GY-30")

        while True:
            now = time.time()

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

            if now < mpu_ignore_until:
                remain = mpu_ignore_until - now
                mpu_state = "LOCK"
                write_ui_data(lux, brightness, position, last_ay_logic, mpu_state, remain)

                if DEBUG_PRINT:
                    print(
                        "MPU=LOCK %.2fs  position=%d  Lux=%7.2f  Brightness=%3d  Mode=%s"
                        % (remain, position, lux, brightness, mode),
                        flush=True
                    )

                time.sleep(SLEEP_TIME)
                continue

            mpu_state = "READ"
            remain = 0.0

            _, ay = read_ay(mpu)
            ay_logic = (ay - ay_zero) * DIRECTION
            last_ay_logic = ay_logic

            if abs(ay_logic) < AY_DEADBAND:
                ay_effective = 0.0
            else:
                ay_effective = ay_logic

            old_position = position

            if ay_effective > RIGHT_PUSH_THRESHOLD:
                right_count += 1
                left_count = 0
            elif ay_effective < LEFT_PUSH_THRESHOLD:
                left_count += 1
                right_count = 0
            else:
                right_count = 0
                left_count = 0

            if right_count >= TRIGGER_COUNT:
                position = 1
                right_count = 0
                left_count = 0
                mpu_ignore_until = time.time() + MPU_IGNORE_TIME_AFTER_TRIGGER
                log_msg("触发：向右推动，position=1，锁定 %.1f 秒，AY_LOGIC=%.6f" %
                        (MPU_IGNORE_TIME_AFTER_TRIGGER, ay_logic))

            elif left_count >= TRIGGER_COUNT:
                position = 0
                right_count = 0
                left_count = 0
                mpu_ignore_until = time.time() + MPU_IGNORE_TIME_AFTER_TRIGGER
                log_msg("触发：向左推动，position=0，锁定 %.1f 秒，AY_LOGIC=%.6f" %
                        (MPU_IGNORE_TIME_AFTER_TRIGGER, ay_logic))

            if position != old_position:
                if position == 0:
                    brightness = BRIGHTNESS_OFF
                    set_brightness(brightness)
                    last_brightness = brightness
                    mode = "FORCE_OFF"
                else:
                    brightness = lux_to_brightness(lux)
                    set_brightness(brightness)
                    last_brightness = brightness
                    mode = "GY30_AUTO"

            write_ui_data(lux, brightness, position, ay_logic, mpu_state, remain)

            if DEBUG_PRINT:
                print(
                    "MPU=READ  AY=% .6f  AY_LOGIC=% .6f  position=%d  Lux=%7.2f  Brightness=%3d  Mode=%s  Rcnt=%d Lcnt=%d"
                    % (ay, ay_logic, position, lux, brightness, mode, right_count, left_count),
                    flush=True
                )

            time.sleep(SLEEP_TIME)

    except KeyboardInterrupt:
        log_msg("程序手动退出")

    except Exception as e:
        log_error("程序异常：%s" % str(e))
        raise

    finally:
        if mpu:
            mpu.close()
        if gy30:
            gy30.close()


if __name__ == "__main__":
    main()
