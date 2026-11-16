"""Ephemeral 16 kHz mono PCM. No recording, no microphone without a live hold."""
import base64
from collections import deque
import threading
import time
import uuid


class AudioBridge:
    def __init__(self, viewers, clock=time.monotonic, logger=None):
        self.viewers, self.clock = viewers, clock
        self.logger = logger or (lambda *args: None)
        self.lock, self.frames_lock = threading.RLock(), threading.Lock()
        self.conn = None
        self.sender = None
        self.listeners = {}
        self.frames = deque(maxlen=100)
        self.frame_seq = 0
        self.talker = None
        self.subscribed = False
        self.stats = {
            "rx_callbacks": 0, "rx_frames": 0, "rx_rejected": 0,
            "rx_bytes": 0, "rx_meta": None, "rx_format_logged": False,
            "polls": 0, "poll_bytes": 0, "talk_frames": 0,
            "talk_signal_frames": 0, "talk_bytes": 0,
        }

    def bind(self, conn, sender=None):
        with self.lock:
            self.reset()
            self.conn = conn
            self.sender = sender
            self.logger("audio observer bound")

    def reset(self):
        with self.lock:
            self._stop_talk()
            if self.sender:
                self.sender.close()
            if self.conn and self.subscribed:
                self.conn.get_local_user().unsubscribe_all_audio()
            self.subscribed = False
            self.listeners.clear()
            with self.frames_lock:
                self.frames.clear()
            self.conn = None
            self.sender = None

    def _check(self, code, operation):
        if code != 0:
            self.logger("audio sdk error", operation, code)
            raise ValueError(f"Audio SDK error ({operation}: {code})")

    def _stop_talk(self):
        if self.talker and self.conn:
            code = self.sender.stop() if self.sender else self.conn.unpublish_audio()
            self.logger("talk stopped", "code", code)
        self.talker = None

    def tick(self):
        with self.lock:
            now = self.clock()
            self.listeners = {key: info for key, info in self.listeners.items()
                              if info['until'] > now and self.viewers.valid(key)}
            if self.talker and (self.talker['until'] <= now or not self.viewers.valid(self.talker['viewer'])):
                self._stop_talk()
            if self.talker and self.sender:
                try:
                    self._check(self.sender.tick(), 'send_aac')
                except Exception:
                    self._stop_talk()
                    raise
            if self.subscribed and not self.listeners:
                self.conn.get_local_user().unsubscribe_all_audio()
                self.subscribed = False
                with self.frames_lock:
                    self.frames.clear()

    def receive(self, frame):
        # SDK callback never takes the SDK control lock (callbacks can be synchronous).
        try:
            data = bytes(frame.buffer)
            meta = (frame.samples_per_sec, frame.channels, frame.bytes_per_sample, len(data))
        except Exception as exc:  # noqa: BLE001 - never let a Python callback unwind into Agora
            with self.frames_lock:
                self.stats["rx_callbacks"] += 1
                self.stats["rx_rejected"] += 1
            self.logger("audio callback frame unreadable", type(exc).__name__)
            return
        with self.frames_lock:
            self.stats["rx_callbacks"] += 1
            self.stats["rx_meta"] = meta
            if meta[0] != 16000 or meta[1] != 1 or meta[2] != 2:
                self.stats["rx_rejected"] += 1
                if not self.stats["rx_format_logged"]:
                    self.stats["rx_format_logged"] = True
                    self.logger("audio frame format rejected", meta)
                return
            self.stats["rx_frames"] += 1
            self.stats["rx_bytes"] += len(data)
            self.frame_seq += 1
            self.frames.append((self.frame_seq, self.clock(), data))

    def diagnostics(self):
        with self.lock, self.frames_lock:
            result = dict(self.stats)
            result["listener_count"] = len(self.listeners)
            result["talk_active"] = bool(self.talker)
            result["subscribed"] = self.subscribed
            self.stats.update({
                "rx_callbacks": 0, "rx_frames": 0, "rx_rejected": 0,
                "rx_bytes": 0, "polls": 0, "poll_bytes": 0,
                "talk_frames": 0, "talk_signal_frames": 0, "talk_bytes": 0,
            })
            return result

    def command(self, data):
        with self.lock:
            self.tick()
            key, action = data.get('session_id', ''), data.get('action')
            if not isinstance(key, str) or not self.viewers.valid(key) or not self.conn:
                raise ValueError('Camera session expired')
            now = self.clock()
            if action == 'listen_start':
                if not self.subscribed:
                    self._check(self.conn.get_local_user().subscribe_all_audio(), "subscribe_all_audio")
                    self.subscribed = True
                self.listeners[key] = {'until': now + 2, 'cursor': self.frame_seq}
                self.logger("listen started", "subscribed", self.subscribed)
            elif action == 'listen_stop':
                self.listeners.pop(key, None)
                self.tick()
                self.logger("listen stopped")
            elif action == 'poll':
                if key not in self.listeners:
                    raise ValueError('Listening has ended')
                listener = self.listeners[key]
                with self.frames_lock:
                    frames = [f for f in self.frames if f[0] > listener['cursor'] and now-f[1] < .35]
                    listener['cursor'] = self.frame_seq
                    payload = b''.join(f[2] for f in frames)[-11200:]
                    self.stats["polls"] += 1
                    self.stats["poll_bytes"] += len(payload)
                listener['until'] = now + 2
                return {'pcm': base64.b64encode(payload).decode(), 'sample_rate': 16000}
            elif action == 'talk_start':
                if self.talker:
                    raise ValueError('The microphone is already in use')
                self._check(self.sender.start() if self.sender else self.conn.publish_audio(), "publish_audio")
                token = uuid.uuid4().hex
                self.talker = {'viewer': key, 'token': token, 'until': now+1.5, 'seq': -1}
                self.logger("talk started", "publish_audio ok")
                return {'talk_id': token}
            elif action in ('talk_pcm', 'talk_stop'):
                if not self.talker or self.talker['viewer'] != key or self.talker['token'] != data.get('talk_id'):
                    raise ValueError('Mikrofon wyciszony')
                if action == 'talk_stop':
                    self._stop_talk()
                else:
                    seq = data.get('seq')
                    pcm = base64.b64decode(data.get('pcm', ''), validate=True)
                    # One message may carry up to 500 ms (5 x 100 ms): fewer round trips
                    # through HA, so latency spikes no longer cut speech off.
                    if (not isinstance(seq, int) or seq <= self.talker['seq'] or not pcm
                            or len(pcm) % 3200 or len(pcm) > 16000):
                        raise ValueError('Invalid audio frame')
                    try:
                        for i in range(0, len(pcm), 3200):
                            part = pcm[i:i + 3200]
                            code = self.sender.push(part) if self.sender else self.conn.push_audio_pcm_data(bytearray(part), 16000, 1)
                            self._check(code, "push_audio")
                    except Exception:
                        self._stop_talk()
                        raise
                    self.talker['seq'] = seq
                    self.talker['until'] = now + 1.5 + len(pcm) / 32000
                    signal = any(pcm[i] or pcm[i + 1] for i in range(0, len(pcm), 2))
                    with self.frames_lock:
                        self.stats["talk_frames"] += 1
                        self.stats["talk_signal_frames"] += int(signal)
                        self.stats["talk_bytes"] += len(pcm)
            else:
                raise ValueError('Unknown audio action')
            return {'ok': True}
