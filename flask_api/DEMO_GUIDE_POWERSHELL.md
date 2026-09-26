# Demo setup: PowerShell

Use this guide if you run commands in **PowerShell**. That includes Windows
Terminal and the VS Code terminal, which open PowerShell by default. If your
prompt doesn't start with `PS`, you're in Command Prompt, so use
[DEMO_GUIDE_CMD.md](DEMO_GUIDE_CMD.md) instead.

Once the server is running, follow [DEMO_GUIDE.md](DEMO_GUIDE.md) for the run of
show and the tested sentences.

---

## 1. Open PowerShell

Press `Win + X` and choose **Terminal** (or **Windows PowerShell**). You can also
open the terminal in VS Code with `` Ctrl + ` ``.

The prompt starts with `PS`, for example:

```
PS C:\Users\you>
```

---

## 2. One-time setup

You need:
- **Python 3.12 or newer**, from python.org. Tick "Add Python to PATH" during install.
- **Git for Windows**, which already includes Git LFS.

Run these one line at a time, from a folder with a **short path**, such as `C:\projects`.
Deep folders (for example inside OneDrive\Documents) can hit Windows' 260-character
path limit while installing packages:

```powershell
git lfs install
git clone https://github.com/Rudra-clrscr/AI_Content_Moderation_System.git
cd AI_Content_Moderation_System\flask_api
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This guide always runs Python as `.venv\Scripts\python.exe`, so you never need
to "activate" the virtual environment. That also avoids PowerShell's
*"running scripts is disabled on this system"* error from `Activate.ps1`.
(`activate.bat` does nothing useful in PowerShell.)

Check that the model downloaded completely:

```powershell
dir models\current
```

`model_int8.onnx` should be about **172266637** bytes (the `Length` column). If
it's only a few hundred bytes, run `git lfs pull` and check again.

---

## 3. Start the server (every time)

From the `AI_Content_Moderation_System\flask_api` folder:

```powershell
$env:DEMO_PAGE = "1"
$env:RESULT_SINK = "log"
.venv\Scripts\python.exe -m waitress --port=8000 wsgi:app
```

> **Don't use `set DEMO_PAGE=1` here.** That's Command Prompt syntax. In
> PowerShell it runs without an error but sets nothing the server can see, so the
> page says *demo page disabled*. Always use `$env:NAME = "value"`.

- `DEMO_PAGE=1` turns on the demo page, which is off by default so it never appears in production.
- `RESULT_SINK=log` writes decisions to this window instead of SQL Server, so the demo needs no database.

Wait for this line:

```
INFO waitress: Serving on http://0.0.0.0:8000
```

Leave this window open, because closing it stops the server. The `$env:` values
only last while this window is open, so run all three lines each time you start.

**To stop the server:** click this window and press `Ctrl + C`.

---

## 4. Open the demo page

In a **second** PowerShell window:

```powershell
Start-Process http://127.0.0.1:8000/demo
```

Or type `http://127.0.0.1:8000/demo` into your browser.

### Check it's working

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`/health` shows `status : ok`. `/ready` should show `status : ready` and
`gate_rules : 6`.

**Next:** follow [DEMO_GUIDE.md](DEMO_GUIDE.md) from section 2.

---

## 5. Optional: call the API from PowerShell

This shows that the page is only a front end: the same API serves any client.
Run these in the second window while the server is running.

```powershell
# Allowed listing
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order.", "content_type": "product_listing", "content_id": "LST-1001"}'

# Blocked by the gate
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "Invest now and double your money in 7 days", "content_type": "advertisement"}'

# Bad input is rejected cleanly (the error text is printed by the catch block)
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "   ", "content_type": "post"}' } catch { $_.ErrorDetails.Message }
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "Hello", "content_type": "tweet"}' } catch { $_.ErrorDetails.Message }
```

| Request | Response |
|---|---|
| Allowed listing | `decision : allow`, `decided_by : model` |
| Blocked by the gate | `decision : reject`, `decided_by : gate` |
| Empty content | `invalid_content` |
| Unknown type | `invalid_content_type`, with the list of allowed types |

To show the full JSON record instead of PowerShell's table view, add
`| ConvertTo-Json -Depth 5` to the end of a request line.

> Use `Invoke-RestMethod`, not `curl`. In Windows PowerShell 5.1, `curl` is
> an alias for a different command, and the Command Prompt examples won't work.
> `curl.exe` (with `.exe`) is fine for simple GET checks.

### Latency benchmark

```powershell
.venv\Scripts\python.exe scripts\bench_latency.py
```

This prints p50 and p95 inference times against the 30 ms budget. On the
development laptop, p95 is about 28 ms. Slower machines may report `OVER`,
because the budget assumes a 4-core server.

---

## 6. Troubleshooting (PowerShell)

| Problem | Fix |
|---|---|
| `python : The term 'python' is not recognized` | Reinstall Python with "Add Python to PATH" ticked, or use `py -m venv .venv` in step 2. |
| `OSError: [Errno 2] No such file or directory` during `pip install`, mentioning *Long Path support* | The project folder's path is too long. Delete the folder, clone again into a short path such as `C:\projects`, and repeat step 2. |
| The page says **demo page disabled** | You probably used `set DEMO_PAGE=1`. Stop the server (`Ctrl + C`), run `$env:DEMO_PAGE = "1"`, then start it again in the **same** window. |
| `db_write_failed` errors in the server window | `RESULT_SINK` isn't set. Stop the server, run `$env:RESULT_SINK = "log"`, then start it again. |
| `No module named waitress` | You ran plain `python`, or tried `activate.bat`. Use `.venv\Scripts\python.exe -m waitress --port=8000 wsgi:app`. |
| `running scripts is disabled on this system` | You ran `Activate.ps1`. You don't need it: use `.venv\Scripts\python.exe` as shown. |
| `status not_ready`, or the model fails to load | Run `dir models\current`. If `model_int8.onnx` is tiny, run `git lfs pull`, then restart the server. |
| Port 8000 already in use | Run `Get-NetTCPConnection -LocalPort 8000 \| Select-Object OwningProcess` to see the process ID, then `Stop-Process -Id <that number>`. Or use another port: `.venv\Scripts\python.exe -m waitress --port=8080 wsgi:app`, and open `http://127.0.0.1:8080/demo`. |
| To check what's set | `$env:DEMO_PAGE` and `$env:RESULT_SINK` print the current values. `Remove-Item Env:DEMO_PAGE` clears one. |
