#!/usr/bin/env python3
import time
from smbus import SMBus

BUS_NUM = 4
BH1750_ADDR = 0x23

POWER_ON = 0x01
RESET = 0x07
CONT_H_RES_MODE = 0x10

MAX_LUX = 1000.0


def read_lux(bus):
    bus.write_byte(BH1750_ADDR, POWER_ON)
    time.sleep(0.02)
    bus.write_byte(BH1750_ADDR, RESET)
    time.sleep(0.02)
    bus.write_byte(BH1750_ADDR, CONT_H_RES_MODE)
    time.sleep(0.18)

    data = bus.read_i2c_block_data(BH1750_ADDR, CONT_H_RES_MODE, 2)
    raw = (data[0] << 8) | data[1]
    lux = raw / 1.2
    return lux


def lux_to_percent(lux):
    percent = lux / MAX_LUX * 100
    if percent < 0:
        percent = 0
    if percent > 100:
        percent = 100
    return percent


def make_bar(percent):
    total = 30
    filled = int(total * percent / 100)
    empty = total - filled
    return "[" + "#" * filled + "-" * empty + "]"


def main():
    print("GY-30 / BH1750 light sensor")
    print("I2C: /dev/i2c-4")
    print("Address: 0x23")
    print("Range: 0 - 100")
    print("Press Ctrl+C to exit")
    print()

    bus = SMBus(BUS_NUM)

    try:
        while True:
            lux = read_lux(bus)
            percent = lux_to_percent(lux)
            bar = make_bar(percent)

            print(
                f"\rLux: {lux:8.2f} lx | Light: {percent:6.2f}% {bar}",
                end="",
                flush=True
            )

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nExit")

    finally:
        bus.close()


if __name__ == "__main__":
    main()
