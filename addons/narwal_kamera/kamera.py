"""Narwal camera bridge: joins the robot's Agora channel only while someone watches.

POST /session   -> explicit viewer start/heartbeat/stop
GET /frame.jpg  -> latest JPEG (only with an active viewer lease)
GET /stream     -> MJPEG (multipart/x-mixed-replace)
GET /status     -> JSON
Session params come from HA service narwal_cloud.start_wideo (via Supervisor).
Listens only on the internal add-on network (no ports mapped).
"""
import base64
import io
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sessions import ViewerSessions
from audio_bridge import AudioBridge
from talk_audio import TalkAudio
from video_bridge import FrameSlot
from agora.rtc.audio_frame_observer import IAudioFrameObserver

import numpy as np
from PIL import Image
from agora.rtc.agora_base import (AgoraServiceConfig, ChannelProfileType, ClientRoleType,
                                  EncryptionConfig, RTCConnConfig, RtcConnectionPublishConfig, AudioScenarioType,
                                  AudioPublishType)
from agora.rtc.agora_service import AgoraService
from agora.rtc.rtc_connection_observer import IRTCConnectionObserver
from agora.rtc.local_user_observer import IRTCLocalUserObserver
from agora.rtc.video_frame_observer import IVideoFrameObserver

OPTS = json.load(open("/data/options.json")) if os.path.exists("/data/options.json") else {}
DEVICE_ID = OPTS.get("device_id", "")  # optional: empty = the robot picked by narwal_cloud
DEVICE = {"device_id": DEVICE_ID, "product_id": ""}
viewers = ViewerSessions()  # explicit Start + renewable 35-second lease
STALL_S = 15         # reconnect if no frames for this long while watched
MAX_W = 1024
raw_frames, jpeg_frames = FrameSlot(), FrameSlot()

lock = threading.Lock()
state = {"raw": None, "raw_t": 0.0, "jpeg": None, "jpeg_t": 0.0, "seen": 0.0,
         "conn": None, "connected_t": 0.0, "status": "idle", "frames": 0}
svc = {"service": None, "app_id": None}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


audio = AudioBridge(viewers, logger=log)


def get_session():
    req = urllib.request.Request(
        "http://supervisor/core/api/services/narwal_cloud/start_wideo?return_response",
        data=json.dumps({"device_id": DEVICE_ID} if DEVICE_ID else {}).encode(),
        headers={"Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"],
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))["service_response"]


class Conn(IRTCConnectionObserver):
    def on_connected(self, c, info, reason):
        state["status"] = "connected"; log("connected", reason)

    def on_disconnected(self, c, info, reason):
        state["status"] = "disconnected"; log("disconnected", reason)

    def on_user_joined(self, c, uid):
        log("robot joined", uid)

    def on_user_left(self, c, uid, reason):
        log("robot left", uid, reason)

    def on_encryption_error(self, c, t):
        log("encryption error", t)


class Frames(IVideoFrameObserver):
    def on_frame(self, ch, uid, f):
        # copy only; heavy work happens in the encoder thread
        raw = (f.width, f.height, f.y_stride, f.u_stride, f.v_stride,
               bytes(f.y_buffer), bytes(f.u_buffer), bytes(f.v_buffer))
        now = time.time()
        state["raw_t"] = now
        raw_frames.put((raw, now))
        state["frames"] += 1
        w0, n0 = state.get("fps_win", (now, state["frames"] - 1))
        if now - w0 >= 5:  # measured incoming rate from the robot
            state["fps_in"] = round((state["frames"] - n0) / (now - w0), 1)
            state["size"] = f"{f.width}x{f.height}"
            if not state.get("fps_logged"):
                log("robot video", state["size"], state["fps_in"], "fps"); state["fps_logged"] = True
            state["fps_win"] = (now, state["frames"])
        elif "fps_win" not in state:
            state["fps_win"] = (w0, n0)
        return 1


class AudioFrames(IAudioFrameObserver):
    def on_get_audio_frame_position(self, user):
        return 8  # before mixing: remote robot only

    def on_playback_audio_frame_before_mixing(self, user, channel, uid, frame, vad, vad_data):
        try:
            audio.receive(frame)
        except Exception as exc:  # noqa: BLE001 - never unwind through the Agora callback
            log("audio observer callback error", type(exc).__name__)
        return 1


class AudioTrackState(IRTCLocalUserObserver):
    """Log audio media state only; never inspect audio content."""
    def on_audio_subscribe_state_changed(self, user, channel, uid, old_state, new_state, elapsed):
        log("audio subscription state", old_state, new_state, elapsed)

    def on_user_audio_track_subscribed(self, user, uid, track):
        log("remote audio track subscribed")

    def on_user_audio_track_state_changed(self, user, uid, track, track_state, reason, elapsed):
        log("remote audio track state", track_state, reason, elapsed)

    def on_first_remote_audio_frame(self, user, uid, elapsed):
        log("first remote audio frame", elapsed)

    def on_first_remote_audio_decoded(self, user, uid, elapsed):
        log("first remote audio decoded", elapsed)

    def on_audio_publish_state_changed(self, user, channel, old_state, new_state, elapsed):
        log("local audio publish state", old_state, new_state, elapsed)

    def on_local_audio_track_state_changed(self, user, track, track_state, error):
        log("local audio track state", track_state, error)


