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


# =========================
# 配置区
# =========================

# 改成你电脑端 MQTT broker 的 IP
BROKER_IP = "192.168.107.122"
BROKER_PORT = 1883

TOPIC_PC_TO_BOARD = "pc/to_board/text"
TOPIC_BOARD_STATUS = "board/status"
TOPIC_BOARD_SIGN = "board/sign_text"
TOPIC_BOARD_LIGHT = "board/light"

CAMERA_PIPELINE = (
    "v4l2src device=/dev/video31 ! "
    "video/x-raw,format=NV12,width=640,height=480 ! "
    "videoconvert ! "
    "video/x-raw,format=BGR ! "
    "appsink max-buffers=1 drop=true sync=false"
)

# 已有传感器文件，存在就读，不存在就显示 --
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
latest_staff_text = "等待工作人员发送文字..."
latest_sign_text = "等待识别..."
latest_confidence = "--"
latest_light = "--"
latest_position = "--"
latest_fan = "--"


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


def read_light_value():
    value = read_text_file(LIGHT_VALUE_FILE, default="--")

    if value == "--":
        return value

    return value


def make_mqtt_client(client_id):
    if mqtt is None:
        return None

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


class ThreePartUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("板子端三分区 UI")

        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+0+0")
        self.root.configure(bg="#f5f5f5")

        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass

        self.default_font = self.get_font(10)
        self.title_font = self.get_font(16, bold=True)
        self.panel_title_font = self.get_font(12, bold=True)
        self.big_font = self.get_font(16, bold=True)
        self.small_font = self.get_font(8)

        self.video_photo = None
        self.cap = None
        self.running = True

        self.mqtt_client = make_mqtt_client("rv1126b_board_three_ui")

        self.build_ui()
        self.start_camera()
        self.start_mqtt()

        self.root.bind("<Escape>", lambda e: self.close())
        self.root.bind("q", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.update_status_loop()
        self.update_camera_loop()

    def get_font(self, size, bold=False):
        weight = "bold" if bold else "normal"
        names = [
            "Noto Sans CJK SC",
            "WenQuanYi Zen Hei",
            "Microsoft YaHei",
            "Arial",
        ]

        for name in names:
            try:
                return tkfont.Font(family=name, size=size, weight=weight)
            except Exception:
                pass

        return tkfont.Font(size=size, weight=weight)

    def build_ui(self):
        title = tk.Label(
            self.root,
            text="板子端三分区 UI",
            bg="#f5f5f5",
            fg="#111111",
            font=self.title_font,
        )
        title.pack(pady=(6, 3))

        main = tk.Frame(self.root, bg="#f5f5f5")
        main.pack(fill="both", expand=True, padx=2, pady=4)

        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)
        main.grid_columnconfigure(2, weight=1)
        main.grid_rowconfigure(0, weight=1)

        self.left_panel = self.make_panel(main, 0)
        self.middle_panel = self.make_panel(main, 1)
        self.right_panel = self.make_panel(main, 2)

        self.build_left_panel()
        self.build_middle_panel()
        self.build_right_panel()

    def make_panel(self, parent, col):
        panel = tk.Frame(
            parent,
            bg="white",
            bd=3,
            relief="solid",
            highlightbackground="black",
            highlightthickness=1,
        )

        panel.grid(row=0, column=col, sticky="nsew", padx=2, pady=4)
        panel.configure(width=250, height=410)
        panel.grid_propagate(False)

        return panel

    def build_left_panel(self):
        title = tk.Label(
            self.left_panel,
            text="左区：摄像头画面",
            bg="white",
            fg="#111111",
            font=self.panel_title_font,
        )
        title.pack(pady=(6, 2))

        desc = tk.Label(
            self.left_panel,
            text="实时采集手势 / 显示识别框 / 状态",
            bg="white",
            fg="#333333",
            font=self.small_font,
        )
        desc.pack(pady=(0, 3))

        self.video_label = tk.Label(
            self.left_panel,
            text="摄像头加载中...",
            bg="black",
            fg="white",
            font=self.default_font,
            width=20,
            height=7,
        )
        self.video_label.pack(padx=4, pady=3, fill="both", expand=True)

        self.sign_label = tk.Label(
            self.left_panel,
            text="识别结果：等待识别...",
            bg="white",
            fg="#007000",
            font=self.default_font,
            wraplength=190,
            justify="center",
        )
        self.sign_label.pack(pady=(2, 5))

    def build_middle_panel(self):
        title = tk.Label(
            self.middle_panel,
            text="中区：对话文本",
            bg="white",
            fg="#111111",
            font=self.panel_title_font,
        )
        title.pack(pady=(6, 2))

        desc = tk.Label(
            self.middle_panel,
            text="听障人士消息 / 工作人员回复",
            bg="white",
            fg="#333333",
            font=self.small_font,
        )
        desc.pack(pady=(0, 3))

        self.chat_box = tk.Text(
            self.middle_panel,
            bg="#fafafa",
            fg="#111111",
            font=self.default_font,
            bd=1,
            relief="solid",
            wrap="word",
            height=13,
        )
        self.chat_box.pack(padx=4, pady=3, fill="both", expand=True)
        self.chat_box.config(state="disabled")

        self.add_chat("系统", "等待 MQTT 连接与工作人员消息...")

    def build_right_panel(self):
        title = tk.Label(
            self.right_panel,
            text="右区：状态栏",
            bg="white",
            fg="#111111",
            font=self.panel_title_font,
        )
        title.pack(pady=(6, 2))

        desc = tk.Label(
            self.right_panel,
            text="MQTT连接 / 温度 / position / 光照值 / 风扇档位 / 置信度",
            bg="white",
            fg="#333333",
            font=self.small_font,
            wraplength=190,
            justify="center",
        )
        desc.pack(pady=(0, 3))

        self.status_frame = tk.Frame(self.right_panel, bg="white")
        self.status_frame.pack(fill="both", expand=True, padx=4, pady=3)

        self.status_labels = {}

        self.add_status_row("MQTT", "未连接")
        self.add_status_row("温度", "--")
        self.add_status_row("position", "--")
        self.add_status_row("光照值", "--")
        self.add_status_row("风扇档位", "--")
        self.add_status_row("置信度", "--")

    def add_status_row(self, key, value):
        row = tk.Frame(self.status_frame, bg="white")
        row.pack(fill="x", pady=3)

        k = tk.Label(
            row,
            text=f"{key}：",
            bg="white",
            fg="#111111",
            font=self.default_font,
            width=6,
            anchor="w",
        )
        k.pack(side="left")

        v = tk.Label(
            row,
            text=value,
            bg="white",
            fg="#0066cc",
            font=self.default_font,
            anchor="w",
        )
        v.pack(side="left", fill="x", expand=True)

        self.status_labels[key] = v

    def add_chat(self, speaker, text):
        self.chat_box.config(state="normal")

        now = time.strftime("%H:%M:%S")

        if speaker == "工作人员":
            prefix = f"[{now}] 工作人员：\n"
        elif speaker == "听障人士":
            prefix = f"[{now}] 听障人士：\n"
        else:
            prefix = f"[{now}] {speaker}：\n"

        self.chat_box.insert("end", prefix)
        self.chat_box.insert("end", text + "\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def start_camera(self):
        if cv2 is None:
            self.video_label.config(text="未安装 cv2，无法显示摄像头")
            return

        try:
            self.cap = cv2.VideoCapture(CAMERA_PIPELINE, cv2.CAP_GSTREAMER)

            if not self.cap.isOpened():
                self.video_label.config(text="摄像头未打开\n/dev/video31")
                self.cap = None
        except Exception as e:
            self.video_label.config(text=f"摄像头错误：{e}")
            self.cap = None

    def update_camera_loop(self):
        if not self.running:
            return

        if self.cap is not None:
            try:
                ret, frame = self.cap.read()

                if ret and frame is not None:
                    frame = cv2.resize(frame, (200, 135))
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                    ok, ppm = cv2.imencode(".ppm", frame)

                    if ok:
                        self.video_photo = tk.PhotoImage(
                            data=ppm.tobytes(),
                            format="PPM"
                        )
                        self.video_label.config(image=self.video_photo, text="")
                else:
                    self.video_label.config(text="摄像头读取失败")
            except Exception as e:
                self.video_label.config(text=f"摄像头异常：{e}")

        self.root.after(40, self.update_camera_loop)

    def start_mqtt(self):
        if self.mqtt_client is None:
            self.add_chat("系统", "未安装 paho-mqtt，MQTT 不可用")
            return

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        self.mqtt_client.on_message = self.on_mqtt_message

        threading.Thread(target=self.mqtt_connect_loop, daemon=True).start()

    def mqtt_connect_loop(self):
        while self.running:
            try:
                self.mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                self.add_chat("系统", f"MQTT 连接失败：{e}")
                time.sleep(3)

    def on_mqtt_connect(self, client, userdata, flags, rc):
        global mqtt_connected

        if rc == 0:
            mqtt_connected = True
            client.subscribe(TOPIC_PC_TO_BOARD)
            client.publish(TOPIC_BOARD_STATUS, "board_three_ui online")
            self.add_chat("系统", "MQTT 已连接")
        else:
            mqtt_connected = False
            self.add_chat("系统", f"MQTT 连接失败 rc={rc}")

    def on_mqtt_disconnect(self, client, userdata, rc):
        global mqtt_connected
        mqtt_connected = False

    def on_mqtt_message(self, client, userdata, msg):
        global latest_staff_text

        try:
            text = msg.payload.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = str(msg.payload)

        if not text:
            return

        latest_staff_text = text

        self.root.after(0, lambda: self.add_chat("工作人员", text))

    def update_status_loop(self):
        global latest_light, latest_position, latest_fan

        mqtt_text = "已连接" if mqtt_connected else "未连接"
        self.status_labels["MQTT"].config(text=f"{mqtt_text} {BROKER_IP}:{BROKER_PORT}")

        self.status_labels["温度"].config(text=read_board_temp())

        latest_position = read_first_existing(POSITION_FILES, default="--")
        self.status_labels["position"].config(text=latest_position)

        latest_light = read_light_value()
        self.status_labels["光照值"].config(text=latest_light)

        latest_fan = read_first_existing(FAN_FILES, default="--")
        self.status_labels["风扇档位"].config(text=latest_fan)

        self.status_labels["置信度"].config(text=str(latest_confidence))

        self.sign_label.config(text=f"识别结果：{latest_sign_text}")

        # 定时上报状态
        try:
            if self.mqtt_client is not None and mqtt_connected:
                payload = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "temp": read_board_temp(),
                    "position": latest_position,
                    "light": latest_light,
                    "fan": latest_fan,
                    "confidence": latest_confidence,
                }
                self.mqtt_client.publish(
                    TOPIC_BOARD_STATUS,
                    json.dumps(payload, ensure_ascii=False)
                )
        except Exception:
            pass

        self.root.after(1000, self.update_status_loop)

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
    app = ThreePartUI()
    app.run()


if __name__ == "__main__":
    main()
