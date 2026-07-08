import os
import re
import time

FILE = "board_three_ui.py"

if not os.path.exists(FILE):
    raise FileNotFoundError(FILE)

with open(FILE, "r", encoding="utf-8") as f:
    code = f.read()

backup = f"board_three_ui.py.bak_publish_{time.strftime('%Y%m%d_%H%M%S')}"
with open(backup, "w", encoding="utf-8") as f:
    f.write(code)

print("已备份：", backup)

# 1. 添加板端发给电脑端的 topic
if 'TOPIC_BOARD_TO_PC' not in code:
    code = code.replace(
        'TOPIC_BOARD_STATUS = "board/status"',
        'TOPIC_BOARD_STATUS = "board/status"\nTOPIC_BOARD_TO_PC = "board/to_pc/text"',
        1
    )

# 2. 初始化 last_sent_word / last_sent_time
if "self.last_sent_word" not in code:
    code = code.replace(
        'self.infer_frame_count = 0',
        'self.infer_frame_count = 0\n        self.last_sent_word = ""\n        self.last_sent_time = 0.0',
        1
    )

# 3. 在识别成功后发布 MQTT
old = '''                    self.sign_label.config(
                        text=f"识别结果：{word}\\nID：{pred}  置信度：{score:.3f}"
                    )'''

new = '''                    self.sign_label.config(
                        text=f"识别结果：{word}\\nID：{pred}  置信度：{score:.3f}"
                    )

                    # ===== publish sign result to PC =====
                    now = time.time()
                    if (
                        self.mqtt_client is not None
                        and mqtt_connected
                        and word
                        and (word != self.last_sent_word or now - self.last_sent_time > 1.2)
                    ):
                        try:
                            self.mqtt_client.publish(TOPIC_BOARD_TO_PC, word)
                            self.add_chat("听障人士", word)
                            self.last_sent_word = word
                            self.last_sent_time = now
                        except Exception as e:
                            self.add_chat("MQTT", f"发送识别结果失败：{e}")
                    # ===== end publish sign result to PC ====='''

if "publish sign result to PC" not in code:
    if old not in code:
        raise RuntimeError("没有找到识别结果显示代码，无法插入 MQTT publish")
    code = code.replace(old, new, 1)
else:
    print("MQTT publish 逻辑已存在，跳过。")

with open(FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("修改完成：", FILE)
print("备份文件：", backup)
