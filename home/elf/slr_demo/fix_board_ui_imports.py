import os
import time

FILE = "board_three_ui.py"

with open(FILE, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

backup = f"board_three_ui.py.bak_fix_imports_{time.strftime('%Y%m%d_%H%M%S')}"
with open(backup, "w", encoding="utf-8") as f:
    f.writelines(lines)

new_lines = []
skip = False

for line in lines:
    s = line.strip()

    # 删除旧的/坏的 SLR import 块
    if "===== SLR imports" in line:
        skip = True
        continue

    if "===== end SLR imports" in line:
        skip = False
        continue

    if skip:
        continue

    # 删除可能插坏的 import 行
    if "import numpy as np" in line:
        continue

    if "from collections import deque" in line:
        continue

    if "from realtime_demo import (" in line:
        skip = True
        continue

    # 跳过 realtime_demo import 块的剩余行
    if skip:
        if s == ")":
            skip = False
        continue

    new_lines.append(line)

IMPORT_BLOCK = [
    "\n",
    "# ===== SLR imports: camera keypoints -> sign classification =====\n",
    "import numpy as np\n",
    "from collections import deque\n",
    "\n",
    "from realtime_demo import (\n",
    "    BODY_MODEL,\n",
    "    HAND_MODEL,\n",
    "    SLR_MODEL,\n",
    "    DICT_PATH,\n",
    "    SEQ_LEN,\n",
    "    load_dict,\n",
    "    load_rknn,\n",
    "    preprocess,\n",
    "    parse_yolo_pose,\n",
    "    make_46_feature,\n",
    "    softmax,\n",
    ")\n",
    "# ===== end SLR imports =====\n",
    "\n",
]

inserted = False
final_lines = []

for line in new_lines:
    final_lines.append(line)

    if not inserted and "from tkinter import font as tkfont" in line:
        final_lines.extend(IMPORT_BLOCK)
        inserted = True

if not inserted:
    raise RuntimeError("没有找到 from tkinter import font as tkfont，无法插入 import")

with open(FILE, "w", encoding="utf-8") as f:
    f.writelines(final_lines)

print("修复完成")
print("备份文件：", backup)
