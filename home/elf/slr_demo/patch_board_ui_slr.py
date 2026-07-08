import os
import re
import time

FILE = "board_three_ui.py"

if not os.path.exists(FILE):
    raise FileNotFoundError(FILE)

with open(FILE, "r", encoding="utf-8") as f:
    code = f.read()

backup = f"board_three_ui.py.bak_before_slr_auto_{time.strftime('%Y%m%d_%H%M%S')}"
with open(backup, "w", encoding="utf-8") as f:
    f.write(code)

print("已备份：", backup)

# ============================================================
# 1. 添加手语识别相关 import
# ============================================================

IMPORT_BLOCK = '''
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
'''

if "===== SLR imports" not in code:
    if "from tkinter import font as tkfont" in code:
        code = code.replace(
            "from tkinter import font as tkfont\n",
            "from tkinter import font as tkfont\n" + IMPORT_BLOCK + "\n",
            1
        )
    else:
        raise RuntimeError("找不到 tkinter import 位置，无法自动插入 import")
else:
    print("SLR import 已存在，跳过。")


# ============================================================
# 2. 在 self.start_camera() 前初始化 RKNN 模型
# ============================================================

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
        raise RuntimeError("找不到 self.start_camera()，无法插入模型初始化代码")
    code = code.replace(target, INIT_BLOCK + target, 1)
else:
    print("SLR model init 已存在，跳过。")


# ============================================================
# 3. 添加 run_sign_recognition() 函数
# ============================================================

RUN_FUNC = r'''
    def run_sign_recognition(self, frame_bgr):
        """
        使用 body_pose.rknn + hand_pose.rknn 提取关键点，
        再用 sign_tcn_64x46.rknn 输出 500 类手语结果。
        最终结果显示到左侧 self.sign_label。
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
else:
    print("run_sign_recognition 已存在，跳过。")


# ============================================================
# 4. 在 update_camera() 中插入识别调用
# ============================================================

if "self.run_sign_recognition(infer_frame)" not in code:
    pattern = r'(\n\s*if ret and frame is not None:\n)(\s*)frame = cv2\.resize\(frame, \(484, 350\)\)'
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
        raise RuntimeError("没有成功修改 update_camera()，未找到 frame resize 位置")

    code = code_new
else:
    print("update_camera 识别调用已存在，跳过。")


with open(FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("自动修改完成：", FILE)
print("备份文件：", backup)
