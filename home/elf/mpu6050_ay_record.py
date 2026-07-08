#!/usr/bin/env python3
import fcntl
import time
import statistics
import csv
import os
from datetime import datetime

# ===================== 用户配置区 =====================

# MPU6050 所在 I2C 总线
I2C_BUS = "/dev/i2c-3"

# MPU6050 地址，AD0 悬空或接 GND 通常是 0x68
MPU_ADDR = 0x68

# 实时打印间隔，单位秒
SLEEP_TIME = 0.05

# 采集结果保存路径
LOG_FILE = "/home/elf/mpu6050_ay_log.csv"
REPORT_FILE = "/home/elf/mpu6050_ay_report.txt"

# 加速度量程：默认 ±2g，对应 16384 LSB/g
ACCEL_SCALE = 16384.0

# 最小死区，单位 g
# 即使数据很稳，也建议至少保留 0.03g 左右的死区，避免误触发
MIN_DEADBAND = 0.03

# 死区系数，越大越保守
# 6 表示用 6 倍噪声作为静止区间
DEADBAND_SIGMA_FACTOR = 6.0

# ======================================================

I2C_SLAVE = 0x0703

PWR_MGMT_1 = 0x6B
ACCEL_CONFIG = 0x1C
ACCEL_XOUT_H = 0x3B


def open_i2c():
    f = open(I2C_BUS, "r+b", buffering=0)
    fcntl.ioctl(f, I2C_SLAVE, MPU_ADDR)
    return f


def write_reg(f, reg, value):
    f.write(bytes([reg, value]))


def read_regs(f, reg, length):
    f.write(bytes([reg]))
    return f.read(length)


def read_word_2c(data, index):
    high = data[index]
    low = data[index + 1]
    value = (high << 8) | low
    if value >= 0x8000:
        value -= 65536
    return value


def init_mpu6050(f):
    # 解除睡眠
    write_reg(f, PWR_MGMT_1, 0x00)
    time.sleep(0.2)

    # 设置加速度计量程为 ±2g
    write_reg(f, ACCEL_CONFIG, 0x00)
    time.sleep(0.1)


def read_ay(f):
    # 从 ACCEL_XOUT_H 开始读取 AX、AY、AZ，共 6 字节
    data = read_regs(f, ACCEL_XOUT_H, 6)

    ay_raw = read_word_2c(data, 2)
    ay = ay_raw / ACCEL_SCALE

    return ay_raw, ay


def percentile(values, p):
    if not values:
        return 0.0

    values = sorted(values)
    if len(values) == 1:
        return values[0]

    k = (len(values) - 1) * p / 100.0
    lower = int(k)
    upper = min(lower + 1, len(values) - 1)
    weight = k - lower

    return values[lower] * (1.0 - weight) + values[upper] * weight


def robust_analysis(ay_values):
    if len(ay_values) < 10:
        raise RuntimeError("样本太少，至少采集 10 个以上数据")

    # 用中位数估算零漂，比平均值更不容易被左右运动影响
    zero = statistics.median(ay_values)

    # 用 MAD 估算静止噪声
    deviations = [abs(v - zero) for v in ay_values]
    mad = statistics.median(deviations)
    robust_sigma = 1.4826 * mad

    # 如果运动数据很多，MAD 会变大；再用中间 50% 的数据重新估算噪声
    sorted_values = sorted(ay_values)
    n = len(sorted_values)
    mid_values = sorted_values[int(n * 0.25): int(n * 0.75)]

    if len(mid_values) >= 10:
        zero_mid = statistics.median(mid_values)
        mid_deviations = [abs(v - zero_mid) for v in mid_values]
        mad_mid = statistics.median(mid_deviations)
        sigma_mid = 1.4826 * mad_mid

        # 零漂仍以整体中位数为主，噪声采用中间段估计
        noise_sigma = sigma_mid
    else:
        noise_sigma = robust_sigma

    deadband = max(MIN_DEADBAND, DEADBAND_SIGMA_FACTOR * noise_sigma)

    left_threshold = zero - deadband
    right_threshold = zero + deadband

    min_ay = min(ay_values)
    max_ay = max(ay_values)

    p05 = percentile(ay_values, 5)
    p25 = percentile(ay_values, 25)
    p50 = percentile(ay_values, 50)
    p75 = percentile(ay_values, 75)
    p95 = percentile(ay_values, 95)

    return {
        "zero": zero,
        "noise_sigma": noise_sigma,
        "deadband": deadband,
        "left_threshold": left_threshold,
        "right_threshold": right_threshold,
        "min_ay": min_ay,
        "max_ay": max_ay,
        "p05": p05,
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "p95": p95,
    }


