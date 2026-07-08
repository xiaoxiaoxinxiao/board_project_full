#!/bin/bash

cd /home/elf/slr_demo

# 等图形界面起来
sleep 8

# 设备权限
chmod 666 /dev/rknpu 2>/dev/null
chmod 666 /dev/video31 2>/dev/null
chmod 666 /dev/media4 2>/dev/null

# 显示环境
export DISPLAY=:0
export LIBGL_ALWAYS_SOFTWARE=1
export QT_X11_NO_MITSHM=1

# 启动程序
python3 /home/elf/slr_demo/realtime_demo_show.py
