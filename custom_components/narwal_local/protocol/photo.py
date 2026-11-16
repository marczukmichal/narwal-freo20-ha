"""Read an existing vision photo; this command never activates the camera."""
from dataclasses import dataclass, field

from .pb import WIRE_LEN, WIRE_VARINT, Writer, parse

MAX_PHOTO_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class PhotoResponse:
    result: int = 0
    image: bytes = field(default=b"", repr=False)
    url: str = field(default="", repr=False)


def photo_request(obstacle_id: int) -> bytes:
    if not isinstance(obstacle_id, int) or isinstance(obstacle_id, bool) or not 0 <= obstacle_id < 2**31:
        raise ValueError("Invalid obstacle id")
    return bytes(Writer().varint(1, 1).varint(2, obstacle_id))


def photo_response(payload: bytes) -> PhotoResponse:
    if len(payload) > MAX_PHOTO_BYTES:
        raise ValueError("The photo is too large")
    result, image, url = 0, b"", ""
    for f in parse(payload):
        if f.num == 1 and f.wtype == WIRE_VARINT:
            result = f.int
        elif f.num == 2 and f.wtype == WIRE_LEN:
            image = f.data
        elif f.num == 3 and f.wtype == WIRE_LEN:
            url = f.data.decode("utf-8")
    return PhotoResponse(result, image, url)
