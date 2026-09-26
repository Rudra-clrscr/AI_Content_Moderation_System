# Demo setup: Command Prompt

Use this guide if you run commands in **Command Prompt** (`cmd.exe`). If your
prompt starts with `PS`, you're in PowerShell, so use
[DEMO_GUIDE_POWERSHELL.md](DEMO_GUIDE_POWERSHELL.md) instead.

Once the server is running, follow [DEMO_GUIDE.md](DEMO_GUIDE.md) for the run of
show and the tested sentences.

---

## 1. Open Command Prompt

Press `Win + R`, type `cmd`, and press Enter.

The prompt looks like a plain path, for example:

```
C:\Users\you>
```

If it starts with `PS`, you opened PowerShell by mistake. Close it and try again.

---

## 2. One-time setup

You need:
- **Python 3.12 or newer**, from python.org. Tick "Add Python to PATH" during install.
- **Git for Windows**, which already includes Git LFS.

Run these one line at a time, from a folder with a **short path**, such as `C:\projects`.
Deep folders (for example inside OneDrive\Documents) can hit Windows' 260-character
path limit while installing packages:

```cmd
git lfs install
git clone https://github.com/Rudra-clrscr/AI_Content_Moderation_System.git
cd AI_Content_Moderation_System\flask_api
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This guide always runs Python as `.venv\Scripts\python.exe`, so you never need
to "activate" the virtual environment.

Check that the model downloaded completely:

```cmd
dir models\current
```

`model_int8.onnx` should be about **172,266,637 bytes**. If it's only a few
hundred bytes, run `git lfs pull` and check again.

---

## 3. Start the server (every time)

From the `AI_Content_Moderation_System\flask_api` folder:

```cmd
set DEMO_PAGE=1
set RESULT_SINK=log
.venv\Scripts\python.exe -m waitress --port=8000 wsgi:app
```

> Type `set` lines exactly as shown, with **no spaces around `=`**.
> `set DEMO_PAGE = 1` creates a different setting, and the page stays disabled.

- `DEMO_PAGE=1` turns on the demo page, which is off by default so it never appears in production.
- `RESULT_SINK=log` writes decisions to this window instead of SQL Server, so the demo needs no database.

Wait for this line:

```
INFO waitress: Serving on http://0.0.0.0:8000
```

Leave this window open, because closing it stops the server. The `set` values
only last while this window is open, so run all three lines each time you start.

**To stop the server:** click this window and press `Ctrl + C`.

---

## 4. Open the demo page

In a **second** Command Prompt window:

```cmd
start http://127.0.0.1:8000/demo
```

Or type `http://127.0.0.1:8000/demo` into your browser.

### Check it's working

```cmd
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

`/health` returns `{"status":"ok"}`. `/ready` should include `"status":"ready"`
and `"gate_rules":6`.

**Next:** follow [DEMO_GUIDE.md](DEMO_GUIDE.md) from section 2.

---

## 5. Optional: call the API from Command Prompt

This shows that the page is only a front end: the same API serves any client.
Run these in the second window while the server is running.

```cmd
:: Allowed listing
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order.\", \"content_type\": \"product_listing\", \"content_id\": \"LST-1001\"}"

:: Blocked by the gate
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"Invest now and double your money in 7 days\", \"content_type\": \"advertisement\"}"

:: Bad input is rejected cleanly
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"   \", \"content_type\": \"post\"}"
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"Hello\", \"content_type\": \"tweet\"}"
```

| Request | Response |
|---|---|
| Allowed listing | `"decision":"allow"`, `"decided_by":"model"` |
| Blocked by the gate | `"decision":"reject"`, `"decided_by":"gate"` |
| Empty content | `invalid_content` |
| Unknown type | `invalid_content_type`, with the list of allowed types |

In cmd, the `\"` inside the JSON is required. Copy the lines as they are.

### Latency benchmark

```cmd
.venv\Scripts\python.exe scripts\bench_latency.py
```

This prints p50 and p95 inference times against the 30 ms budget. On the
development laptop, p95 is about 28 ms. Slower machines may report `OVER`,
because the budget assumes a 4-core server.

---

## 6. Troubleshooting (Command Prompt)

| Problem | Fix |
|---|---|
| `'python' is not recognized` | Reinstall Python with "Add Python to PATH" ticked, or use `py -m venv .venv` in step 2. |
| `OSError: [Errno 2] No such file or directory` during `pip install`, mentioning *Long Path support* | The project folder's path is too long. Delete the folder, clone again into a short path such as `C:\projects`, and repeat step 2. |
| `'git' is not recognized` | Install Git for Windows, then open a new Command Prompt window. |
| The page says **demo page disabled** | Stop the server (`Ctrl + C`), run `set DEMO_PAGE=1` with no spaces around `=`, then start it again in the **same** window. |
| `db_write_failed` errors in the server window | `RESULT_SINK` isn't set. Stop the server, run `set RESULT_SINK=log`, then start it again. |
| `No module named waitress` | You ran plain `python`. Use `.venv\Scripts\python.exe -m waitress --port=8000 wsgi:app`. |
| `status not_ready`, or the model fails to load | Run `dir models\current`. If `model_int8.onnx` is tiny, run `git lfs pull`, then restart the server. |
| Port 8000 already in use | Run `netstat -ano \| findstr :8000` to see the process ID (last column), then `taskkill /PID <that number> /F`. Or use another port: `.venv\Scripts\python.exe -m waitress --port=8080 wsgi:app`, and open `http://127.0.0.1:8080/demo`. |
| `curl` returns `invalid_json` | The `\"` escapes were lost when copying. Copy the whole line again from this guide. |
