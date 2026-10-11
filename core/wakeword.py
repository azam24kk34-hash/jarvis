"""Офлайн-детектор фрази «Hey Jarvis» на моделях openWakeWord (ONNX).
Працює повністю на комп'ютері: поки фразу не сказано, звук нікуди не надсилається.

Моделі (каталог assets/wakeword): melspectrogram.onnx, embedding_model.onnx, hey_jarvis_v0.1.onnx
з https://github.com/dscripka/openWakeWord (код Apache-2.0, моделі CC BY-NC-SA 4.0 — лише некомерційне використання).
Конвеєр повторює openwakeword.utils.AudioFeatures у потоковому режимі: 16 кГц, шматки по 1280 семплів (80 мс)."""
import collections
import os

import numpy as np

CHUNK = 1280          # 80 мс при 16 кГц
SAMPLE_RATE = 16000


class WakeWordDetector:
    def __init__(self, model_dir, model_name="hey_jarvis_v0.1.onnx", threshold=0.5):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        prov = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(os.path.join(model_dir, "melspectrogram.onnx"), opts, providers=prov)
        self.emb = ort.InferenceSession(os.path.join(model_dir, "embedding_model.onnx"), opts, providers=prov)
        self.ww = ort.InferenceSession(os.path.join(model_dir, model_name), opts, providers=prov)
        self.ww_input = self.ww.get_inputs()[0].name
        self.n_frames = self.ww.get_inputs()[0].shape[1]  # 16
        self.threshold = threshold
        self.reset()

    # -- як в openwakeword.AudioFeatures
    def _melspec(self, x):
        out = self.mel.run(None, {"input": x[None, :].astype(np.float32)})[0]
        return np.squeeze(out) / 10 + 2

    def _embeddings_batch(self, audio):
        spec = self._melspec(audio)
        wins = [spec[i:i + 76] for i in range(0, spec.shape[0], 8) if spec[i:i + 76].shape[0] == 76]
        batch = np.expand_dims(np.array(wins), -1).astype(np.float32)
        return self.emb.run(None, {"input_1": batch})[0].squeeze()

    def reset(self):
        self.raw = collections.deque(maxlen=SAMPLE_RATE * 10)
        self.melbuf = np.ones((76, 32), dtype=np.float32)
        self.feats = self._embeddings_batch(np.zeros(160000, dtype=np.int16))
        self.pending = np.zeros(0, dtype=np.int16)
        self.n_pred = 0

    def process(self, pcm16: bytes) -> float:
        """Подати шматок аудіо (int16 моно 16 кГц, будь-якої довжини). Повертає найвищий бал (0..1)."""
        data = np.concatenate([self.pending, np.frombuffer(pcm16, dtype=np.int16)])
        best = 0.0
        while len(data) >= CHUNK:
            chunk, data = data[:CHUNK], data[CHUNK:]
            best = max(best, self._step(chunk))
        self.pending = data
        return best

    def _step(self, chunk):
        self.raw.extend(chunk.tolist())
        tail = np.array(list(self.raw)[-CHUNK - 160 * 3:], dtype=np.int16)
        self.melbuf = np.vstack((self.melbuf, self._melspec(tail)))[-970:]
        win = self.melbuf[-76:].astype(np.float32)[None, :, :, None]
        e = self.emb.run(None, {"input_1": win})[0].squeeze()
        self.feats = np.vstack((self.feats, e))[-120:]
        score = float(self.ww.run(None, {self.ww_input: self.feats[None, -self.n_frames:, :].astype(np.float32)})[0][0][0])
        self.n_pred += 1
        return 0.0 if self.n_pred < 5 else score  # перші кадри — прогрів, як в openwakeword
