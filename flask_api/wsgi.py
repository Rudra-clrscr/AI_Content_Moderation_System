"""Entry point.

Dev:   python wsgi.py
Prod:  waitress-serve --port=8000 wsgi:app        (Windows)
       gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app     (Linux; each worker loads its own model copy)
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
