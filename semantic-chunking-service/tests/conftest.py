import os
import hashlib

import numpy as np

os.environ["LOG_LEVEL"] = "INFO"
os.environ["CHUNKER_DEFAULT_STRATEGY_VERSION"] = "cluster-semantic@1"
os.environ["EMBEDDING_ENDPOINT"] = "http://localhost:9"
os.environ["EMBEDDING_MODEL"] = "test-model"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["EMBEDDING_BATCH_SIZE"] = "64"
os.environ["EMBEDDING_TIMEOUT_S"] = "60"
os.environ["CHUNKER_MAX_SEGMENTS_DP"] = "10000"
os.environ["REWARD_CACHE_MAX_SIZE"] = "100000"


def fake_embed(texts):
    out = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        v = np.frombuffer(h, dtype=np.uint8).astype(np.float32)[:16]
        out.append((v / (np.linalg.norm(v) or 1)).tolist())
    return out
