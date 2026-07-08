import os
import time

FILE = "board_three_ui.py"

if not os.path.exists(FILE):
    raise FileNotFoundError(FILE)

with open(FILE, "r", encoding="utf-8") as f:
    code = f.read()

backup = f"board_three_ui.py.bak_no_repeat_{time.strftime('%Y%m%d_%H%M%S')}"
with open(backup, "w", encoding="utf-8") as f:
    f.write(code)

print("已备份：", backup)

old = "and (word != self.last_sent_word or now - self.last_sent_time > 1.2)"
new = "and word != self.last_sent_word"

if old in code:
    code = code.replace(old, new, 1)
    print("已修改：相同识别结果不再重复发送")
elif new in code:
    print("已经是去重逻辑，无需修改")
else:
    print("没有找到原来的重复发送条件，请手动检查 publish 逻辑")
    print("建议搜索：grep -n \"last_sent_word\\|publish\" board_three_ui.py")

with open(FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("完成：", FILE)
