import os
import re
import time

FILE = "board_three_ui.py"

if not os.path.exists(FILE):
    raise FileNotFoundError(FILE)

with open(FILE, "r", encoding="utf-8", errors="ignore") as f:
    code = f.read()

backup = f"board_three_ui.py.bak_repair_{time.strftime('%Y%m%d_%H%M%S')}"
with open(backup, "w", encoding="utf-8") as f:
    f.write(code)

print("已备份：", backup)

HEADER = '''import os
import time
import json
import threading
import tkinter as tk
from tkinter import font as tkfont

try:
    import cv2
except Exception:
    cv2 = None

try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

# ===== SLR imports: camera keypoints -> sign classification =====
import numpy as np
from collections import deque

from realtime_demo import (
    BODY_MODEL,
    HAND_MODEL,
    SLR_MODEL,
    DICT_PATH,
    SEQ_LEN,
    load_dict,
    load_rknn,
    preprocess,
    parse_yolo_pose,
    make_46_feature,
    softmax,
)
# ===== end SLR imports =====

BROKER_IP = "192.168.43.220"
BROKER_PORT = 1883

TOPIC_PC_TO_BOARD = "pc/to_board/text"
TOPIC_BOARD_STATUS = "board/status"

CAMERA_PIPELINE = (
    "v4l2src device=/dev/video31 ! "
    "video/x-raw,format=NV12,width=640,height=480 ! "
    "videoconvert ! "
    "video/x-raw,format=BGR ! "
    "appsink max-buffers=1 drop=true sync=false"
)

LIGHT_VALUE_FILE = "/home/elf/gy30_light_value.txt"

POSITION_FILES = [
    "/home/elf/position.txt",
    "/home/elf/position_state.txt",
    "/tmp/position.txt",
    "/tmp/position_state.txt",
]

FAN_FILES = [
    "/home/elf/fan_state.txt",
    "/tmp/fan_state.txt",
]

WINDOW_W = 800
WINDOW_H = 480

mqtt_connected = False


'''

m = re.search(r'(?m)^def read_board_temp\s*\(', code)
if not m:
    raise RuntimeError("找不到 def read_board_temp()，无法安全修复文件头")

code = HEADER + code[m.start():]

INIT_BLOCK = '''
        # ===== SLR model init =====
        self.id2word = load_dict(DICT_PATH)

        self.body_rknn = load_rknn(BODY_MODEL)
        self.hand_rknn = load_rknn(HAND_MODEL)
        self.slr_rknn = load_rknn(SLR_MODEL)

        self.seq_buffer = deque(maxlen=SEQ_LEN)
        self.last_word = ""
        self.last_score = 0.0
        self.infer_frame_count = 0
        # ===== end SLR model init =====

'''

if "===== SLR model init" not in code:
    target = "        self.start_camera()\n"
    if target not in code:
        raise RuntimeError("找不到 self.start_camera()，无法插入模型初始化")
    code = code.replace(target, INIT_BLOCK + target, 1)

RUN_FUNC = r'''
    def run_sign_recognition(self, frame_bgr):
        """
        使用 body_pose.rknn + hand_pose.rknn 提取关键点，
        再用 sign_tcn_64x46.rknn 输出 500 类手语结果。
        """
        try:
            frame_h, frame_w = frame_bgr.shape[:2]

            inp, scale, dw, dh = preprocess(frame_bgr)

            body_out = self.body_rknn.inference(inputs=[inp])
            hand_out = self.hand_rknn.inference(inputs=[inp])

            body_xy = parse_yolo_pose(
                body_out,
                num_kpts=17,
                scale=scale,
                dw=dw,
                dh=dh,
                frame_w=frame_w,
                frame_h=frame_h
            )

            hand_xy = parse_yolo_pose(
                hand_out,
                num_kpts=21,
                scale=scale,
                dw=dw,
                dh=dh,
                frame_w=frame_w,
                frame_h=frame_h
            )

            feat46 = make_46_feature(hand_xy, body_xy, frame_w, frame_h)

            if feat46 is None:
                self.sign_label.config(text="识别中：未检测到完整关键点")
                return

            self.seq_buffer.append(feat46)

            if len(self.seq_buffer) < SEQ_LEN:
                self.sign_label.config(
                    text=f"识别准备中：{len(self.seq_buffer)}/{SEQ_LEN}"
                )
                return

            x = np.array(self.seq_buffer, dtype=np.float32)
            x = np.expand_dims(x, axis=0)  # [1, 64, 46]

            slr_out = self.slr_rknn.inference(inputs=[x])
            logits = slr_out[0]

            prob = softmax(logits)
            pred = int(np.argmax(prob, axis=1)[0])
            score = float(np.max(prob))

            if score > 0.35:
                word = self.id2word.get(pred, "")

                if word:
                    self.last_word = word
                    self.last_score = score

                    self.sign_label.config(
                        text=f"识别结果：{word}\nID：{pred}  置信度：{score:.3f}"
                    )
                else:
                    self.sign_label.config(
                        text=f"未知标签：{pred}\n置信度：{score:.3f}"
                    )
            else:
                self.sign_label.config(
                    text=f"识别中...\n当前置信度：{score:.3f}"
                )

        except Exception as e:
            self.sign_label.config(text=f"识别异常：{e}")

'''

if "def run_sign_recognition" not in code:
    marker = "    def update_camera(self):"
    if marker not in code:
        raise RuntimeError("找不到 def update_camera(self)，无法插入识别函数")
    code = code.replace(marker, RUN_FUNC + "\n" + marker, 1)

if "self.run_sign_recognition(infer_frame)" not in code:
    pattern = r'(\n\s*if ret and frame is not None:\n)(\s*)frame = cv2\.resize\(frame,\s*\(484,\s*350\)\)'
    replacement = (
        r'\1'
        r'\2infer_frame = frame.copy()\n\n'
        r'\2# 每 3 帧做一次识别，避免 UI 卡顿\n'
        r'\2self.infer_frame_count += 1\n'
        r'\2if self.infer_frame_count % 3 == 0:\n'
        r'\2    self.run_sign_recognition(infer_frame)\n\n'
        r'\2frame = cv2.resize(frame, (484, 350))'
    )

    code_new, n = re.subn(pattern, replacement, code, count=1)

    if n != 1:
        raise RuntimeError("没有找到 update_camera() 里的 frame resize 位置，未插入识别调用")

    code = code_new

with open(FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("修复完成：", FILE)
print("备份文件：", backup)
