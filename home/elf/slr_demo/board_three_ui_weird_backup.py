import os
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


BROKER_IP = "192.168.107.122"
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


def read_board_temp():
    root = "/sys/class/thermal"
    temps = []

    if not os.path.exists(root):
        return "--"

    for name in os.listdir(root):
        if not name.startswith("thermal_zone"):
            continue

        path = os.path.join(root, name, "temp")

        try:
            with open(path, "r") as f:
                value = int(f.read().strip())

            if value > 1000:
                value = value / 1000.0

            temps.append(value)
        except Exception:
            pass

    if not temps:
        return "--"

    return f"{max(temps):.1f}℃"


def read_text_file(path, default="--"):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()

        return s if s else default
    except Exception:
        return default


def read_first_existing(paths, default="--"):
    for path in paths:
        value = read_text_file(path, default=None)
        if value is not None:
            return value
    return default


def make_mqtt_client(client_id):
    if mqtt is None:
        return None

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


class BoardUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Board Three UI")
        self.root.geometry("800x480+0+0")
        self.root.configure(bg="#eeeeee")

        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass

        self.running = True
        self.cap = None
        self.video_photo = None

        self.mqtt_client = make_mqtt_client("rv1126b_board_ui")

        self.font_title = self.get_font(15, True)
        self.font_panel = self.get_font(11, True)
        self.font_text = self.get_font(10, False)
        self.font_small = self.get_font(8, False)
        self.font_big = self.get_font(13, True)

        self.build_ui()
        self.start_camera()
        self.start_mqtt()

        self.root.bind("<Escape>", lambda e: self.close())
        self.root.bind("q", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.update_camera()
        self.update_status()

    def get_font(self, size, bold=False):
        weight = "bold" if bold else "normal"
        for name in ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "Arial"]:
            try:
                return tkfont.Font(family=name, size=size, weight=weight)
            except Exception:
                pass
        return tkfont.Font(size=size, weight=weight)

    def make_panel(self, x, y, w, h, title):
        frame = tk.Frame(
            self.root,
            bg="white",
            bd=2,
            relief="solid",
            highlightbackground="black",
            highlightthickness=1,
        )
        frame.place(x=x, y=y, width=w, height=h)

        label = tk.Label(
            frame,
            text=title,
            bg="white",
            fg="black",
            font=self.font_panel,
        )
        label.place(x=4, y=4, width=w - 8, height=22)

        return frame

    def build_ui(self):
        title = tk.Label(
            self.root,
            text="板子端三分区 UI",
            bg="#eeeeee",
            fg="black",
            font=self.font_title,
        )
        title.place(x=0, y=4, width=800, height=28)

        # 固定坐标，别再让 grid 自动乱撑
        self.left = self.make_panel(5, 38, 440, 437, "左区：摄像头画面")
        self.middle = self.make_panel(450, 38, 210, 437, "中区：对话")
        self.right = self.make_panel(665, 38, 130, 437, "右区：状态")

        self.video_label = tk.Label(
            self.left,
            text="摄像头加载中...",
            bg="black",
            fg="white",
            font=self.font_text,
            justify="center",
        )
        self.video_label.place(x=8, y=32, width=424, height=310)

        self.sign_label = tk.Label(
            self.left,
            text="识别结果：等待识别",
            bg="white",
            fg="#008000",
            font=self.font_big,
            wraplength=420,
            justify="center",
        )
        self.sign_label.place(x=8, y=350, width=424, height=70)

        self.chat_box = tk.Text(
            self.middle,
            bg="#fafafa",
            fg="black",
            font=self.font_text,
            bd=1,
            relief="solid",
            wrap="word",
        )
        self.chat_box.place(x=6, y=32, width=198, height=390)
        self.chat_box.config(state="disabled")

        self.add_chat("系统", "等待工作人员消息")

        self.status_labels = {}

        rows = [
            ("MQTT", "未连"),
            ("温度", "--"),
            ("位置", "--"),
            ("光照", "--"),
            ("风扇", "--"),
            ("置信", "--"),
        ]

        y = 36
        for key, value in rows:
            self.add_status_row(key, value, y)
            y += 58

    def add_status_row(self, key, value, y):
        key_label = tk.Label(
            self.right,
            text=key,
            bg="white",
            fg="black",
            font=self.font_small,
            anchor="w",
        )
        key_label.place(x=6, y=y, width=45, height=20)

        value_label = tk.Label(
            self.right,
            text=value,
            bg="white",
            fg="#0055cc",
            font=self.font_small,
            anchor="w",
            wraplength=70,
            justify="left",
        )
        value_label.place(x=50, y=y, width=74, height=42)

        self.status_labels[key] = value_label

    def add_chat(self, speaker, text):
        self.chat_box.config(state="normal")
        now = time.strftime("%H:%M:%S")
        self.chat_box.insert("end", f"[{now}] {speaker}：\n")
        self.chat_box.insert("end", text + "\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def start_camera(self):
        if cv2 is None:
            self.video_label.config(text="未安装 cv2")
            return

        try:
            self.cap = cv2.VideoCapture(CAMERA_PIPELINE, cv2.CAP_GSTREAMER)

            if not self.cap.isOpened():
                self.video_label.config(text="摄像头未打开\n/dev/video31")
                self.cap = None
        except Exception as e:
            self.video_label.config(text=f"摄像头错误：{e}")
            self.cap = None

    def update_camera(self):
        if not self.running:
            return

        if self.cap is not None:
            try:
                ret, frame = self.cap.read()

                if ret and frame is not None:
                    frame = cv2.resize(frame, (424, 310))
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                    ok, ppm = cv2.imencode(".ppm", frame)

                    if ok:
                        self.video_photo = tk.PhotoImage(
                            data=ppm.tobytes(),
                            format="PPM",
                        )
                        self.video_label.config(image=self.video_photo, text="")
                else:
                    self.video_label.config(text="摄像头读取失败")
            except Exception as e:
                self.video_label.config(text=f"摄像头异常：{e}")

        self.root.after(50, self.update_camera)

    def start_mqtt(self):
        if self.mqtt_client is None:
            self.add_chat("系统", "paho-mqtt 未安装")
            return

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        self.mqtt_client.on_message = self.on_mqtt_message

        threading.Thread(target=self.mqtt_loop, daemon=True).start()

    def mqtt_loop(self):
        while self.running:
            try:
                self.mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                self.add_chat("系统", f"MQTT连接失败：{e}")
                time.sleep(3)

    def on_mqtt_connect(self, client, userdata, flags, rc):
        global mqtt_connected

        if rc == 0:
            mqtt_connected = True
            client.subscribe(TOPIC_PC_TO_BOARD)
            client.publish(TOPIC_BOARD_STATUS, "board ui online")
            self.root.after(0, lambda: self.add_chat("系统", "MQTT已连接"))
        else:
            mqtt_connected = False

    def on_mqtt_disconnect(self, client, userdata, rc):
        global mqtt_connected
        mqtt_connected = False

    def on_mqtt_message(self, client, userdata, msg):
        try:
            text = msg.payload.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = str(msg.payload)

        if not text:
            return

        self.root.after(0, lambda: self.add_chat("工作人员", text))

    def update_status(self):
        mqtt_text = "已连" if mqtt_connected else "未连"
        self.status_labels["MQTT"].config(text=mqtt_text)

        self.status_labels["温度"].config(text=read_board_temp())

        position = read_first_existing(POSITION_FILES, "--")
        self.status_labels["位置"].config(text=position)

        light = read_text_file(LIGHT_VALUE_FILE, "--")
        self.status_labels["光照"].config(text=light)

        fan = read_first_existing(FAN_FILES, "--")
        self.status_labels["风扇"].config(text=fan)

        self.status_labels["置信"].config(text="--")

        try:
            if self.mqtt_client is not None and mqtt_connected:
                payload = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "temp": read_board_temp(),
                    "position": position,
                    "light": light,
                    "fan": fan,
                }
                self.mqtt_client.publish(
                    TOPIC_BOARD_STATUS,
                    json.dumps(payload, ensure_ascii=False),
                )
        except Exception:
            pass

        self.root.after(1000, self.update_status)

    def close(self):
        self.running = False

        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass

        try:
            if self.mqtt_client is not None:
                self.mqtt_client.disconnect()
        except Exception:
            pass

        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = BoardUI()
    app.run()


