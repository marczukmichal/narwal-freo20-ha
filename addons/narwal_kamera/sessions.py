"""Short-lived viewer leases; reading frames never activates the camera."""
import threading
import time
import uuid


class ViewerSessions:
    def __init__(self, ttl=35, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._leases = {}
        self._lock = threading.Lock()

    def _prune(self):
        now = self.clock()
        self._leases = {key: v for key, v in self._leases.items() if v[0] > now}

    def start(self, mode='video'):
        with self._lock:
            self._prune()
            if len(self._leases) >= 16:
                raise ValueError('Too many viewers')
            key = uuid.uuid4().hex
            self._leases[key] = [self.clock() + self.ttl, mode if mode in ('video', 'audio') else 'video']
            return key

    def heartbeat(self, key):
        with self._lock:
            self._prune()
            if key not in self._leases:
                return False
            self._leases[key][0] = self.clock() + self.ttl
            return True

    def stop(self, key):
        with self._lock:
            self._leases.pop(key, None)

    def active(self):
        with self._lock:
            self._prune()
            return bool(self._leases)

    def valid(self, key):
        with self._lock:
            self._prune()
            return key in self._leases

    def video_active(self):
        with self._lock:
            self._prune()
            return any(v[1] == 'video' for v in self._leases.values())
