import numpy as np
from rknnlite.api import RKNNLite

MODEL = "sign_tcn_288_hand102_64x102.rknn"

rknn = RKNNLite()

print("load:", MODEL)
ret = rknn.load_rknn(MODEL)
print("load_rknn ret =", ret)
if ret != 0:
    raise RuntimeError("load_rknn failed")

ret = rknn.init_runtime()
print("init_runtime ret =", ret)
if ret != 0:
    raise RuntimeError("init_runtime failed")

x = np.zeros((1, 64, 102), dtype=np.float32)
outs = rknn.inference(inputs=[x])

print("outputs:", type(outs), len(outs))
for i, out in enumerate(outs):
    print("output", i, "shape:", out.shape, "dtype:", out.dtype)
    print("top5:", np.argsort(out[0])[-5:][::-1])

rknn.release()
print("✅ 288 hand102 RKNN 测试成功")
