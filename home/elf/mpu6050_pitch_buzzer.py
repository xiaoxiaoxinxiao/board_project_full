import os
import time
import math
import json
import statistics
from datetime import datetime

try:
    import smbus
    SMBusClass = smbus.SMBus
except Exception:
    from smbus2 import SMBus as SMBusClass


# ================= 蜂鸣器配置 =================

# P16 第16脚，对应 Linux GPIO129
BUZZER_GPIO = 129

# 高电平触发：1响，0停
BUZZER_ACTIVE_HIGH = True


# ================= MPU6050 配置 =================

MPU_ADDR = 0x68


# ================= 零漂校准配置 =================

# 程序启动后先做 6 秒零漂校准
ZERO_DRIFT_SECONDS = 6.0
CALIB_SAMPLE_DELAY = 0.05


# ================= 报警配置 =================

# pitch 变化超过 3 度报警
PITCH_THRESHOLD_DEG = 3.0

# 报警总时长 5 秒
ALARM_SECONDS = 5.0

# 蜂鸣器响 0.5 秒，停 0.5 秒
BEEP_ON_SECONDS = 0.3
BEEP_OFF_SECONDS = 0.3

# 报警后冷却时间，防止连续重复报警
COOLDOWN_SECONDS = 3.0

# 正常检测采样间隔
SAMPLE_DELAY = 0.1

# 连续超过阈值几次才报警，防止瞬间抖动误触发
TRIGGER_COUNT = 3

# 低通滤波系数：越大越灵敏，越小越稳定
FILTER_ALPHA = 0.25

# False：始终以开机零漂角度为基准
# True：每次报警后重新设置当前角度为基准
RESET_BASE_AFTER_ALARM = False

STATUS_FILE = "/tmp/slr_pitch_alarm_status.json"


def gpio_init(gpio):
    path = f"/sys/class/gpio/gpio{gpio}"

    if not os.path.exists(path):
        try:
            with open("/sys/class/gpio/export", "w") as f:
                f.write(str(gpio))
            time.sleep(0.2)
        except Exception:
            pass

    with open(f"{path}/direction", "w") as f:
        f.write("out")

    buzzer_off()


def gpio_write(gpio, on):
    value = 1 if on else 0

    if not BUZZER_ACTIVE_HIGH:
        value = 0 if on else 1

    with open(f"/sys/class/gpio/gpio{gpio}/value", "w") as f:
        f.write(str(value))


def buzzer_on():
    gpio_write(BUZZER_GPIO, True)


def buzzer_off():
    gpio_write(BUZZER_GPIO, False)


def buzzer_alarm(seconds):
    """
    间歇报警：
    响 0.5 秒，停 0.5 秒，总共持续 seconds 秒。
    """
    print(
        f"蜂鸣器间歇报警：响 {BEEP_ON_SECONDS}s，停 {BEEP_OFF_SECONDS}s，总时长 {seconds}s",
        flush=True
    )

    start = time.time()

    while time.time() - start < seconds:
        buzzer_on()
        time.sleep(BEEP_ON_SECONDS)

        buzzer_off()

        remain = seconds - (time.time() - start)
        if remain <= 0:
            break

        time.sleep(min(BEEP_OFF_SECONDS, remain))

    buzzer_off()


def find_mpu_bus():
    for bus_id in range(0, 8):
        try:
            bus = SMBusClass(bus_id)

            # 唤醒 MPU6050
            bus.write_byte_data(MPU_ADDR, 0x6B, 0x00)
            time.sleep(0.1)

            who = bus.read_byte_data(MPU_ADDR, 0x75)
            print(f"找到 MPU6050: /dev/i2c-{bus_id}, WHO_AM_I=0x{who:02x}", flush=True)
            return bus

        except Exception:
            try:
                bus.close()
            except Exception:
                pass

    raise RuntimeError("没有找到 MPU6050，请检查 I2C 接线和地址")


def read_word(bus, reg):
    high = bus.read_byte_data(MPU_ADDR, reg)
    low = bus.read_byte_data(MPU_ADDR, reg + 1)

    value = (high << 8) | low

    if value >= 0x8000:
        value = -((65535 - value) + 1)

    return value


def read_accel(bus):
    ax = read_word(bus, 0x3B) / 16384.0
    ay = read_word(bus, 0x3D) / 16384.0
    az = read_word(bus, 0x3F) / 16384.0

    return ax, ay, az


def calc_pitch(ax, ay, az):
    """
    通过加速度计算 pitch 角度。
    """
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
    return pitch


def read_pitch(bus):
    ax, ay, az = read_accel(bus)
    return calc_pitch(ax, ay, az)


def trim_mean(values, trim_ratio=0.15):
    if not values:
        return 0.0

    values = sorted(values)
    n = len(values)
    cut = int(n * trim_ratio)

    if n > 10 and cut > 0:
        values = values[cut:-cut]

    return sum(values) / len(values)


