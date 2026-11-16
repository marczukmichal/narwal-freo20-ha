"""Encode intercom PCM as explicit AAC-LC packets, paced in real time.

The app selects AAC payload 69. A private SDK parameter returning success
does not establish the codec of a custom PCM track. The encoded-track API
sets the format explicitly, as in Agora's push_audio_encoded_file example.

Each packet carries an ADTS header, like the frames Agora's AAC samples send:
the robot gets no out-of-band decoder config, so raw packets are undecodable.
The app keeps its microphone track published for the whole session and only
switches capture on and off, so the track stays published here too; nothing
is sent unless the talk button is held.
"""
from collections import deque
from fractions import Fraction
import time


class TalkAudio:
    SAMPLE_RATE = 16000
    SAMPLES = 1024
    PERIOD = SAMPLES / SAMPLE_RATE

    def __init__(self, conn, clock=time.monotonic):
        self.conn, self.clock = conn, clock
        self.encoder = None
        self.pending = deque()
        self.published = False

    @staticmethod
    def adts(packet, rate_index=8, channels=1):
        """ADTS header for one AAC-LC packet (16 kHz = index 8)."""
        n = len(packet) + 7
        return bytes([0xFF, 0xF1, 0x40 | (rate_index << 2) | (channels >> 2),
                      ((channels & 3) << 6) | (n >> 11), (n >> 3) & 0xFF,
                      ((n & 7) << 5) | 0x1F, 0xFC]) + packet

    def start(self):
        import av
        self.encoder = av.CodecContext.create('aac', 'w')
        self.encoder.sample_rate = self.SAMPLE_RATE
        self.encoder.layout = 'mono'
        self.encoder.format = 'fltp'
        self.encoder.bit_rate = 32000
        self.encoder.time_base = Fraction(1, self.SAMPLE_RATE)
        self.encoder.open()
        self.pending.clear()
        self.samples = 0
        self.next_at = self.clock()
        if self.published:
            return 0
        code = self.conn.publish_audio()
        self.published = code == 0
        return code

    def stop(self):
        # Discard the encoder's tail on release; never play buffered speech later.
        self.encoder = None
        self.pending.clear()
        return 0

    def close(self):
        self.stop()
        if not self.published:
            return 0
        self.published = False
        return self.conn.unpublish_audio()

    def push(self, pcm):
        import av
        import numpy as np
        if self.encoder is None:
            raise ValueError('Mikrofon wyciszony')
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32).reshape(1, -1) / 32768
        frame = av.AudioFrame.from_ndarray(values, format='fltp', layout='mono')
        frame.sample_rate = self.SAMPLE_RATE
        frame.pts = self.samples
        frame.time_base = Fraction(1, self.SAMPLE_RATE)
        self.samples += frame.samples
        self.pending.extend(self.adts(bytes(packet)) for packet in self.encoder.encode(frame))
        # Paced in real time by tick(); up to ~3 s may wait (TTS is sent ahead).
        if len(self.pending) > 48:
            raise ValueError('Audio connection too slow, microphone muted')
        return 0

    def tick(self):
        if not self.pending or self.clock() < self.next_at:
            return 0
        from agora.rtc.agora_base import AudioCodecType, EncodedAudioFrameInfo
        info = EncodedAudioFrameInfo(codec=AudioCodecType.AUDIO_CODEC_AACLC,
                                    sample_rate=self.SAMPLE_RATE,
                                    samples_per_channel=self.SAMPLES,
                                    number_of_channels=1, speech=1)
        result = self.conn.push_audio_encoded_data(self.pending.popleft(), info)
        # Do not burst old packets after a scheduling pause.
        now = self.clock()
        deadline = self.next_at + self.PERIOD
        self.next_at = deadline if deadline > now else now + self.PERIOD
        return result


class PcmTalk:
    """Speech as 16 kHz mono PCM; the SDK encodes it with its default codec,
    as the Narwal app does for robots without agoraVideoStreamingType=AAC.
    Pushed in 20 ms chunks paced in real time; up to ~3 s may wait."""
    SAMPLE_RATE = 16000
    CHUNK = 640  # 20 ms
    PERIOD = 0.02
    MAX_PENDING = 150

    def __init__(self, conn, clock=time.monotonic):
        self.conn, self.clock = conn, clock
        self.pending = deque()
        self.published = False
        self.active = False
        self.next_at = 0.0

    def start(self):
        self.pending.clear()
        self.active = True
        self.next_at = self.clock()
        if self.published:
            return 0
        code = self.conn.publish_audio()
        self.published = code == 0
        return code

    def stop(self):
        # Never play buffered speech after release.
        self.active = False
        self.pending.clear()
        return 0

    def close(self):
        self.stop()
        if not self.published:
            return 0
        self.published = False
        return self.conn.unpublish_audio()

    def push(self, pcm):
        if not self.active:
            raise ValueError('Mikrofon wyciszony')
        for i in range(0, len(pcm), self.CHUNK):
            self.pending.append(bytes(pcm[i:i + self.CHUNK]))
        if len(self.pending) > self.MAX_PENDING:
            raise ValueError('Audio connection too slow, microphone muted')
        return 0

    def tick(self):
        if not self.pending or self.clock() < self.next_at:
            return 0
        result = self.conn.push_audio_pcm_data(bytearray(self.pending.popleft()), self.SAMPLE_RATE, 1)
        now = self.clock()
        deadline = self.next_at + self.PERIOD
        self.next_at = deadline if deadline > now else now + self.PERIOD
        return result
