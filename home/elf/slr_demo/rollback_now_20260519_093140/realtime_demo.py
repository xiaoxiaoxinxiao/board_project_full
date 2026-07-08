import os
import cv2
import numpy as np
from rknnlite.api import RKNNLite


BODY_MODEL = "body_pose.rknn"
HAND_MODEL = "hand_pose.rknn"

# 必须使用你的新模型
SLR_MODEL = "sign_tcn_288_hand102_64x102.rknn"
DICT_PATH = "dictionary_288class.txt"

IMG_SIZE = 640
SEQ_LEN = 64
ACTION_FRAMES = 24

INPUT_DIM = 102
NUM_CLASSES = 288

# 288模型输出修正
BANNED_WORDS = {"瘦"}

LABEL_REMAP = {
    168: 186,   # 老人 -> 我
    105: 99,    # 多 -> 有
    273: 80,    # 经验 -> 困难
}

BOARD_CONF_THRESHOLD = 0.25

# body_pose 17点里取9点，和电脑端训练时一致
BODY_102_IDXS = [0, 9, 10, 11, 12, 13, 14, 15, 16]

# 如果只检测到一只手，又无法根据身体手腕判断，就默认放右手通道
SINGLE_HAND_POLICY = "right"

# 无身体手腕辅助时，正面画面里人的左手一般在图像右边
SUBJECT_LEFT_IS_IMAGE_RIGHT = True


def load_dict(path):
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
        raise RuntimeError("load_rknn failed: " + path)

    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError("init_runtime failed: " + path)

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


def _format_yolo_output(output):
    if output is None:
        return None

    if isinstance(output, list):
        if len(output) == 0 or output[0] is None:
            return None
        output = output[0]

    out = np.asarray(output)

    if out.ndim == 3:
        out = out[0]

    if out.ndim != 2:
        return None

    # 常见形状: [D, N]，转成 [N, D]
    if out.shape[0] < out.shape[1]:
        out = out.T

    return out


def _restore_xy_from_letterbox(xy, scale, dw, dh, frame_w, frame_h):
    xy = xy.astype(np.float32)

    # 如果模型输出是0~1归一化坐标，先乘到640
    if np.nanmax(xy) <= 2.0:
        xy = xy * IMG_SIZE

    xy[:, 0] = (xy[:, 0] - dw) / scale
    xy[:, 1] = (xy[:, 1] - dh) / scale

    xy[:, 0] = np.clip(xy[:, 0], 0, frame_w - 1)
    xy[:, 1] = np.clip(xy[:, 1], 0, frame_h - 1)

    return xy


def parse_pose_best(output, num_kpts, scale, dw, dh, frame_w, frame_h, conf_thres=0.20):
    out = _format_yolo_output(output)

    if out is None:
        return None, 0.0

    need_dim = 5 + num_kpts * 3

    if out.shape[1] < need_dim:
        return None, 0.0

    conf = out[:, 4]
    best = int(np.argmax(conf))
    score = float(conf[best])

    if score < conf_thres:
        return None, score

    data = out[best]
    kpts = data[5:5 + num_kpts * 3].reshape(num_kpts, 3)

    xy = kpts[:, :2]
    xy = _restore_xy_from_letterbox(xy, scale, dw, dh, frame_w, frame_h)

    return xy, score


def parse_pose_multi(output, num_kpts, scale, dw, dh, frame_w, frame_h, topk=2, conf_thres=0.18):
    out = _format_yolo_output(output)

    if out is None:
        return []

    need_dim = 5 + num_kpts * 3

    if out.shape[1] < need_dim:
        return []

    conf = out[:, 4]
    order = np.argsort(conf)[::-1]

    dets = []

    used_centers = []

    for idx in order:
        score = float(conf[idx])

        if score < conf_thres:
            continue

        data = out[int(idx)]
        kpts = data[5:5 + num_kpts * 3].reshape(num_kpts, 3)

        xy = kpts[:, :2]
        xy = _restore_xy_from_letterbox(xy, scale, dw, dh, frame_w, frame_h)

        center = xy.mean(axis=0)

        # 简单去重：两个候选中心太近，就认为是同一只手
        duplicate = False
        for c in used_centers:
            if np.linalg.norm(center - c) < 35:
                duplicate = True
                break

        if duplicate:
            continue

        used_centers.append(center)

        dets.append({
            "xy": xy.astype(np.float32),
            "score": score,
            "center": center.astype(np.float32),
        })

        if len(dets) >= topk:
            break

    return dets


