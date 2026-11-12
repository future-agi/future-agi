"""Single-process request replay storage for memory-only service endpoints."""

import threading
import time

from tfc.service_envelope import SKEW


class MemoryNonceStore:
    def __init__(self, *, epoch=None, capacity=100000):
        self.epoch = time.time() if epoch is None else epoch
        self.capacity = capacity
        self._lock = threading.Lock()
        self._consumed = {}

    def consume(self, audience, identifier, iat, exp, now):
        with self._lock:
            if iat < self.epoch + SKEW:
                return False
            self._consumed = {k: v for k, v in self._consumed.items() if v >= now}
            key = (audience, identifier)
            if key in self._consumed or len(self._consumed) >= self.capacity:
                return False
            self._consumed[key] = exp
            return True
