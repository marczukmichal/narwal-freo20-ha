# Freo 20: obstacle photos and live camera in Home Assistant (cloud companion)

> **Status:** personal setup, works on my Freo 20. Not a supported integration.
>
> **Cloud dependency and account risk:** everything below needs a Narwal cloud account and uses unofficial, undocumented app endpoints. Narwal can change or block them at any time, and using them could put the account at risk. The local side (robot state, settings, station, map) uses the modified copy of cvette/narwal-ha in this repository (MIT); the cloud parts sit on top of it.

## Overview

| Feature | Source | Runs where |
|---|---|---|
| Obstacle photos with label and time | Narwal cloud, decrypted locally | Small custom HA integration |
| Live camera, robot audio, push-to-talk | Agora RTC session, credentials from the Narwal cloud | HA add-on (Agora Python server SDK) |

The custom integration logs in to the Narwal cloud once with the account's email and password, the same way the app does. It stores only the access and refresh tokens in the config entry, and refreshes them when they expire. The password is not stored.

## 1. Obstacle photos

### Where the photos are

1. List the cleaning records for the device: `GET /sweeper-map-scheme-app-server/v2/work_record/all` with `deviceId` and `productId`.
2. For each record, get the detail: `GET /clean-record-app-server/v1/clean-record/detail` with `clean_record_id`, `device_id`, and `product_id`.
   * `image_resources.obs_images[]` has a URL per photo. The file name contains the capture time in nanoseconds and the robot's own classification label, for example `..._<ns>_<label>.jpg.nenc`.
   * `vision_data.vision_obs[]` has the detection box per obstacle id. I use its centre to pin the photo on the map.
3. Download the `.jpg.nenc` file from the URL. It is encrypted.

### Getting the key (once per device)

This is the same key exchange the app does:

1. Generate your own RSA-2048 key pair.
2. `POST /iot-platform/iot-device-key-manage-server/v2/device/secret/key/get` with `deviceId`, `productId`, `keyName: 1`, and your public key as PEM.
3. The response has three fields:
   * `aesSecretCiphertext`: an AES-128 key, RSA-encrypted with your public key (PKCS#1 v1.5).
   * `ivCiphertext`: the device IV, encrypted with that AES key (AES-128-ECB). It decrypts to hex text; the first 32 hex characters are the 16-byte IV.
   * `secretKeyCiphertext`: the device RSA private key as PEM, also AES-128-ECB with the same AES key.

The device key and IV are per device and stay the same, so after this one call all decryption is local.

### Decrypting a photo

Each `.nenc` file is:

* The first 256 bytes: an AES-256 key, RSA-encrypted with the device key (PKCS#1 v1.5).
* The rest: the JPEG, AES-256-CBC with the device IV and PKCS7 padding.

```python
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sympad

def decrypt_photo(data: bytes, device_key, iv: bytes) -> bytes:
    key = device_key.decrypt(data[:256], padding.PKCS1v15())
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    raw = dec.update(data[256:]) + dec.finalize()
    unpad = sympad.PKCS7(128).unpadder()
    jpg = unpad.update(raw) + unpad.finalize()
    assert jpg.startswith(b"\xff\xd8")  # JPEG
    return jpg
```

I keep the decrypted photos in HA's private storage, not in `www`, and serve them through an authenticated HA view. The gallery card shows the label name and the local time, for example "Shoes · 13:19".

The pet photos from the app's album are different: they are plain JPEGs behind short-lived signed links (`GET /clean-record-app-server/v2/deviceFile/listMedia`), so they need no decryption.

## 2. Live camera, audio and push-to-talk

### Getting a session

1. `GET /iot-application-platform/app-product-media-server/v1/getDeviceInfo` with `deviceId` and `productId` returns the Agora `appId`.
2. `POST /iot-application-platform/app-product-media-server/v1/channel/start` with `deviceId`, `productId`, `appId`, and a random `sessionID`. It returns `channel`, `token`, `uid`, `securityKey`, `salt`, and `tokenExpireTime`. This call also tells the robot to join the channel and start the camera.
3. For robot audio, the app makes one more call: `POST .../channel/function/add` with `type: 1` and the channel. Without it, the robot joins with video only.

### Joining from Home Assistant

A local add-on (Docker, `agora-python-server-sdk`) joins that channel as a second participant:

* Media encryption is on: `enable_encryption` with mode 7 (AES-128-GCM2), `securityKey` as the key, and the base64-decoded `salt` as the KDF salt.
* Video arrives as YUV420 frames. The add-on converts them to JPEG and serves an MJPEG stream on the internal add-on network only (no ports exposed). HA proxies it as a normal camera entity, so only logged-in users can see it.
* Robot audio: the robot sends AAC with RTP payload type 69 (`che.audio.custom_payload_type: 69`), the same as the Android app's player.
* Push-to-talk: the robot only played speech when it was sent as AAC-LC (it did not play Opus). The add-on encodes the browser's microphone to AAC-LC and publishes it only while the talk button is held.
* The frame rate is set by the robot; the app has no FPS or quality setting.

### Lifecycle (answer to the service-call question)

The add-on only joins while someone is watching. It is driven by a small session API, which the companion integration exposes as an HA service:

* `start` (video or audio-only) returns a session id and joins the channel.
* `heartbeat` renews a 35-second lease.
* `stop`, or a missed heartbeat, leaves the channel, and the robot's camera turns off.

So join / serve frames / leave is already a service call. Snapshots work through the normal `camera.snapshot` action while a session is active. For a security-monitor automation, two changes would be needed:

1. The service currently requires an admin user context. An automation runs without a user, so it needs an explicit opt-in for system calls.
2. A "watch for N seconds" mode with a server-side lease, so an automation does not have to send heartbeats.

## What is not included

* No credentials, tokens, device keys or device identifiers are in this write-up. You need your own account.
* The API host depends on the account's region (EU, US, and so on), the same hosts the app uses.

I'm happy to collaborate on the security-monitor side as an optional cloud companion, so the core integration stays local-only.
