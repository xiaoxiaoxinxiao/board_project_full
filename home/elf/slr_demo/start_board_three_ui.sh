#!/bin/bash

cd /home/elf/slr_demo || exit 1

echo "===================================="
echo "start_board_three_ui.sh start at $(date)"
echo "===================================="

export DISPLAY=:0
export HOME=/home/elf
export XAUTHORITY=/home/elf/.Xauthority
export QT_XCB_GL_INTEGRATION=none
export LIBGL_ALWAYS_SOFTWARE=1
export QT_QUICK_BACKEND=software
export PYTHONUNBUFFERED=1

# 等待图形界面
for i in $(seq 1 90); do
    if [ -S /tmp/.X11-unix/X0 ]; then
        echo "X display socket ready"
        sleep 5
        break
    fi
    echo "waiting X display... $i"
    sleep 1
done

# 权限
chmod 666 /dev/rknpu 2>/dev/null || true
chmod 666 /dev/video52 2>/dev/null || true
chmod 666 /dev/video* 2>/dev/null || true

# 保活循环：程序退出后自动重启
while true; do
    echo "launch board_three_ui.py at $(date)"

    pkill -f "python3.*board_three_ui.py" 2>/dev/null || true
    sleep 1

    python3 -u /home/elf/slr_demo/board_three_ui.py

    RET=$?
    echo "board_three_ui.py exited, code=$RET at $(date)"
    echo "restart after 5 seconds..."
    sleep 5
done
