import cv2
import os
import numpy as np
from rknnlite.api import RKNNLite


BODY_MODEL = "body_pose.rknn"
HAND_MODEL = "hand_pose.rknn"
SLR_MODEL = "sign_tcn_288_hand102_64x102.rknn"
DICT_PATH = "dictionary_288class.txt"

IMG_SIZE = 640
SEQ_LEN = 64

# 新模型输入维度：
# 左手21点*2 + 右手21点*2 + 身体9点*2 = 102
INPUT_DIM = 102
NUM_CLASSES = 288

# body_pose 17点里取这9个点：
# 0 nose, 9 mouth_left, 10 mouth_right,
# 11 left_shoulder, 12 right_shoulder,
# 13 left_elbow, 14 right_elbow,
# 15 left_wrist, 16 right_wrist
BODY_102_IDXS = [0, 9, 10, 11, 12, 13, 14, 15, 16]

# 板端当前 hand_pose 只解析出一只手时，默认放到 right hand 通道
# 如果实际识别左右手很怪，可以改成 "left"
SINGLE_HAND_POLICY = "right"


def load_dict(path):
    """
    dictionary_288class.txt 格式：
    new_label old_label word
    例如：
    186 000242 我
    """
    id2word = {}

    if not os.path.exists(path):
        print("字典不存在:", path)
        return id2word

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            parts = line.split()

            if len(parts) < 3:
                continue

            try:
                new_label = int(parts[0])
                word = "".join(parts[2:])
                id2word[new_label] = word
            except Exception:
                continue

    print("load dict:", path, "classes:", len(id2word))
    return id2word


def load_rknn(path):
    rknn = RKNNLite()

    ret = rknn.load_rknn(path)
    if ret != 0:
        raise RuntimeError("load rknn failed: " + path)

    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError("init runtime failed: " + path)

    print("load rknn ok:", path)
    return rknn


def letterbox(img, size=640):
    h, w = img.shape[:2]

    scale = min(size / w, size / h)

    nw = int(w * scale)
    nh = int(h * scale)

    resized = cv2.resize(img, (nw, nh))

    canvas = np.full((size, size, 3), 114, dtype=np.uint8)

    dw = (size - nw) / 2
    dh = (size - nh) / 2

    canvas[int(dh): int(dh) + nh, int(dw): int(dw) + nw] = resized

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
    兼容你原来的 body_pose.rknn / hand_pose.rknn 输出。
    返回 best detection 的关键点 xy，单位是原图像素坐标。
    """
    if output is None:
        return None, 0.0

    out = output

    if isinstance(out, list):
        if len(out) == 0 or out[0] is None:
            return None, 0.0
        out = out[0]

    out = np.asarray(out)

    if out.ndim == 3:
        out = out[0]

    # 转成 [N, D]
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

    # 从 letterbox 输入坐标还原到原图坐标
    xy[:, 0] = (xy[:, 0] - dw) / scale
    xy[:, 1] = (xy[:, 1] - dh) / scale

    xy[:, 0] = np.clip(xy[:, 0], 0, frame_w - 1)
    xy[:, 1] = np.clip(xy[:, 1], 0, frame_h - 1)

    return xy, best_score


def _normalize_points(left_pts, right_pts, body_pts):
    zero_left = np.zeros((21, 2), dtype=np.float32)
    zero_right = np.zeros((21, 2), dtype=np.float32)
    zero_body = np.zeros((9, 2), dtype=np.float32)

    visible = []

    if left_pts is not None:
        visible.append(left_pts)

    if right_pts is not None:
        visible.append(right_pts)

    if body_pts is not None:
        visible.append(body_pts)

    if len(visible) == 0:
        return zero_left, zero_right, zero_body

    all_pts = np.concatenate(visible, axis=0)

    center = all_pts.mean(axis=0)

    min_xy = all_pts.min(axis=0)
    max_xy = all_pts.max(axis=0)
    scale = max(max_xy[0] - min_xy[0], max_xy[1] - min_xy[1])

    # body_pts 的第3/4个是左右肩
    if body_pts is not None and body_pts.shape[0] >= 5:
        lsho = body_pts[3]
        rsho = body_pts[4]

        dist = np.linalg.norm(lsho - rsho)

        if dist > 1e-6:
            center = (lsho + rsho) / 2.0
            scale = dist

    if scale < 1e-6:
        scale = 1.0

    if left_pts is not None:
        left_out = (left_pts - center) / scale
    else:
        left_out = zero_left

    if right_pts is not None:
        right_out = (right_pts - center) / scale
    else:
        right_out = zero_right

    if body_pts is not None:
        body_out = (body_pts - center) / scale
    else:
        body_out = zero_body

    return left_out, right_out, body_out


def make_102_feature(hand_xy, body_xy, frame_w, frame_h):
    """
    输出 shape = (102,)
    注意：当前板端 hand_pose 旧流程只返回一只手。
    所以这里把这一只手默认放到 right hand 通道。
    """
    left_pts = None
    right_pts = None
    body_pts = None

    if hand_xy is not None:
        hand_xy = np.asarray(hand_xy, dtype=np.float32)

        if hand_xy.shape[0] >= 21:
            hand21 = hand_xy[:21]

            if SINGLE_HAND_POLICY == "left":
                left_pts = hand21
            else:
                right_pts = hand21

    if body_xy is not None:
        body_xy = np.asarray(body_xy, dtype=np.float32)

        pts = []

        for idx in BODY_102_IDXS:
            if idx < len(body_xy):
                pts.append(body_xy[idx])
            else:
                pts.append([0.0, 0.0])

        body_pts = np.asarray(pts, dtype=np.float32)

    left_norm, right_norm, body_norm = _normalize_points(
        left_pts,
        right_pts,
        body_pts,
    )

    feat = np.concatenate([
        left_norm.flatten(),
        right_norm.flatten(),
        body_norm.flatten(),
    ], axis=0)

    feat = np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0)
    feat = feat.astype(np.float32)

    if feat.shape[0] != 102:
        fixed = np.zeros((102,), dtype=np.float32)
        n = min(102, feat.shape[0])
        fixed[:n] = feat[:n]
        feat = fixed

    return feat


# 为了兼容 board_three_ui.py 原来的 import 名字：
# board_three_ui.py 仍然 import make_46_feature，但这里实际返回 102维。
def make_46_feature(hand_xy, body_xy, frame_w, frame_h):
    return make_102_feature(hand_xy, body_xy, frame_w, frame_h)


def softmax(x):
    x = np.asarray(x, dtype=np.float32)
    x = x - np.max(x, axis=-1, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=-1, keepdims=True)
