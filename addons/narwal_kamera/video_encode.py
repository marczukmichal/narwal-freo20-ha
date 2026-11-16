"""Fast JPEG encoding of the robot's YUV420 frames.

libavcodec (PyAV) encodes MJPEG straight from YUV, with no RGB conversion in
Python. On a slow HA CPU that is several times faster than Pillow; Pillow
stays as a fallback if PyAV fails.
"""
import io
from fractions import Fraction

import numpy as np


def planes(raw):
    """(w, h, y, u, v) as arrays without stride padding."""
    w, h, ys, us, vs, yb, ub, vb = raw
    y = np.frombuffer(yb, np.uint8)[: h * ys].reshape(h, ys)[:, :w]
    u = np.frombuffer(ub, np.uint8)[: (h // 2) * us].reshape(h // 2, us)[:, : w // 2]
    v = np.frombuffer(vb, np.uint8)[: (h // 2) * vs].reshape(h // 2, vs)[:, : w // 2]
    return w, h, y, u, v


def out_size(w, h, max_w):
    if w > max_w:
        w, h = max_w, round(h * max_w / w)
    return w // 2 * 2, h // 2 * 2


class JpegEncoder:
    def __init__(self, max_w=1024, quality=5):
        self.max_w, self.quality = max_w, quality  # MJPEG qscale, 2 = best
        self.ctx, self.size, self.failed, self.error = None, None, False, None

    def _context(self, size):
        import av
        ctx = av.CodecContext.create("mjpeg", "w")
        ctx.width, ctx.height = size
        ctx.pix_fmt = "yuvj420p"
        ctx.time_base = Fraction(1, 30)
        ctx.qmin = ctx.qmax = self.quality
        ctx.open()
        return ctx

    def encode(self, raw):
        w, h, y, u, v = planes(raw)
        size = out_size(w, h, self.max_w)
        if not self.failed:
            try:
                import av
                if self.ctx is None or self.size != size:
                    self.ctx, self.size = self._context(size), size
                w2, h2 = w // 2 * 2, h // 2 * 2
                arr = np.concatenate([y[:h2, :w2].reshape(-1), u[: h2 // 2, : w2 // 2].reshape(-1),
                                      v[: h2 // 2, : w2 // 2].reshape(-1)]).reshape(h2 * 3 // 2, w2)
                frame = av.VideoFrame.from_ndarray(arr, format="yuvj420p")  # same range as before
                frame = frame.reformat(width=size[0], height=size[1], format="yuvj420p")
                data = b"".join(bytes(p) for p in self.ctx.encode(frame))
                if data[:2] == b"\xff\xd8":
                    return data
            except Exception as exc:  # noqa: BLE001 - fall back to Pillow
                self.failed, self.error = True, f"{type(exc).__name__}: {exc}"[:120]
        return self._pillow(w, h, y, u, v, size)

    @staticmethod
    def _pillow(w, h, y, u, v, size):
        from PIL import Image
        img = Image.merge("YCbCr", [Image.fromarray(np.ascontiguousarray(y)),
                                    Image.fromarray(np.ascontiguousarray(u)).resize((w, h)),
                                    Image.fromarray(np.ascontiguousarray(v)).resize((w, h))])
        if size != (w, h):
            img = img.resize(size)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return buf.getvalue()
