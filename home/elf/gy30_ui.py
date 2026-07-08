import sys
import cv2
import json
import time
import paho.mqtt.client as mqtt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QTextEdit, QPushButton, 
    QVBoxLayout, QHBoxLayout
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QFont


class GovSignUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("多模态无障碍政务交互终端")
        self.resize(900, 520)
        
        self.camera = None
        self.client = None
        self.broker_ip = "192.168.107.122"   # 你的电脑IP
        
        self.init_ui()
        self.init_camera()
        self.init_mqtt()

    def init_ui(self):
        self.setStyleSheet("""
            QWidget { background-color: #f8fafc; font-family: Microsoft YaHei, Arial; }
            QLabel, QTextEdit, QPushButton { font-size: 15px; }
            QPushButton {
                background-color: #1677ff; color: white; border-radius: 5px; 
                padding: 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #0958d9; }
        """)

        title = QLabel("多模态无障碍政务交互终端")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        title.setStyleSheet("color: #0f172a; padding: 6px; background: white; border-radius: 8px;")

        # 左侧：摄像头
        self.camera_label = QLabel("摄像头启动中...")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setStyleSheet("background-color: #1e2937; color: white; border-radius: 10px;")

        self.raw_status = QLabel("当前识别：等待手语输入")
        self.raw_status.setAlignment(Qt.AlignCenter)
        self.raw_status.setStyleSheet("background-color: #e0f2fe; color: #0369a1; border-radius: 8px; padding: 8px; font-weight: bold;")

        left = QVBoxLayout()
        left.addWidget(QLabel("① 手语采集区"))
        left.addWidget(self.camera_label, 6)
        left.addWidget(self.raw_status, 1)

        # 中间：对话历史记录
        mid = QVBoxLayout()
        mid.addWidget(QLabel("② 对话历史记录"))
        self.chat_box = QTextEdit()
        self.chat_box.setReadOnly(True)
        self.chat_box.append("系统：欢迎使用无障碍政务交互终端")
        self.chat_box.append("系统：已连接工作人员端...")
        mid.addWidget(self.chat_box, 8)

        self.sim_btn = QPushButton("模拟手语识别")
        self.sim_btn.clicked.connect(self.simulate_sign)
        mid.addWidget(self.sim_btn)

        # 右侧：系统状态
        right = QVBoxLayout()
        right.addWidget(QLabel("③ 系统状态"))
        self.mqtt_label = self.make_status("MQTT", "连接中...", "#f59e0b")
        self.temp_label = self.make_status("温度", "37.8℃", "#f59e0b")
        self.conf_label = self.make_status("置信度", "--", "#8b5cf6")
        self.pos_label = self.make_status("Position", "正常", "#10b981")
        self.fan_label = self.make_status("风扇", "自动", "#64748b")

        for label in [self.mqtt_label, self.temp_label, self.conf_label, self.pos_label, self.fan_label]:
            right.addWidget(label)
        right.addStretch()

        content = QHBoxLayout()
        content.setSpacing(8)
        content.setContentsMargins(6, 4, 6, 6)
        content.addLayout(left, 4)
        content.addLayout(mid, 5)
        content.addLayout(right, 3)

        main = QVBoxLayout()
        main.setContentsMargins(8, 5, 8, 6)
        main.addWidget(title)
        main.addLayout(content)
        self.setLayout(main)

    def make_status(self, name, value, color):
        label = QLabel(f"{name}：{value}")
        label.setStyleSheet(f"background:white; padding:8px; border-radius:6px; border:2px solid {color}; font-weight:bold; font-size:14px;")
        return label

    # ====================== MQTT ======================
    def init_mqtt(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

        try:
            self.client.connect(self.broker_ip, 1883, 60)
            self.client.loop_start()
        except Exception as e:
            self.mqtt_label.setText("MQTT：连接失败")
            print("MQTT 连接错误:", e)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.mqtt_label.setText("MQTT：已连接")
            self.mqtt_label.setStyleSheet("background:white; padding:8px; border-radius:6px; border:2px solid #10b981; font-weight:bold;")
            # 订阅工作人员回复
            client.subscribe("staff/down/reply", qos=1)
            print("MQTT 订阅成功")
        else:
            self.mqtt_label.setText(f"MQTT：连接失败({rc})")

    def on_disconnect(self, client, userdata, rc):
        self.mqtt_label.setText("MQTT：已断开")

    def on_message(self, client, userdata, msg):
        """接收工作人员回复"""
        try:
            data = json.loads(msg.payload.decode('utf-8'))
            text = data.get("text", str(msg.payload.decode('utf-8')))
            self.chat_box.append(f"工作人员：{text}")
            self.chat_box.moveCursor(Qt.End)
        except Exception:
            text = msg.payload.decode('utf-8', errors='ignore')
            self.chat_box.append(f"工作人员：{text}")

    # ====================== 其他功能 ======================
    def init_camera(self):
        self.camera = cv2.VideoCapture(0)
        if not self.camera.isOpened():
            self.camera_label.setText("摄像头打开失败")
            return
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_camera)
        self.timer.start(70)

    def update_camera(self):
        ret, frame = self.camera.read()
        if not ret: return
        frame = cv2.flip(frame, 1)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame.shape[:2]
        qimg = QImage(frame.data, w, h, w*3, QImage.Format_RGB888)
        self.camera_label.setPixmap(QPixmap.fromImage(qimg).scaled(
            self.camera_label.width(), self.camera_label.height(), 
            Qt.KeepAspectRatio, Qt.FastTransformation))

    def simulate_sign(self):
        """模拟手语识别并发送到工作人员端"""
        text = "我要办理社保业务"
        self.raw_status.setText(f"当前识别：{text}")
        self.conf_label.setText("置信度：0.93")
        self.chat_box.append(f"听障用户：{text}")
        self.chat_box.moveCursor(Qt.End)

        # 发送到工作人员电脑
        if self.client and self.client.is_connected():
            msg = {
                "text": text,
                "time": time.strftime("%H:%M:%S"),
                "from": "board"
            }
            self.client.publish("board/up/message", json.dumps(msg, ensure_ascii=False), qos=1)
            print("已发送识别结果到工作人员端")

    def closeEvent(self, event):
        if self.camera:
            self.camera.release()
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = GovSignUI()
    ui.show()
    sys.exit(app.exec_())
