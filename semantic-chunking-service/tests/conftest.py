import os
import hashlib

import numpy as np

os.environ.setdefault("EMBEDDING_ENDPOINT", "http://localhost:9")
os.environ.setdefault("EMBEDDING_MODEL", "test-model")


def fake_embed(texts):
    out = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        v = np.frombuffer(h, dtype=np.uint8).astype(np.float32)[:16]
        out.append((v / (np.linalg.norm(v) or 1)).tolist())
    return out
