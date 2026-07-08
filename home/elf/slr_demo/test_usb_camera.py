import cv2
import time

CAMERA = "/dev/video52"

cap = cv2.VideoCapture(CAMERA, cv2.CAP_V4L2)

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)

if not cap.isOpened():
    print("打开摄像头失败:", CAMERA)
    exit(1)

print("摄像头打开成功:", CAMERA)

for i in range(50):
    ret, frame = cap.read()
    print("frame", i, "ret=", ret, "shape=", None if frame is None else frame.shape)

    if ret and frame is not None:
        cv2.imwrite("/tmp/usb_camera_test.jpg", frame)
        print("已保存测试图片: /tmp/usb_camera_test.jpg")
        break

    time.sleep(0.1)

cap.release()
