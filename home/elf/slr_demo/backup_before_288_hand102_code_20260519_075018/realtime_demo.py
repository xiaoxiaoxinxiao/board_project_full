import cv2
import time
import numpy as np
from collections import deque
from rknnlite.api import RKNNLite

BODY_MODEL = "body_pose.rknn"
HAND_MODEL = "hand_pose.rknn"
SLR_MODEL = "sign_tcn_64x46.rknn"
DICT_PATH = "dictionary.txt"

IMG_SIZE = 640
SEQ_LEN = 64

# 你现在摄像头已经确认是 /dev/video52
CAMERA_PIPELINE = (
    "v4l2src device=/dev/video52 ! "
    "video/x-raw,format=NV12,width=640,height=480 ! "
    "videoconvert ! "
    "video/x-raw,format=BGR ! "
    "appsink drop=1 sync=false"
)

# 身体取 6 个点：鼻子、左肩、右肩、左肘、右肘、左手腕
BODY_IDXS = [0, 5, 6, 7, 8, 9]

# 手部取前 17 个点，对应训练数据 34维
HAND_NUM = 17

seq_buffer = deque(maxlen=SEQ_LEN)


def load_dict(path):
    id2word = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    idx = int(parts[0])
                    word = parts[1]
                    id2word[idx] = word
                except Exception:
                    continue

    return id2word


def load_rknn(path):
    rknn = RKNNLite()

    ret = rknn.load_rknn(path)
    if ret != 0:
        raise RuntimeError(f"加载RKNN失败: {path}")

    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError(f"NPU初始化失败: {path}")

    return rknn


def letterbox(img, size=640):
    h, w = img.shape[:2]
    scale = min(size / w, size / h)

    nw, nh = int(w * scale), int(h * scale)
    resized = cv2.resize(img, (nw, nh))

    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    dw = (size - nw) // 2
    dh = (size - nh) // 2
    canvas[dh:dh + nh, dw:dw + nw] = resized

    return canvas, scale, dw, dh


def preprocess(img):
    img, scale, dw, dh = letterbox(img, IMG_SIZE)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img.astype(np.float32) / 255.0
    img = img.transpose(2, 0, 1)
    img = np.expand_dims(img, axis=0)
    return img, scale, dw, dh


def parse_yolo_pose(output, num_kpts, scale, dw, dh, frame_w, frame_h):
    """
    body 输出一般是 (1,56,8400)
    hand 输出一般是 (1,68,8400)
    """
    out = output

    if isinstance(out, list):
        out = out[0]

    if out.ndim == 3:
        out = out[0]

    # [C, N] -> [N, C]
    if out.shape[0] < out.shape[1]:
        out = out.T

    if out.shape[1] < 5 + num_kpts * 3:
        return None

    conf = out[:, 4]
    best = int(np.argmax(conf))
    best_score = float(conf[best])

    if best_score < 0.25:
        return None

    data = out[best]
    kpts = data[5:5 + num_kpts * 3].reshape(num_kpts, 3)

    xy = kpts[:, :2].astype(np.float32)

    # 反 letterbox
    xy[:, 0] = (xy[:, 0] - dw) / scale
    xy[:, 1] = (xy[:, 1] - dh) / scale

    xy[:, 0] = np.clip(xy[:, 0], 0, frame_w - 1)
    xy[:, 1] = np.clip(xy[:, 1], 0, frame_h - 1)

    return xy


def make_46_feature(hand_xy, body_xy, frame_w, frame_h):
    """
    输出 46维：
    hand 17点 x,y = 34
    body 6点 x,y = 12
    """
    feat = []

    if hand_xy is None:
        feat.extend([0.0] * 34)
    else:
        hand_xy = hand_xy[:HAND_NUM]
        for x, y in hand_xy:
            feat.append(float(x) / frame_w)
            feat.append(float(y) / frame_h)

        # 不足17点补0
        while len(feat) < 34:
            feat.append(0.0)

    if body_xy is None:
        feat.extend([0.0] * 12)
    else:
        for idx in BODY_IDXS:
            if idx < len(body_xy):
                x, y = body_xy[idx]
                feat.append(float(x) / frame_w)
                feat.append(float(y) / frame_h)
            else:
                feat.extend([0.0, 0.0])

    feat = np.array(feat, dtype=np.float32)

    # 简单中心化：用左右肩作为中心
    body = feat[34:].reshape(-1, 2)
    if body.shape[0] >= 3:
        ls = body[1]
        rs = body[2]
        if np.any(ls != 0) and np.any(rs != 0):
            cx = (ls[0] + rs[0]) / 2.0
            cy = (ls[1] + rs[1]) / 2.0
            feat[0::2] -= cx
            feat[1::2] -= cy

    return feat


def softmax(x):
    x = x.astype(np.float32)
    x = x - np.max(x, axis=1, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=1, keepdims=True)


def main():
    print("加载 dictionary...")
    id2word = load_dict(DICT_PATH)

    print("加载 RKNN 模型...")
    body_rknn = load_rknn(BODY_MODEL)
    hand_rknn = load_rknn(HAND_MODEL)
    slr_rknn = load_rknn(SLR_MODEL)

    print("打开摄像头 /dev/video52 ...")
    cap = cv2.VideoCapture(CAMERA_PIPELINE, cv2.CAP_GSTREAMER)

    if not cap.isOpened():
        print("摄像头打开失败")
        print("请确认 /dev/video52 能用，并且 pic31.jpg 可以正常生成")
        return

    print("摄像头打开成功，开始实时识别")
    print("按 Ctrl + C 停止程序")

    last_word = ""
    last_print_time = 0.0
    frame_count = 0

    try:
        while True:
            ret, frame = cap.read()

            if not ret or frame is None:
                print("读取摄像头失败")
                time.sleep(0.1)
                continue

            frame_count += 1
            frame_h, frame_w = frame.shape[:2]

            inp, scale, dw, dh = preprocess(frame)

            body_out = body_rknn.inference(inputs=[inp])
            hand_out = hand_rknn.inference(inputs=[inp])

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
            seq_buffer.append(feat46)

            if len(seq_buffer) == SEQ_LEN:
                x = np.array(seq_buffer, dtype=np.float32)
                x = np.expand_dims(x, axis=0)  # (1,64,46)

                out = slr_rknn.inference(inputs=[x])
                logits = out[0]

                prob = softmax(logits)
                pred = int(np.argmax(prob, axis=1)[0])
                score = float(np.max(prob))

                if score > 0.35:
                    word = id2word.get(pred, "未知")
                    last_word = word

                    now = time.time()
                    if now - last_print_time > 0.5:
                        print(f"识别结果: {word}  ID={pred}  score={score:.3f}")
                        last_print_time = now

            if frame_count % 30 == 0:
                print(f"运行中... buffer={len(seq_buffer)}/{SEQ_LEN} last_word={last_word}")

    except KeyboardInterrupt:
        print("用户停止程序")

    finally:
        cap.release()
        body_rknn.release()
        hand_rknn.release()
        slr_rknn.release()
        print("程序已退出")


if __name__ == "__main__":
    main()
