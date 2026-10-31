"""Gunicorn settings for the BONC moderation API on a Linux host.

    gunicorn -c deploy/gunicorn.conf.py wsgi:app      # run from flask_api/

WORKERS ARE EXPENSIVE HERE, and this is the one number to get right on a VPS. Each worker is
a separate process that loads its own copy of both ONNX models - wsgi.py says so, and measured
on this codebase that is:

    text model   models/v9-ver3     558 MB resident
    image model  models/img_v1      661 MB resident
                                  ------
    per worker                    ~1.24 GB, before Python, Flask and request buffers

So the usual "2 x cores + 1" advice is wrong for this service: on an 8-core box it would ask
for 17 workers and about 21 GB of models alone. Size by MEMORY, not by cores:

    4 GB VPS   ->  1 worker    (plus Whisper, see below - this is tight)
    8 GB VPS   ->  2-3 workers
    16 GB VPS  ->  6 workers

Whisper is on top of that again. media.video.enabled is true in settings.yaml and wsgi.py
warms `small` in a background thread on startup, which is roughly another 1 GB in whichever
worker handles the upload, and it downloads on first run. If video moderation is not wanted on
this host, set VIDEO_CHECK=0 and the warm-up and the download both stop.

Threads rather than more workers is the cheaper way to add concurrency here: inference holds
the GIL only inside onnxruntime, which releases it, so a couple of threads per worker overlap
nicely without a second copy of a 1.24 GB model.
"""
import multiprocessing
import os

# One worker by default. Raising this is a decision about RAM, and the deliberate default is
# the one that fits the smallest box rather than the one that looks fast.
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")

# Model load plus Whisper warm-up can take a while on a cold VPS disk, and a worker killed
# mid-load restarts into the same slow path for ever.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 60
# Long enough that an idle keep-alive connection is not re-establishing TLS constantly, short
# enough that a dead proxy connection is noticed.
keepalive = 15

# NOT preload_app. It would load the models once before forking and look like a saving, but
# onnxruntime sessions do not survive fork safely - the children inherit a session whose
# threads do not exist in them, and the failure is a hang on first inference rather than an
# error. Each worker loads its own, which is why workers cost what they cost above.
preload_app = False

max_requests = 0          # no cycling: a restart means reloading 1.24 GB of models
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
# The real client address is recorded by the app itself (app/client_address.py), which needs
# `trust_proxy_hops` set in settings.yaml when anything sits in front. Gunicorn's own
# forwarded-for handling is left alone so there is one place that decides.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus'


def on_starting(server):
    server.log.info("BONC moderation API starting: %d worker(s) x %d threads, ~1.24 GB of "
                    "models per worker", workers, threads)
    if workers > 1:
        server.log.warning("%d workers will hold about %.1f GB of models in total",
                           workers, workers * 1.24)