def write_status(base_pitch, pitch, diff, alarm, mode="NORMAL"):
    data = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "base_pitch": round(float(base_pitch), 3),
        "pitch": round(float(pitch), 3),
        "diff": round(float(diff), 3),
        "threshold": PITCH_THRESHOLD_DEG,
        "alarm": bool(alarm),
        "status": "ALARM" if alarm else mode,
        "buzzer": "ON" if alarm else "OFF",
        "alarm_pattern": f"{BEEP_ON_SECONDS}s_on_{BEEP_OFF_SECONDS}s_off_{ALARM_SECONDS}s_total",
    }

    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def zero_drift_calibration(bus):
    """
    上电后进行零漂校准。
    校准期间蜂鸣器保持关闭，不报警。
    """
    print(f"开始 {ZERO_DRIFT_SECONDS:.1f} 秒零漂校准，请保持板子静止...", flush=True)

    values = []
    start = time.time()
    last_print = 0

    while time.time() - start < ZERO_DRIFT_SECONDS:
        buzzer_off()

        pitch = read_pitch(bus)
        values.append(pitch)

        remain = ZERO_DRIFT_SECONDS - (time.time() - start)

        if time.time() - last_print >= 1.0:
            print(f"零漂校准中... 剩余 {max(0, remain):.1f}s, pitch={pitch:.3f}°", flush=True)
            last_print = time.time()

        write_status(
            base_pitch=0.0,
            pitch=pitch,
            diff=0.0,
            alarm=False,
            mode="CALIBRATING"
        )

        time.sleep(CALIB_SAMPLE_DELAY)

    base_pitch = trim_mean(values, trim_ratio=0.15)

    try:
        std_val = statistics.pstdev(values)
    except Exception:
        std_val = 0.0

    print("零漂校准完成", flush=True)
    print(f"采样数量: {len(values)}", flush=True)
    print(f"初始基准 pitch = {base_pitch:.3f}°", flush=True)
    print(f"校准期间波动 std = {std_val:.3f}°", flush=True)

    buzzer_off()
    return base_pitch


def main():
    print("MPU6050 pitch 角度变化蜂鸣器报警启动", flush=True)
    print("蜂鸣器 GPIO:", BUZZER_GPIO, flush=True)
    print("触发阈值:", PITCH_THRESHOLD_DEG, "度", flush=True)
    print("报警方式: 0.5秒响 / 0.5秒停 / 总计5秒", flush=True)
    print("零漂校准:", ZERO_DRIFT_SECONDS, "秒", flush=True)

    # 程序一启动，立刻拉低蜂鸣器 GPIO
    gpio_init(BUZZER_GPIO)
    buzzer_off()

    bus = find_mpu_bus()

    # 启动后 6 秒零漂校准，期间不报警
    base_pitch = zero_drift_calibration(bus)

    filtered_pitch = base_pitch
    hit_count = 0
    last_alarm_time = 0

    print("进入正式检测模式...", flush=True)

    while True:
        try:
            raw_pitch = read_pitch(bus)

            filtered_pitch = (
                FILTER_ALPHA * raw_pitch +
                (1.0 - FILTER_ALPHA) * filtered_pitch
            )

            diff = abs(filtered_pitch - base_pitch)

            if diff >= PITCH_THRESHOLD_DEG:
                hit_count += 1
            else:
                hit_count = max(0, hit_count - 1)

            print(
                f"base={base_pitch:.2f}°, "
                f"raw={raw_pitch:.2f}°, "
                f"pitch={filtered_pitch:.2f}°, "
                f"diff={diff:.2f}°, "
                f"hit={hit_count}/{TRIGGER_COUNT}",
                flush=True
            )

            now = time.time()

            if hit_count >= TRIGGER_COUNT and now - last_alarm_time > COOLDOWN_SECONDS:
                print("pitch 变化超过阈值，蜂鸣器间歇报警 5 秒！", flush=True)

                write_status(base_pitch, filtered_pitch, diff, True, mode="ALARM")

                buzzer_alarm(ALARM_SECONDS)
                buzzer_off()

                last_alarm_time = time.time()
                hit_count = 0

                if RESET_BASE_AFTER_ALARM:
                    print("报警后重新进行零漂基准设置...", flush=True)
                    base_pitch = zero_drift_calibration(bus)
                    filtered_pitch = base_pitch

            else:
                write_status(base_pitch, filtered_pitch, diff, False, mode="NORMAL")
                buzzer_off()

            time.sleep(SAMPLE_DELAY)

        except KeyboardInterrupt:
            break

        except Exception as e:
            print("异常:", repr(e), flush=True)
            buzzer_off()
            time.sleep(1)

    buzzer_off()
    print("退出", flush=True)


if __name__ == "__main__":
    main()
