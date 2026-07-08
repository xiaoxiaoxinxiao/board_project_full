#!/usr/bin/env python3
import fcntl
import time
import math
import struct

I2C_BUS = "/dev/i2c-3"
MPU_ADDR = 0x68

I2C_SLAVE = 0x0703

PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43

ACCEL_SCALE = 16384.0
GYRO_SCALE = 131.0

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
    time.sleep(0.1)

def read_sensor(f):
    data = read_regs(f, ACCEL_XOUT_H, 14)

    ax_raw = read_word_2c(data, 0)
    ay_raw = read_word_2c(data, 2)
    az_raw = read_word_2c(data, 4)

    temp_raw = read_word_2c(data, 6)

    gx_raw = read_word_2c(data, 8)
    gy_raw = read_word_2c(data, 10)
    gz_raw = read_word_2c(data, 12)

    ax = ax_raw / ACCEL_SCALE
    ay = ay_raw / ACCEL_SCALE
    az = az_raw / ACCEL_SCALE

    gx = gx_raw / GYRO_SCALE
    gy = gy_raw / GYRO_SCALE
    gz = gz_raw / GYRO_SCALE

    temp = temp_raw / 340.0 + 36.53

    return ax, ay, az, gx, gy, gz, temp

def calc_acc_angle(ax, ay, az):
    roll = math.degrees(math.atan2(ay, az))
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
    return pitch, roll

def main():
    f = open_i2c()
    init_mpu6050(f)

    print("MPU6050 姿态读取开始，按 Ctrl+C 退出")
    print("I2C_BUS =", I2C_BUS)
    print("ADDR    = 0x%02X" % MPU_ADDR)

    pitch = 0.0
    roll = 0.0
    alpha = 0.96

    last_time = time.time()

    try:
        while True:
            now = time.time()
            dt = now - last_time
            last_time = now

            ax, ay, az, gx, gy, gz, temp = read_sensor(f)

            acc_pitch, acc_roll = calc_acc_angle(ax, ay, az)

            # 互补滤波：陀螺仪短时间平滑，加速度计长期校正
            pitch = alpha * (pitch + gy * dt) + (1 - alpha) * acc_pitch
            roll = alpha * (roll + gx * dt) + (1 - alpha) * acc_roll

            print(
                "Pitch=%7.2f°  Roll=%7.2f°  "
                "AX=%6.3f AY=%6.3f AZ=%6.3f  "
                "GX=%7.2f GY=%7.2f GZ=%7.2f  Temp=%5.2f°C"
                % (pitch, roll, ax, ay, az, gx, gy, gz, temp)
            )

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        f.close()

if __name__ == "__main__":
    main()
