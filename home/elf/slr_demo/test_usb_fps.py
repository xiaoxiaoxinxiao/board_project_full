import cv2
import time

CAM = "/dev/video52"

cap = cv2.VideoCapture(CAM, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("camera open failed")
    exit(1)

cnt = 0
t0 = time.time()

while time.time() - t0 < 5:
    ret, frame = cap.read()
    if ret:
        cnt += 1

cap.release()

print("camera only fps =", cnt / 5.0)
