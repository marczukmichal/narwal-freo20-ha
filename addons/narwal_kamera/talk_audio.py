"""Encode intercom PCM as explicit AAC-LC packets, paced in real time.

The app selects AAC payload 69. A private SDK parameter returning success
does not establish the codec of a custom PCM track. The encoded-track API
sets the format explicitly, as in Agora's push_audio_encoded_file example.
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
        return self.conn.publish_audio()

    def stop(self):
        # Discard the encoder's tail on release; never play buffered speech later.
        self.encoder = None
        self.pending.clear()
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
        self.pending.extend(bytes(packet) for packet in self.encoder.encode(frame))
        if len(self.pending) > 6:
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
