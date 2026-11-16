"""Single-frame mailbox: slow viewers skip old frames instead of buffering them."""
import threading


class FrameSlot:
    def __init__(self):
        self.condition = threading.Condition()
        self.sequence = 0
        self.value = None

    def put(self, value):
        with self.condition:
            self.sequence += 1
            self.value = value
            self.condition.notify_all()

    def latest(self):
        with self.condition:
            return self.sequence, self.value

    def wait(self, after, timeout=1):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != after, timeout)
            return self.sequence, self.value
