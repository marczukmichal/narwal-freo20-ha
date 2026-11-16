FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir agora-python-server-sdk==2.4.10 numpy pillow av==16.1.0
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/agora/agora_sdk
COPY kamera.py sessions.py audio_bridge.py talk_audio.py video_bridge.py /
WORKDIR /data
CMD ["python3", "-u", "/kamera.py"]
