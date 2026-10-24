"""Entry point.

Dev:   python wsgi.py
Prod:  waitress-serve --port=8000 wsgi:app        (Windows)
       gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app     (Linux; each worker loads its own model copy)
"""
from app import create_app

app = create_app()

# Load the speech model now rather than inside the first video upload: it can be a download of
# several hundred MB. Until it is ready, an uploaded video is told to retry (never published
# unchecked by accident), and /ready shows video.ready.
import threading

_video = app.extensions["moderation"].settings.video
if _video.enabled:
    threading.Thread(target=_video.warm, name="video-warmup", daemon=True).start()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
