import cv2
import time
import numpy as np
from collections import deque
from rknnlite.api import RKNNLite

BODY_MODEL = "body_pose.rknn"
HAND_MODEL = "hand_pose.rknn"
SLR_MODEL = "tcn_slr_rv1126.rknn"

IMG_SIZE = 640
SEQ_LEN = 64
SLR_INTERVAL = 8

SHOW_W = 800
SHOW_H = 480

CAMERA_PIPELINE = (
    "v4l2src device=/dev/video31 ! "
    "video/x-raw,format=NV12,width=640,height=480 ! "
    "videoconvert ! "
    "video/x-raw,format=BGR ! "
    "appsink max-buffers=1 drop=true sync=false"
)

BODY_IDXS = [0, 5, 6, 7, 8, 9]
HAND_NUM = 17

seq_buffer = deque(maxlen=SEQ_LEN)


def load_rknn(path):
    rknn = RKNNLite()

    ret = rknn.load_rknn(path)
    if ret != 0:
        raise RuntimeError(f"load failed: {path}")

    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError(f"init runtime failed: {path}")

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
    if output is None:
        return None, 0.0

    out = output

    if isinstance(out, list):
        if len(out) == 0 or out[0] is None:
            return None, 0.0
        out = out[0]

    if out is None:
        return None, 0.0

    if out.ndim == 3:
        out = out[0]

    if out.shape[0] < out.shape[1]:
        out = out.T

    need_dim = 5 + num_kpts * 3
    if out.shape[1] < need_dim:
        return None, 0.0

    conf = out[:, 4]
    best = int(np.argmax(conf))
    best_score = float(conf[best])

    if best_score < 0.25:
        return None, best_score

    data = out[best]
    kpts = data[5:5 + num_kpts * 3].reshape(num_kpts, 3)

    xy = kpts[:, :2].astype(np.float32)

    xy[:, 0] = (xy[:, 0] - dw) / scale
    xy[:, 1] = (xy[:, 1] - dh) / scale

    xy[:, 0] = np.clip(xy[:, 0], 0, frame_w - 1)
    xy[:, 1] = np.clip(xy[:, 1], 0, frame_h - 1)

    return xy, best_score


def make_46_feature(hand_xy, body_xy, frame_w, frame_h):
    feat = []

    if hand_xy is None:
        feat.extend([0.0] * 34)
    else:
        hand_xy = hand_xy[:HAND_NUM]
        for x, y in hand_xy:
            feat.append(float(x) / frame_w)
            feat.append(float(y) / frame_h)

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


def draw_points(frame, hand_xy, body_xy):
    if hand_xy is not None:
        for x, y in hand_xy[:HAND_NUM]:
            cv2.circle(frame, (int(x), int(y)), 4, (0, 255, 0), -1)

    if body_xy is not None:
        for idx in BODY_IDXS:
            if idx < len(body_xy):
                x, y = body_xy[idx]
                cv2.circle(frame, (int(x), int(y)), 5, (255, 0, 0), -1)


def main():
    print("load RKNN models...")
    body_rknn = load_rknn(BODY_MODEL)
    hand_rknn = load_rknn(HAND_MODEL)
    slr_rknn = load_rknn(SLR_MODEL)

    print("open camera /dev/video31...")
    cap = cv2.VideoCapture(CAMERA_PIPELINE, cv2.CAP_GSTREAMER)

    if not cap.isOpened():
        print("camera open failed")
        return

    print("camera ok, start demo")

    pred_id = -1
    pred_score = 0.0
    frame_count = 0
    last_time = time.time()
    fps = 0.0

    cv2.namedWindow("SLR Demo", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("SLR Demo", SHOW_W, SHOW_H)

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                print("read frame failed")
                time.sleep(0.05)
                continue

            frame_count += 1
            frame_h, frame_w = frame.shape[:2]

            inp, scale, dw, dh = preprocess(frame)

            body_out = body_rknn.inference(inputs=[inp])
            hand_out = hand_rknn.inference(inputs=[inp])

            body_xy, body_score = parse_yolo_pose(
                body_out, 17, scale, dw, dh, frame_w, frame_h
            )

            hand_xy, hand_score = parse_yolo_pose(
                hand_out, 21, scale, dw, dh, frame_w, frame_h
            )

            feat46 = make_46_feature(hand_xy, body_xy, frame_w, frame_h)
            seq_buffer.append(feat46)

            if len(seq_buffer) == SEQ_LEN and frame_count % SLR_INTERVAL == 0:
                x = np.array(seq_buffer, dtype=np.float32)
                x = np.expand_dims(x, axis=0)

                out = slr_rknn.inference(inputs=[x])
                logits = out[0]

                prob = softmax(logits)
                pred_id = int(np.argmax(prob, axis=1)[0])
                pred_score = float(np.max(prob))

            now = time.time()
            dt = now - last_time
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)
            last_time = now

            draw_points(frame, hand_xy, body_xy)

            label_id = "------" if pred_id < 0 else f"{pred_id:06d}"

            cv2.rectangle(frame, (0, 0), (440, 170), (0, 0, 0), -1)

            cv2.putText(frame, f"ID: {label_id}", (20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)

            cv2.putText(frame, f"score: {pred_score:.3f}", (20, 75),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)

            cv2.putText(frame, f"buffer: {len(seq_buffer)}/{SEQ_LEN}", (20, 115),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2)

            cv2.putText(frame, f"FPS: {fps:.1f}", (20, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2)

            cv2.putText(frame, f"hand: {hand_score:.2f}", (460, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)

            cv2.putText(frame, f"body: {body_score:.2f}", (460, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 0, 0), 2)

            show = cv2.resize(frame, (SHOW_W, SHOW_H))
            cv2.imshow("SLR Demo", show)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

            if frame_count % 30 == 0:
                print(f"ID={label_id} score={pred_score:.3f} buffer={len(seq_buffer)}/{SEQ_LEN} FPS={fps:.1f}")

    except KeyboardInterrupt:
        print("stop")

    finally:
        cap.release()
        body_rknn.release()
        hand_rknn.release()
        slr_rknn.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
