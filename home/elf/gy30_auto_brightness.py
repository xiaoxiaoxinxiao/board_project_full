import os
import time
import json
from datetime import datetime


# =========================
# GY30 / BH1750 配置
# =========================

GY30_ADDR_CANDIDATES = [0x23, 0x5C]
I2C_CANDIDATES = [0, 1, 2, 3, 4, 5, 6, 7]

STATUS_FILE = "/tmp/slr_light_status.json"


# =========================
# 亮度参数
# =========================

# 你说基础亮度满意，所以最低亮度不要太低
# 如果还是太亮，可以改成 50
# 如果还是太暗，可以改成 70
MIN_BRIGHTNESS_PERCENT = 3

MAX_BRIGHTNESS_PERCENT = 100

# 低于这个 lux，屏幕用最低亮度
LUX_DARK = 5

# 高于这个 lux，屏幕用最高亮度
LUX_BRIGHT = 350

# 每次都强制写入，不跳过
INTERVAL_SECONDS = 1.0


try:
    import smbus
except Exception:
    try:
        import smbus2 as smbus
    except Exception:
        smbus = None


def find_backlight_paths():
    root = "/sys/class/backlight"
    paths = []

    if not os.path.exists(root):
        return paths

    for name in os.listdir(root):
        p = os.path.join(root, name)

        brightness = os.path.join(p, "brightness")
        max_brightness = os.path.join(p, "max_brightness")

        if os.path.isfile(brightness) and os.path.isfile(max_brightness):
            paths.append(p)

    return paths


def set_sys_backlight(percent):
    paths = find_backlight_paths()

    if not paths:
        print("[backlight] no /sys/class/backlight device")
        return False, []

    percent = int(max(1, min(100, percent)))

    results = []

    for p in paths:
        try:
            with open(os.path.join(p, "max_brightness"), "r") as f:
                max_brightness = int(f.read().strip())

            value = int(max_brightness * percent / 100.0)
            value = max(1, min(max_brightness, value))

            with open(os.path.join(p, "brightness"), "w") as f:
                f.write(str(value))

            # 写完再读一次，确认系统接受了
            with open(os.path.join(p, "brightness"), "r") as f:
                real_value = int(f.read().strip())

            print(f"[backlight] {p} -> {real_value}/{max_brightness} ({percent}%)")

            results.append({
                "path": p,
                "value": real_value,
                "max": max_brightness,
                "percent": percent,
            })

        except Exception as e:
            print("[backlight] write failed:", p, repr(e))

    return True, results


def lux_to_brightness_percent(lux):
    """
    分段自动调光：
    0~5 lux      ：15%  极暗环境
    5~70 lux     ：15% -> 60%
    70~350 lux   ：60% -> 100%
    350 lux以上  ：100%

    这样正常室内 60~80 lux 不会被压到 15%，
    只有真正挡住光照传感器/环境很暗时才会明显变暗。
    """
    if lux is None:
        return MAX_BRIGHTNESS_PERCENT

    lux = max(0.0, float(lux))

    LUX_VERY_DARK = 5.0
    LUX_NORMAL = 70.0
    LUX_BRIGHT_FULL = 350.0

    DARK_PERCENT = MIN_BRIGHTNESS_PERCENT      # 15%
    NORMAL_PERCENT = 60                        # 正常室内基础亮度
    FULL_PERCENT = MAX_BRIGHTNESS_PERCENT      # 100%

    if lux <= LUX_VERY_DARK:
        return DARK_PERCENT

    if lux <= LUX_NORMAL:
        ratio = (lux - LUX_VERY_DARK) / (LUX_NORMAL - LUX_VERY_DARK)
        percent = DARK_PERCENT + ratio * (NORMAL_PERCENT - DARK_PERCENT)
        return int(round(percent))

    if lux >= LUX_BRIGHT_FULL:
        return FULL_PERCENT

    ratio = (lux - LUX_NORMAL) / (LUX_BRIGHT_FULL - LUX_NORMAL)
    percent = NORMAL_PERCENT + ratio * (FULL_PERCENT - NORMAL_PERCENT)
    return int(round(percent))


def find_gy30():
    if smbus is None:
        raise RuntimeError("没有 smbus/smbus2，请安装 python3-smbus 或 smbus2")

    for bus_id in I2C_CANDIDATES:
        for addr in GY30_ADDR_CANDIDATES:
            try:
                bus = smbus.SMBus(bus_id)

                bus.write_byte(addr, 0x01)
                time.sleep(0.05)

                bus.write_byte(addr, 0x10)
                time.sleep(0.18)

                data = bus.read_i2c_block_data(addr, 0x10, 2)
                raw = (data[0] << 8) | data[1]
                lux = raw / 1.2

                print(f"发现 GY30/BH1750: /dev/i2c-{bus_id}, addr=0x{addr:02x}, lux={lux:.2f}")

                return bus, addr

            except Exception:
                try:
                    bus.close()
                except Exception:
                    pass

    raise RuntimeError("没有找到 GY30/BH1750，请检查 I2C 接线和地址")


def read_lux(bus, addr):
    try:
        bus.write_byte(addr, 0x10)
        time.sleep(0.18)

        data = bus.read_i2c_block_data(addr, 0x10, 2)
        raw = (data[0] << 8) | data[1]
        lux = raw / 1.2

        return float(lux)

    except Exception as e:
        print("读取 GY30 失败:", repr(e))
        return None


def write_status(lux, brightness_percent, method, backlight_results):
    data = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "lux": None if lux is None else round(float(lux), 2),
        "brightness_percent": int(brightness_percent),
        "method": method,
        "min_brightness_percent": MIN_BRIGHTNESS_PERCENT,
        "max_brightness_percent": MAX_BRIGHTNESS_PERCENT,
        "lux_dark": LUX_DARK,
        "lux_bright": LUX_BRIGHT,
        "backlight": backlight_results,
    }

    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def main():
    print("GY30 自动调光服务启动：sysfs 直接背光版")
    print("MIN_BRIGHTNESS_PERCENT =", MIN_BRIGHTNESS_PERCENT)
    print("MAX_BRIGHTNESS_PERCENT =", MAX_BRIGHTNESS_PERCENT)
    print("LUX_DARK =", LUX_DARK)
    print("LUX_BRIGHT =", LUX_BRIGHT)

    paths = find_backlight_paths()
    print("backlight paths:", paths)

    bus, addr = find_gy30()

    while True:
        lux = read_lux(bus, addr)
        percent = lux_to_brightness_percent(lux)

        ok, results = set_sys_backlight(percent)

        if ok:
            method = "sysfs_direct"
        else:
            method = "none"

        print(f"lux={lux} -> brightness={percent}% method={method}")

        write_status(lux, percent, method, results)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