if __name__ == "__main__":
    main()
PYEOFcd /home/elf/slr_demo
cp board_three_ui.py board_three_ui_weird_backup.py

cat > board_three_ui.py <<'PYEOF'
import os
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


BROKER_IP = "192.168.107.122"
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


def read_board_temp():
    root = "/sys/class/thermal"
    temps = []

    if not os.path.exists(root):
        return "--"

    for name in os.listdir(root):
        if not name.startswith("thermal_zone"):
            continue

        path = os.path.join(root, name, "temp")

        try:
            with open(path, "r") as f:
                value = int(f.read().strip())

            if value > 1000:
                value = value / 1000.0

            temps.append(value)
        except Exception:
            pass

    if not temps:
        return "--"

    return f"{max(temps):.1f}℃"


def read_text_file(path, default="--"):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()

        return s if s else default
    except Exception:
        return default


def read_first_existing(paths, default="--"):
    for path in paths:
        value = read_text_file(path, default=None)
        if value is not None:
            return value
    return default


def make_mqtt_client(client_id):
    if mqtt is None:
        return None

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


class BoardUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Board Three UI")
        self.root.geometry("800x480+0+0")
        self.root.configure(bg="#eeeeee")

        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass

        self.running = True
        self.cap = None
        self.video_photo = None

        self.mqtt_client = make_mqtt_client("rv1126b_board_ui")

        self.font_title = self.get_font(15, True)
        self.font_panel = self.get_font(11, True)
        self.font_text = self.get_font(10, False)
        self.font_small = self.get_font(8, False)
        self.font_big = self.get_font(13, True)

        self.build_ui()
        self.start_camera()
        self.start_mqtt()

        self.root.bind("<Escape>", lambda e: self.close())
        self.root.bind("q", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.update_camera()
        self.update_status()

    def get_font(self, size, bold=False):
        weight = "bold" if bold else "normal"
        for name in ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "Arial"]:
            try:
                return tkfont.Font(family=name, size=size, weight=weight)
            except Exception:
                pass
        return tkfont.Font(size=size, weight=weight)

    def make_panel(self, x, y, w, h, title):
        frame = tk.Frame(
            self.root,
            bg="white",
            bd=2,
            relief="solid",
            highlightbackground="black",
            highlightthickness=1,
        )
        frame.place(x=x, y=y, width=w, height=h)

        label = tk.Label(
            frame,
            text=title,
            bg="white",
            fg="black",
            font=self.font_panel,
        )
        label.place(x=4, y=4, width=w - 8, height=22)

        return frame

    def build_ui(self):
        title = tk.Label(
            self.root,
            text="板子端三分区 UI",
            bg="#eeeeee",
            fg="black",
            font=self.font_title,
        )
        title.place(x=0, y=4, width=800, height=28)

        # 固定坐标，别再让 grid 自动乱撑
        self.left = self.make_panel(5, 38, 440, 437, "左区：摄像头画面")
        self.middle = self.make_panel(450, 38, 210, 437, "中区：对话")
        self.right = self.make_panel(665, 38, 130, 437, "右区：状态")

        self.video_label = tk.Label(
            self.left,
            text="摄像头加载中...",
            bg="black",
            fg="white",
            font=self.font_text,
            justify="center",
        )
        self.video_label.place(x=8, y=32, width=424, height=310)

        self.sign_label = tk.Label(
            self.left,
            text="识别结果：等待识别",
            bg="white",
            fg="#008000",
            font=self.font_big,
            wraplength=420,
            justify="center",
        )
        self.sign_label.place(x=8, y=350, width=424, height=70)

        self.chat_box = tk.Text(
            self.middle,
            bg="#fafafa",
            fg="black",
            font=self.font_text,
            bd=1,
            relief="solid",
            wrap="word",
        )
        self.chat_box.place(x=6, y=32, width=198, height=390)
        self.chat_box.config(state="disabled")

        self.add_chat("系统", "等待工作人员消息")

        self.status_labels = {}

        rows = [
            ("MQTT", "未连"),
            ("温度", "--"),
            ("位置", "--"),
            ("光照", "--"),
            ("风扇", "--"),
            ("置信", "--"),
        ]

        y = 36
        for key, value in rows:
            self.add_status_row(key, value, y)
            y += 58

    def add_status_row(self, key, value, y):
        key_label = tk.Label(
            self.right,
            text=key,
            bg="white",
            fg="black",
            font=self.font_small,
            anchor="w",
        )
        key_label.place(x=6, y=y, width=45, height=20)

        value_label = tk.Label(
            self.right,
            text=value,
            bg="white",
            fg="#0055cc",
            font=self.font_small,
            anchor="w",
            wraplength=70,
            justify="left",
        )
        value_label.place(x=50, y=y, width=74, height=42)

        self.status_labels[key] = value_label

    def add_chat(self, speaker, text):
        self.chat_box.config(state="normal")
        now = time.strftime("%H:%M:%S")
        self.chat_box.insert("end", f"[{now}] {speaker}：\n")
        self.chat_box.insert("end", text + "\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def start_camera(self):
        if cv2 is None:
            self.video_label.config(text="未安装 cv2")
            return

        try:
            self.cap = cv2.VideoCapture(CAMERA_PIPELINE, cv2.CAP_GSTREAMER)

            if not self.cap.isOpened():
                self.video_label.config(text="摄像头未打开\n/dev/video31")
                self.cap = None
        except Exception as e:
            self.video_label.config(text=f"摄像头错误：{e}")
            self.cap = None

    def update_camera(self):
        if not self.running:
            return

        if self.cap is not None:
            try:
                ret, frame = self.cap.read()

                if ret and frame is not None:
                    frame = cv2.resize(frame, (424, 310))
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                    ok, ppm = cv2.imencode(".ppm", frame)

                    if ok:
                        self.video_photo = tk.PhotoImage(
                            data=ppm.tobytes(),
                            format="PPM",
                        )
                        self.video_label.config(image=self.video_photo, text="")
                else:
                    self.video_label.config(text="摄像头读取失败")
            except Exception as e:
                self.video_label.config(text=f"摄像头异常：{e}")

        self.root.after(50, self.update_camera)

    def start_mqtt(self):
        if self.mqtt_client is None:
            self.add_chat("系统", "paho-mqtt 未安装")
            return

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        self.mqtt_client.on_message = self.on_mqtt_message

        threading.Thread(target=self.mqtt_loop, daemon=True).start()

    def mqtt_loop(self):
        while self.running:
            try:
                self.mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                self.add_chat("系统", f"MQTT连接失败：{e}")
                time.sleep(3)

    def on_mqtt_connect(self, client, userdata, flags, rc):
        global mqtt_connected

        if rc == 0:
            mqtt_connected = True
            client.subscribe(TOPIC_PC_TO_BOARD)
            client.publish(TOPIC_BOARD_STATUS, "board ui online")
            self.root.after(0, lambda: self.add_chat("系统", "MQTT已连接"))
        else:
            mqtt_connected = False

    def on_mqtt_disconnect(self, client, userdata, rc):
        global mqtt_connected
        mqtt_connected = False

    def on_mqtt_message(self, client, userdata, msg):
        try:
            text = msg.payload.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = str(msg.payload)

        if not text:
            return

        self.root.after(0, lambda: self.add_chat("工作人员", text))

    def update_status(self):
        mqtt_text = "已连" if mqtt_connected else "未连"
        self.status_labels["MQTT"].config(text=mqtt_text)

        self.status_labels["温度"].config(text=read_board_temp())

        position = read_first_existing(POSITION_FILES, "--")
        self.status_labels["位置"].config(text=position)

        light = read_text_file(LIGHT_VALUE_FILE, "--")
        self.status_labels["光照"].config(text=light)

        fan = read_first_existing(FAN_FILES, "--")
        self.status_labels["风扇"].config(text=fan)

        self.status_labels["置信"].config(text="--")

        try:
            if self.mqtt_client is not None and mqtt_connected:
                payload = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "temp": read_board_temp(),
                    "position": position,
                    "light": light,
                    "fan": fan,
                }
                self.mqtt_client.publish(
                    TOPIC_BOARD_STATUS,
                    json.dumps(payload, ensure_ascii=False),
                )
        except Exception:
            pass

        self.root.after(1000, self.update_status)

    def close(self):
        self.running = False

        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass

        try:
            if self.mqtt_client is not None:
                self.mqtt_client.disconnect()
        except Exception:
            pass

        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = BoardUI()
    app.run()


if __name__ == "__main__":
    main()