def assign_hands_to_left_right(hand_dets, body_xy):
    left_pts = None
    right_pts = None

    if not hand_dets:
        return left_pts, right_pts

    # 如果有身体手腕，用身体手腕判断左右手
    if body_xy is not None and len(body_xy) >= 17:
        body_xy = np.asarray(body_xy, dtype=np.float32)

        left_wrist = body_xy[15]
        right_wrist = body_xy[16]

        if len(hand_dets) == 1:
            h = hand_dets[0]
            c = h["center"]

            d_left = np.linalg.norm(c - left_wrist)
            d_right = np.linalg.norm(c - right_wrist)

            if d_left <= d_right:
                left_pts = h["xy"][:21]
            else:
                right_pts = h["xy"][:21]

            return left_pts, right_pts

        # 两只手时，先分别算到左右腕距离，贪心分配
        h0 = hand_dets[0]
        h1 = hand_dets[1]

        c0 = h0["center"]
        c1 = h1["center"]

        d0_left = np.linalg.norm(c0 - left_wrist)
        d0_right = np.linalg.norm(c0 - right_wrist)
        d1_left = np.linalg.norm(c1 - left_wrist)
        d1_right = np.linalg.norm(c1 - right_wrist)

        cost_a = d0_left + d1_right
        cost_b = d0_right + d1_left

        if cost_a <= cost_b:
            left_pts = h0["xy"][:21]
            right_pts = h1["xy"][:21]
        else:
            left_pts = h1["xy"][:21]
            right_pts = h0["xy"][:21]

        return left_pts, right_pts

    # 没有身体点时，用图像左右判断
    if len(hand_dets) >= 2:
        sorted_hands = sorted(hand_dets[:2], key=lambda x: float(x["center"][0]))

        image_left = sorted_hands[0]["xy"][:21]
        image_right = sorted_hands[1]["xy"][:21]

        if SUBJECT_LEFT_IS_IMAGE_RIGHT:
            left_pts = image_right
            right_pts = image_left
        else:
            left_pts = image_left
            right_pts = image_right

        return left_pts, right_pts

    # 只有一只手且无身体点
    h = hand_dets[0]["xy"][:21]

    if SINGLE_HAND_POLICY == "left":
        left_pts = h
    else:
        right_pts = h

    return left_pts, right_pts


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

    # body_pts 的第3、第4个是左右肩
    if body_pts is not None and body_pts.shape[0] >= 5:
        lsho = body_pts[3]
        rsho = body_pts[4]

        dist = np.linalg.norm(lsho - rsho)

        if dist > 1e-6:
            center = (lsho + rsho) / 2.0
            scale = dist

    if scale < 1e-6:
        scale = 1.0

    left_out = (left_pts - center) / scale if left_pts is not None else zero_left
    right_out = (right_pts - center) / scale if right_pts is not None else zero_right
    body_out = (body_pts - center) / scale if body_pts is not None else zero_body

    return left_out, right_out, body_out


def make_102_feature(hand_dets, body_xy, frame_w, frame_h):
    left_pts, right_pts = assign_hands_to_left_right(hand_dets, body_xy)

    body_pts = None

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

    if feat.shape[0] != INPUT_DIM:
        fixed = np.zeros((INPUT_DIM,), dtype=np.float32)
        n = min(INPUT_DIM, feat.shape[0])
        fixed[:n] = feat[:n]
        feat = fixed

    return feat


# 兼容旧 board_three_ui.py 名字
def make_46_feature(hand_xy_or_dets, body_xy, frame_w, frame_h):
    if isinstance(hand_xy_or_dets, list):
        hand_dets = hand_xy_or_dets
    elif hand_xy_or_dets is None:
        hand_dets = []
    else:
        hand_dets = [{
            "xy": np.asarray(hand_xy_or_dets, dtype=np.float32),
            "score": 1.0,
            "center": np.asarray(hand_xy_or_dets, dtype=np.float32).mean(axis=0),
        }]

    return make_102_feature(hand_dets, body_xy, frame_w, frame_h)


def resample_seq_to_64(seq, out_len=64, input_dim=102):
    arr = np.asarray(seq, dtype=np.float32)

    if arr.ndim != 2:
        arr = arr.reshape(arr.shape[0], -1)

    if arr.shape[0] <= 0:
        return np.zeros((out_len, input_dim), dtype=np.float32)

    if arr.shape[1] != input_dim:
        fixed = np.zeros((arr.shape[0], input_dim), dtype=np.float32)
        d = min(input_dim, arr.shape[1])
        fixed[:, :d] = arr[:, :d]
        arr = fixed

    if arr.shape[0] == out_len:
        return arr.astype(np.float32)

    old_idx = np.linspace(0, arr.shape[0] - 1, arr.shape[0])
    new_idx = np.linspace(0, arr.shape[0] - 1, out_len)

    out = np.zeros((out_len, input_dim), dtype=np.float32)

    for d in range(input_dim):
        out[:, d] = np.interp(new_idx, old_idx, arr[:, d])

    return np.nan_to_num(out).astype(np.float32)


def softmax(x):
    x = np.asarray(x, dtype=np.float32)
    x = x - np.max(x, axis=-1, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=-1, keepdims=True)


def remap_label(label):
    label = int(label)
    return int(LABEL_REMAP.get(label, label))


def choose_label_from_logits(logits, id2word):
    prob = softmax(logits).reshape(-1)
    order = np.argsort(prob)[::-1]

    top_items = []

    for idx in order[:5]:
        idx = int(idx)
        show_idx = remap_label(idx)
        word = id2word.get(show_idx, "")
        score = float(prob[idx])

        top_items.append((show_idx, word, score, idx))

    chosen = None

    for show_idx, word, score, raw_idx in top_items:
        if not word:
            continue

        if word in BANNED_WORDS:
            continue

        chosen = (show_idx, word, score, raw_idx)
        break

    if chosen is None:
        return None, "", 0.0, top_items

    return chosen[0], chosen[1], chosen[2], top_items


def infer_slr_288(slr_rknn, seq_buffer, id2word):
    x = resample_seq_to_64(
        seq_buffer,
        out_len=SEQ_LEN,
        input_dim=INPUT_DIM,
    )

    x = np.expand_dims(x, axis=0).astype(np.float32)

    outs = slr_rknn.inference(inputs=[x])

    if outs is None or len(outs) == 0:
        return None, "", 0.0, []

    logits = np.asarray(outs[0]).reshape(-1)

    return choose_label_from_logits(logits, id2word)


# 兼容旧代码名称
parse_body_pose = parse_pose_best
parse_hand_pose = parse_pose_best