def audio_watchdog():
    last_report = 0.0
    while True:
        time.sleep(.01)
        try:
            audio.tick()
            now = time.monotonic()
            if now - last_report >= 2:
                report = audio.diagnostics()
                if report["listener_count"] or report["talk_active"]:
                    log("audio stats", json.dumps(report, separators=(",", ":")))
                last_report = now
        except Exception:
            log("audio watchdog error")


def request_device_audio(channel):
    """Narwal app: NRVideoKitDeviceAudioStartRequester -> channel/function/add (type 1).
    Without it the robot joins the channel with video only."""
    body = {"method": "POST",
            "path": "/iot-application-platform/app-product-media-server/v1/channel/function/add",
            "body": {"deviceId": DEVICE["device_id"], "productId": DEVICE["product_id"], "type": 1, "channel": channel},
            "device_id": DEVICE["device_id"], "product_id": DEVICE["product_id"]}
    req = urllib.request.Request(
        "http://supervisor/core/api/services/narwal_cloud/zapytanie?return_response",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"],
                 "Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=20))["service_response"]["odpowiedz"]
    log("device audio start", "code", resp.get("code"), "msg", str(resp.get("msg"))[:60])
    return resp.get("code") == 0


def connect():
    video = viewers.video_active()
    p = get_session()
    DEVICE.update(device_id=p.get("device_id") or DEVICE_ID, product_id=p.get("product_id") or "")
    state["channel"], state["audio_requested"], state["video_mode"] = p["channel"], False, video
    if svc["service"] is None:
        s = AgoraService()
        s.initialize(AgoraServiceConfig(appid=p["appId"], enable_video=1, enable_audio_processor=1,
                                        audio_scenario=AudioScenarioType.AUDIO_SCENARIO_DEFAULT,
                                        log_path="/tmp/agora.log"))
        svc["service"], svc["app_id"] = s, p["appId"]
    conn = svc["service"].create_rtc_connection(
        RTCConnConfig(auto_subscribe_audio=0, auto_subscribe_video=1 if video else 0,
                      client_role_type=ClientRoleType.CLIENT_ROLE_BROADCASTER,
                      channel_profile=ChannelProfileType.CHANNEL_PROFILE_LIVE_BROADCASTING),
        RtcConnectionPublishConfig(is_publish_audio=True, is_publish_video=False,
                                   audio_publish_type=AudioPublishType.AUDIO_PUBLISH_TYPE_ENCODED_PCM,
                                   audio_scenario=AudioScenarioType.AUDIO_SCENARIO_DEFAULT))
    # Preparing an encoded track does not publish it. Only talk_start publishes.
    # Narwal Android SWVideoPlayer uses payload type 69 for AAC camera audio.
    audio_param_code = conn.get_agora_parameter().set_parameters('{"che.audio.custom_payload_type":69}')
    audio.bind(conn, TalkAudio(conn))
    audio_frame_code = conn.get_local_user().set_playback_audio_frame_before_mixing_parameters(1, 16000)
    observer_code = conn.register_audio_frame_observer(AudioFrames(), 0, None)
    audio_track_observer = AudioTrackState()
    local_observer_code = conn.register_local_user_observer(audio_track_observer)
    state["audio_track_observer"] = audio_track_observer
    log("audio observer setup", "payload_type_69", audio_param_code, "talk_codec", "AAC-LC encoded track",
        "frame_params", audio_frame_code, "observer", observer_code,
        "track_observer", local_observer_code)
    conn.register_observer(Conn())
    conn.register_video_frame_observer(Frames())
    conn.enable_encryption(1, EncryptionConfig(
        encryption_mode=7, encryption_key=p["key"],
        encryption_kdf_salt=bytearray(base64.b64decode(p["salt"]))))
    connect_code = conn.connect(p["token"], p["channel"], str(p["uid"]))
    state.update(conn=conn, connected_t=time.time(), status="connecting", raw_t=time.time())
    log("joining channel", "connect", connect_code)


def disconnect():
    audio.reset()
    conn = state["conn"]
    state.update(conn=None, status="idle", raw=None, jpeg=None, audio_track_observer=None, fps_logged=False); state.pop("fps_win", None)
    raw_frames.put(None)
    jpeg_frames.put(None)
    if conn:
        try:
            conn.disconnect(); conn.release()
        except Exception as e:  # noqa: BLE001
            log("disconnect error", e)
        log("left channel")


def manager():
    while True:
        time.sleep(1)
        with lock:
            watched = viewers.active()
            try:
                if watched and state["conn"] is None:
                    connect()
                elif not watched and state["conn"] is not None:
                    disconnect()
                elif watched and viewers.video_active() != state.get("video_mode"):
                    log("mode change, video", viewers.video_active()); disconnect(); connect()
                elif watched and state.get("video_mode") and time.time() - state["raw_t"] > STALL_S:
                    log("no frames, reconnecting"); disconnect(); connect()
                elif (watched and not state.get("video_mode") and state["status"] != "connected"
                      and time.time() - state["connected_t"] > 30):
                    log("audio-only not connected, reconnecting"); disconnect(); connect()
            except Exception as e:  # noqa: BLE001
                state["status"] = f"error: {e}"; log("session error", type(e).__name__); time.sleep(5)


def encoder():
    last = 0
    while True:
        sequence, frame = raw_frames.wait(last)
        if frame is None or sequence == last:
            last = sequence
            continue
        # No frame cap: encode every frame the robot sends. Always take the
        # newest frame, so a slow conversion never builds a video backlog.
        last, frame = raw_frames.latest()
        if frame is None:
            continue
        raw, t = frame
        try:
            w, h, ys, us, vs, yb, ub, vb = raw
            y = np.frombuffer(yb, np.uint8)[: h * ys].reshape(h, ys)[:, :w]
            u = np.frombuffer(ub, np.uint8)[: (h // 2) * us].reshape(h // 2, us)[:, : w // 2]
            v = np.frombuffer(vb, np.uint8)[: (h // 2) * vs].reshape(h // 2, vs)[:, : w // 2]
            img = Image.merge("YCbCr", [Image.fromarray(y), Image.fromarray(u).resize((w, h)),
                                        Image.fromarray(v).resize((w, h))]).convert("RGB")
            if w > MAX_W:
                img = img.resize((MAX_W, round(h * MAX_W / w)))
            buf = io.BytesIO(); img.save(buf, "JPEG", quality=80)
            state["jpeg"], state["jpeg_t"] = buf.getvalue(), t
            jpeg_frames.put(buf.getvalue())
        except Exception as e:  # noqa: BLE001
            log("encode error", e)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _wait_jpeg(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if not viewers.active():
                return None
            if state["jpeg"]:
                return state["jpeg"]
            time.sleep(0.2)
        return None

    def do_POST(self):
        if self.path == "/audio":
            action = "unknown"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 6000:
                    raise ValueError("Invalid body")
                data = json.loads(self.rfile.read(length))
                action = data.get("action", "unknown")
                if state["status"] != "connected":
                    raise ValueError("The camera is not connected")
                if not viewers.valid(data.get("session_id", "")):
                    raise ValueError("Camera session expired")
                if action in ("listen_start", "talk_start") and not state.get("audio_requested"):
                    try:
                        state["audio_requested"] = request_device_audio(state.get("channel"))
                    except Exception as exc:  # noqa: BLE001
                        log("device audio start failed", type(exc).__name__, str(exc)[:80])
                    if not state.get("audio_requested"):
                        raise ValueError("The robot did not confirm audio start. Try again.")
                result = audio.command(data)
                body = json.dumps(result).encode()
            except (ValueError, TypeError, AttributeError) as exc:
                log("audio command rejected", action, str(exc)[:120])
                body = json.dumps({"error": str(exc)[:120]}).encode()
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers(); self.wfile.write(body); return
            except Exception as exc:  # noqa: BLE001
                log("audio command error", action, type(exc).__name__)
                body = json.dumps({"error": "Audio add-on error"}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers(); self.wfile.write(body); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body); return
        if self.path != "/session":
            self.send_response(404); self.end_headers(); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("Invalid body")
            data = json.loads(self.rfile.read(length))
            action, key = data.get("action"), data.get("session_id", "")
            if action == "start":
                key = viewers.start(data.get("mode", "video"))
            elif action == "heartbeat":
                if not viewers.heartbeat(key):
                    self.send_response(410); self.end_headers(); return
            elif action == "stop":
                viewers.stop(key)
                audio.tick()
            elif action != "status":
                raise ValueError("Unknown action")
            # The manager observes lease removal within one second. Existing
            # streams cannot renew a stopped or expired lease.
            body = json.dumps({"session_id": key, "active": viewers.active(),
                               "video": bool(state.get("video_mode")),
                               "status": state["status"].split(":")[0],
                               "frame_age": round(time.time() - state["jpeg_t"], 1)
                               if state["jpeg_t"] else None}).encode()
        except (ValueError, TypeError, AttributeError):
            self.send_response(400); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            body = json.dumps({k: state.get(k) for k in ("status", "frames", "jpeg_t", "fps_in", "size")}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(body); return
        if self.path.startswith(("/frame.jpg", "/stream")) and not viewers.active():
            self.send_response(409); self.end_headers(); return
        if self.path.startswith("/frame.jpg"):
            jpg = self._wait_jpeg()
            if not jpg:
                self.send_response(503); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg))); self.end_headers(); self.wfile.write(jpg); return
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            sent = -1
            try:
                while viewers.active():
                    sequence, jpg = jpeg_frames.wait(sent)
                    if not viewers.active():
                        break
                    changed = sequence != sent
                    sent = sequence
                    if jpg and changed:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_response(404); self.end_headers()


if __name__ == "__main__":
    threading.Thread(target=manager, daemon=True).start()
    threading.Thread(target=encoder, daemon=True).start()
    threading.Thread(target=audio_watchdog, daemon=True).start()
    log("Narwal kamera ready on :8099")
    ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
