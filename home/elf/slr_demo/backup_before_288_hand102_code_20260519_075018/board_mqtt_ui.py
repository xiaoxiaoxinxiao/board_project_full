#!/usr/bin/env python3
import sys
import json
import time
import traceback
import paho.mqtt.client as mqtt

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QTextEdit, QVBoxLayout,
    QHBoxLayout, QLineEdit, QPushButton, QGridLayout
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont, QTextCursor


BROKER_IP = "192.168.107.147"
BROKER_PORT = 1883

TOPIC_REPLY = "staff/down/reply"
TOPIC_ALERT = "staff/down/alert"
TOPIC_BOARD_MSG = "board/up/message"
TOPIC_LIGHT_ALERT = "board/up/light_alert"

LIGHT_VALUE_FILE = "/home/elf/gy30_light_value.txt"

LIGHT_LOW_THRESHOLD = 60.0
LIGHT_HIGH_THRESHOLD = 150.0
ALERT_REPEAT_INTERVAL = 10.0


class MqttSignal(QObject):
    reply_received = pyqtSignal(object)
    alert_received = pyqtSignal(object)
    mqtt_status = pyqtSignal(str)


class BoardMqttUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("听障人士端 - MQTT 交互界面")
        self.resize(1024, 600)

        self.signal = MqttSignal()
        self.signal.reply_received.connect(self.handle_staff_reply)
        self.signal.alert_received.connect(self.handle_staff_alert)
        self.signal.mqtt_status.connect(self.update_mqtt_status)

        self.client = None
        self.last_light_state = None
        self.last_light_alert_time = 0.0

        self.init_ui()
        self.init_mqtt()
        self.init_light_timer()

    def init_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #f2f4f8;
                font-family: WenQuanYi Zen Hei, Noto Sans CJK SC, Arial;
            }
            QLabel {
                color: #111827;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 10px;
                font-size: 18px;
                padding: 10px;
            }
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 10px;
                font-size: 18px;
                padding: 10px;
            }
            QPushButton {
                background-color: #2563eb;
                color: white;
                border-radius: 10px;
                font-size: 17px;
                font-weight: bold;
                padding: 10px;
            }
        """)

        title = QLabel("听障人士端")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("WenQuanYi Zen Hei", 22, QFont.Bold))
        title.setFixedHeight(52)
        title.setStyleSheet("background-color:#ffffff;border-radius:12px;")

        self.mqtt_label = QLabel("MQTT：连接中")
        self.mqtt_label.setAlignment(Qt.AlignCenter)
        self.mqtt_label.setFont(QFont("WenQuanYi Zen Hei", 16, QFont.Bold))
        self.mqtt_label.setFixedHeight(42)
        self.mqtt_label.setStyleSheet("""
            background-color:#e0f2fe;
            color:#075985;
            border-radius:10px;
        """)

        self.light_label = QLabel("光照状态：等待 GY-30 数据")
        self.light_label.setAlignment(Qt.AlignCenter)
        self.light_label.setWordWrap(True)
        self.light_label.setFont(QFont("WenQuanYi Zen Hei", 16, QFont.Bold))
        self.light_label.setFixedHeight(72)
        self.light_label.setStyleSheet("""
            background-color:#ffffff;
            color:#111827;
            border-radius:10px;
            padding:8px;
        """)

        self.alert_label = QLabel("报警状态：正常")
        self.alert_label.setAlignment(Qt.AlignCenter)
        self.alert_label.setWordWrap(True)
        self.alert_label.setFont(QFont("WenQuanYi Zen Hei", 16, QFont.Bold))
        self.alert_label.setFixedHeight(80)
        self.alert_label.setStyleSheet("""
            background-color:#dcfce7;
            color:#166534;
            border-radius:10px;
            padding:8px;
        """)

        quick_title = QLabel("快捷发送")
        quick_title.setFont(QFont("WenQuanYi Zen Hei", 16, QFont.Bold))

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("请输入要发送给工作人员的内容...")
        self.input_box.returnPressed.connect(self.send_to_staff)

        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self.send_to_staff)

        quick_grid = QGridLayout()
        quick_texts = [
            "我需要帮助",
            "请稍等",
            "我看不清楚",
            "请再说一遍",
            "我要办理业务",
            "谢谢"
        ]

        for i, text in enumerate(quick_texts):
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, t=text: self.send_quick(t))
            quick_grid.addWidget(btn, i // 2, i % 2)

        input_layout = QHBoxLayout()
        input_layout.addWidget(self.input_box, 4)
        input_layout.addWidget(self.send_btn, 1)

        left_layout = QVBoxLayout()
        left_layout.addWidget(title)
        left_layout.addWidget(self.mqtt_label)
        left_layout.addWidget(self.light_label)
        left_layout.addWidget(self.alert_label)
        left_layout.addWidget(quick_title)
        left_layout.addLayout(quick_grid)
        left_layout.addLayout(input_layout)
        left_layout.addStretch()

        dialog_title = QLabel("对话记录")
        dialog_title.setAlignment(Qt.AlignCenter)
        dialog_title.setFont(QFont("WenQuanYi Zen Hei", 20, QFont.Bold))
        dialog_title.setFixedHeight(46)
        dialog_title.setStyleSheet("background-color:#ffffff;border-radius:12px;")

        self.chat_box = QTextEdit()
        self.chat_box.setReadOnly(True)
        self.chat_box.setFont(QFont("WenQuanYi Zen Hei", 19))
        self.chat_box.append("系统：对话区已启动。")

        right_layout = QVBoxLayout()
        right_layout.addWidget(dialog_title)
        right_layout.addWidget(self.chat_box)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(10)
        main_layout.addLayout(left_layout, 4)
        main_layout.addLayout(right_layout, 6)

        self.setLayout(main_layout)

    def init_mqtt(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

        try:
            self.client.connect(BROKER_IP, BROKER_PORT, 60)
            self.client.loop_start()
        except Exception as e:
            self.mqtt_label.setText("MQTT：连接失败")
            self.chat_box.append("系统：MQTT 连接失败：" + str(e))

    def init_light_timer(self):
        self.light_timer = QTimer()
        self.light_timer.timeout.connect(self.check_light_and_publish)
        self.light_timer.start(1000)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.signal.mqtt_status.emit("已连接")
            client.subscribe(TOPIC_REPLY, qos=1)
            client.subscribe(TOPIC_ALERT, qos=1)
        else:
            self.signal.mqtt_status.emit("连接失败 rc=%s" % rc)

    def on_disconnect(self, client, userdata, rc):
        self.signal.mqtt_status.emit("已断开")

    def parse_payload(self, raw):
        raw = raw.strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return {"text": str(data), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        except Exception:
            return {"text": raw, "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    def on_message(self, client, userdata, msg):
        try:
            raw = msg.payload.decode("utf-8", errors="ignore")
            data = self.parse_payload(raw)

            if msg.topic == TOPIC_REPLY:
                self.signal.reply_received.emit(data)
            elif msg.topic == TOPIC_ALERT:
                self.signal.alert_received.emit(data)

        except Exception as e:
            print("on_message error:", e)
            traceback.print_exc()

    def update_mqtt_status(self, text):
        self.mqtt_label.setText("MQTT：" + text)

    def append_chat(self, role, text):
        if not text:
            return
        self.chat_box.append("%s：%s" % (role, text))
        self.chat_box.moveCursor(QTextCursor.End)

    def handle_staff_reply(self, data):
        text = str(data.get("text", "")).strip()
        if text == "":
            text = "收到空消息"
        self.append_chat("工作人员", text)

    def handle_staff_alert(self, data):
        level = str(data.get("level", "normal"))
        text = str(data.get("text", "设备状态正常"))

        if level in ["high", "low", "warning", "danger"]:
            self.alert_label.setStyleSheet("""
                background-color:#fee2e2;
                color:#991b1b;
                border-radius:10px;
                padding:8px;
                font-size:16px;
                font-weight:bold;
            """)
            self.alert_label.setText("报警：\n" + text)
        else:
            self.alert_label.setStyleSheet("""
                background-color:#dcfce7;
                color:#166534;
                border-radius:10px;
                padding:8px;
                font-size:16px;
                font-weight:bold;
            """)
            self.alert_label.setText("报警状态：正常")

    def send_quick(self, text):
        self.input_box.setText(text)
        self.send_to_staff()

    def send_to_staff(self):
        text = self.input_box.text().strip()
        if not text:
            return

        msg = {
            "text": text,
            "from": "board",
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        try:
            self.client.publish(
                TOPIC_BOARD_MSG,
                json.dumps(msg, ensure_ascii=False),
                qos=1
            )
            self.append_chat("您", text)
            self.input_box.clear()
        except Exception as e:
            self.chat_box.append("系统：发送失败：" + str(e))

    def read_lux_from_file(self):
        try:
            with open(LIGHT_VALUE_FILE, "r") as f:
                line = f.read().strip()
            if not line:
                return None
            parts = line.split(",")
            return float(parts[0])
        except Exception:
            return None

    def get_light_state(self, lux):
        if lux < LIGHT_LOW_THRESHOLD:
            return "low"
        elif lux > LIGHT_HIGH_THRESHOLD:
            return "high"
        else:
            return "normal"

    def publish_light_alert(self, lux, state):
        now = time.time()

        if state == "low":
            text = "光照数值过低：%.2f lx" % lux
            level = "low"
        elif state == "high":
            text = "光照数值过高：%.2f lx" % lux
            level = "high"
        else:
            text = "光照数值正常：%.2f lx" % lux
            level = "normal"

        state_changed = state != self.last_light_state
        repeat_due = now - self.last_light_alert_time >= ALERT_REPEAT_INTERVAL

        if not state_changed and not (state in ["low", "high"] and repeat_due):
            return

        msg = {
            "name": "光照",
            "value": round(lux, 2),
            "unit": "lx",
            "state": state,
            "level": level,
            "text": text,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        try:
            self.client.publish(
                TOPIC_LIGHT_ALERT,
                json.dumps(msg, ensure_ascii=False),
                qos=1
            )
            self.last_light_state = state
            self.last_light_alert_time = now
        except Exception:
            pass

    def check_light_and_publish(self):
        lux = self.read_lux_from_file()

        if lux is None:
            self.light_label.setText("光照状态：未读取到 GY-30 数据")
            return

        state = self.get_light_state(lux)

        if state == "low":
            self.light_label.setStyleSheet("""
                background-color:#fee2e2;
                color:#991b1b;
                border-radius:10px;
                padding:8px;
                font-size:16px;
                font-weight:bold;
            """)
            self.light_label.setText("光照：%.2f lx\n状态：数值过低" % lux)

        elif state == "high":
            self.light_label.setStyleSheet("""
                background-color:#fee2e2;
                color:#991b1b;
                border-radius:10px;
                padding:8px;
                font-size:16px;
                font-weight:bold;
            """)
            self.light_label.setText("光照：%.2f lx\n状态：数值过高" % lux)

        else:
            self.light_label.setStyleSheet("""
                background-color:#dcfce7;
                color:#166534;
                border-radius:10px;
                padding:8px;
                font-size:16px;
                font-weight:bold;
            """)
            self.light_label.setText("光照：%.2f lx\n状态：正常" % lux)

        self.publish_light_alert(lux, state)

    def closeEvent(self, event):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = BoardMqttUI()
    ui.show()
    sys.exit(app.exec_())