def print_report(result, sample_count, duration):
    lines = []
    lines.append("")
    lines.append("==================== AY 自动分析结果 ====================")
    lines.append("采样数量: %d" % sample_count)
    lines.append("采样时长: %.2f 秒" % duration)
    lines.append("")
    lines.append("AY 零漂估计值: %.6f g" % result["zero"])
    lines.append("AY 噪声估计值: %.6f g" % result["noise_sigma"])
    lines.append("建议静止死区: ±%.6f g" % result["deadband"])
    lines.append("")
    lines.append("建议判定规则:")
    lines.append("  AY > %.6f g  判定为向右移动" % result["right_threshold"])
    lines.append("  AY < %.6f g  判定为向左移动" % result["left_threshold"])
    lines.append("  %.6f g <= AY <= %.6f g  判定为静止或不动作" % (
        result["left_threshold"],
        result["right_threshold"]
    ))
    lines.append("")
    lines.append("本次采集 AY 范围:")
    lines.append("  最小值: %.6f g" % result["min_ay"])
    lines.append("  最大值: %.6f g" % result["max_ay"])
    lines.append("")
    lines.append("分位数参考:")
    lines.append("  P05: %.6f g" % result["p05"])
    lines.append("  P25: %.6f g" % result["p25"])
    lines.append("  P50: %.6f g" % result["p50"])
    lines.append("  P75: %.6f g" % result["p75"])
    lines.append("  P95: %.6f g" % result["p95"])
    lines.append("========================================================")
    lines.append("")

    text = "\n".join(lines)
    print(text)

    with open(REPORT_FILE, "w") as f:
        f.write(text)

    print("AY 原始数据已保存到: %s" % LOG_FILE)
    print("AY 分析报告已保存到: %s" % REPORT_FILE)


def main():
    print("MPU6050 AY 实时采集程序启动")
    print("I2C_BUS  =", I2C_BUS)
    print("MPU_ADDR = 0x%02X" % MPU_ADDR)
    print("LOG_FILE =", LOG_FILE)
    print("")
    print("操作方法:")
    print("1. 程序启动后，先保持 MPU6050 静止 2~3 秒")
    print("2. 然后多次向右、向左移动 MPU6050")
    print("3. 采集足够后按 Ctrl+C 结束")
    print("4. 程序会自动计算 AY 零漂和左右移动阈值")
    print("")

    f = open_i2c()
    init_mpu6050(f)

    ay_values = []
    start_time = time.time()

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    with open(LOG_FILE, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["time_s", "ay_raw", "ay_g"])

        try:
            while True:
                now = time.time()
                t = now - start_time

                ay_raw, ay = read_ay(f)
                ay_values.append(ay)

                writer.writerow(["%.6f" % t, ay_raw, "%.6f" % ay])
                csvfile.flush()

                print("t=%8.3f s   AY_raw=%7d   AY=% .6f g" % (t, ay_raw, ay))

                time.sleep(SLEEP_TIME)

        except KeyboardInterrupt:
            duration = time.time() - start_time
            print("\n采集结束，正在分析 AY 数据...")

        finally:
            f.close()

    if len(ay_values) < 10:
        print("样本数量太少，无法分析。请至少运行几秒钟。")
        return

    result = robust_analysis(ay_values)
    print_report(result, len(ay_values), duration)


if __name__ == "__main__":
    main()
