# Tidy Agent

![Tidy Agent](docs/banner.jpg)

A local mini agent that keeps your **Downloads** and **Desktop** tidy. It runs on your own PC with Ollama (or the Laya decision model), so no files leave your machine.

## Quickest start (Windows, no download, no Python setup)

Open PowerShell. `uv` brings its own Python; nothing is installed system-wide except Ollama and uv.

1. Install Ollama and uv:
   ```
   winget install Ollama.Ollama astral-sh.uv
   ```
2. **Close PowerShell and open a new window.** The window you installed from doesn't see the new `ollama` and `uvx` commands yet, so in the same window step 3 fails with "not recognized".
3. Download the model and start Tidy:
   ```
   ollama pull qwen3.5:4b
   uvx --from https://github.com/arkenisAI/tidy-agent/archive/refs/heads/main.zip tidy-agent
   ```

Run the last line again whenever you want to open Tidy. (Translate.agent: `uvx --from https://github.com/arkenisAI/translate-agent/archive/refs/heads/main.zip translate-agent`.)

## Quick start (Windows, from the ZIP)

1. Install **Python 3.11 or newer** from python.org. Tick **"Add python.exe to PATH"** during setup.
2. Make sure Ollama is running and has the model:
   ```
   ollama pull qwen3.5:4b
   ```
3. Double-click **`run.bat`**. The first run installs the small extras (a native window and PDF reading), then opens the dashboard.

To run it without a window in the background, use `run_background.bat`, then open http://127.0.0.1:8765 whenever you like.
To start it when Windows starts, run `install_startup.bat` once.

## Quick start (macOS)

1. Install Python 3.11+ (python.org, or `brew install python`) and Ollama for Mac. Then run `ollama pull qwen3.5:4b`.
2. Double-click **`run.command`**. If macOS says it "can't be opened", right-click the file and choose **Open**, then **Open** again. You only need to do this once. Or run this in Terminal:
   ```
   xattr -dr com.apple.quarantine /path/to/tidy-agent
   chmod +x /path/to/tidy-agent/*.command
   ```
3. macOS asks whether Python may access **Desktop** and **Downloads**. Click **Allow**. If you clicked Don't Allow, turn it on in System Settings → Privacy & Security → Files and Folders.

- Background mode: `run_background.command`. The log goes to `data/agent.log`.
- Start at login: `install_startup_mac.command`. It sets up a launchd agent, and the script prints how to remove it.
- On Apple Silicon Macs with 16 GB or more, you can use a bigger model. Set `"model": "qwen3.5:9b"` and `"num_ctx": 8192` in the config.

## Starting it by hand (any OS)

```
cd tidy-agent
python3 -m venv .venv
# Windows:  .venv\Scripts\activate     macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
python app.py                # with window
python app.py --background   # server only -> open http://127.0.0.1:8765
```
Stop it with Ctrl+C in that terminal. If you don't need the native window or PDF reading, you can skip `pip install`, because the app runs on the standard library alone.


## How it works

- Every 10 minutes (or when you press **Scan now**), it looks at the top level of Downloads and Desktop. Folders are never touched.
- Files that were modified in the last 60 s are **scheduled**. This waits for downloads and saves to finish.
- **Rules first:** images, videos, archives, installers, code and so on are sorted by extension instantly.
- **AI for the rest:** documents (PDF, Word, TXT…) and unknown file types go to the local model. It answers with a single JSON label such as `Documents/Invoices & Receipts`, and the app's own code does the actual move.
- Files go to `Downloads\Sorted\<Category>` and `Desktop\Sorted\<Category>`.
- **Review first** (default): every proposal waits on the dashboard for **Move** or **Leave it**. You can also change the category before moving.
- **Autopilot:** rule matches and confident AI guesses are moved automatically. Unsure guesses (below 60 %) still wait for you.
- Every move can be **undone**. Name clashes get ` (1)` added instead of overwriting.

## Your history is kept outside the app folder

History (every file scanned, moved, skipped or waiting) and settings live here, not inside `tidy-agent/`:

| OS | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\TidyAgent` |
| macOS | `~/Library/Application Support/TidyAgent` |

Run `python app.py --where` to print the exact path. A daily backup of the history is kept in `backups/` (the last 7 days).

**Updating to a new version:**
- **Unzip over the old folder** (or into the same place): nothing to do. On first start, the old `data/` folder is copied over automatically. The old copy is left untouched as a spare.
- **Unzip somewhere else:** run this once:
  ```
  python app.py --import-from "C:\path\to\old\tidy-agent"
  ```
  This never overwrites a history that already has entries.

**Files that are already sorted are recognised.** On every scan the agent indexes what's already in the `Sorted` folders, including files you put there by hand. If an identical file shows up again (a re-download, or `invoice (2).pdf`), it gets the same folder straight away. The AI isn't asked, so this works even when Ollama is off. These files show as **"Seen before"**. Files that were already in `Sorted` are counted as "already sorted", not as the agent's own work.

## Settings — `config.json` (in the folder above)

Created on the first run. Edit it, then restart the app.

| Key | Default | What it does |
|---|---|---|
| `backend` | `ollama` | `ollama` or `laya` (see below) |
| `model` | `qwen3.5:4b` | Ollama model name |
| `num_ctx` | `4096` | Keep it low on a 4 GB GPU |
| `mode` | `review` | `review` or `auto` (also switchable in the UI) |
| `scan_interval_minutes` | `10` | How often it scans |
| `settle_seconds` | `60` | How long a file must be untouched before it's sorted |
| `confidence_threshold` | `0.6` | Autopilot only moves AI guesses above this |
| `sources` | Downloads, Desktop | Add more folders: `{"name", "path", "dest_root"}` |

## Using Laya instead of Ollama

```
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\pip install laya
```
Then set `"backend": "laya"` in `config.json` (run `python app.py --where` to find it). Laya is an open-source (Apache 2.0) decision model; its multilingual checkpoint is ~322M parameters and that runs on the CPU. It's newer and less proven than Qwen, so try it in Review mode first.

## Mascots

You can have several mascots and pick one in the dashboard (the **Mascot** menu under the speech bubble). Your choice is saved.

Two mascots ship with the app, **Boxbot** and **Mochibot**, with all their poses in `static/assets/`.

Put images in `static/assets/` using this naming rule:

```
mascot_<name>.png             main image (optional)
mascot_<name>_<state>.png     one image per state
```

| State | When it's shown | Also accepted |
|---|---|---|
| `idle` | nothing to do | `stand`, `rest`, `default` |
| `working` | sorting a file | `work`, `busy`, `sorting`, `run` |
| `happy` | just finished a file | `done`, `jump`, `celebrate`, `success` |
| `waiting` | files are waiting for your OK | `wait`, `review`, `ask`, `question` |
| `sleep` | paused | `sleeping`, `nap`, `pause`, `paused` |

Examples: `mascot_boxbot_idle.png`, `mascot_boxbot_working.png`, `mascot_boxbot_done.gif`, `mascot_ko_kart_sleep.png`.

- The name may contain `_` (e.g. `ko_kart`), and `-` also works as a separator (`mascot-mochi-wait.png`).
- Formats: PNG, WebP, GIF/APNG (animated works), JPG, SVG.
- A missing state falls back to `idle`, then to the main image. One image is enough to get started.
- The old style (`mascot.png`, `mascot_working.png`, …) still works and appears as **Default**.
- `mascot_options.png` (a contact sheet) is ignored.
- New files appear in the menu within a couple of seconds. No restart needed.

**Fluffy robot (built-in)** is always available in the menu.

## Categories, refresh and delete

- **Your own categories:** add them in the **Your categories** card, or choose **+ New category…** in a file's dropdown. Use `Parent/Child` for a sub-folder (one level). Optional **keywords** (matched in the file name, e.g. `dekont, ekstre`) and **extensions** are instant rules that win over the built-in ones. The optional **description** helps the AI choose it. Removing a category doesn't move files that are already in its folder.
- **Picking a folder** in the review list is saved right away, so it survives refreshes, restarts and **Approve all**.
- **Refresh** re-reads the folders and updates the history to match what's actually there now:
  - Sorted files you **deleted** are marked *removed*.
  - Files you **moved to another Sorted folder** have their entry follow them.
  - Files you **dragged back** into Downloads/Desktop are left alone. They won't be sorted again.
  - Files you **sorted by hand** are added as *already sorted*.
  - Waiting files you deleted are dropped from the queue.

  This also runs automatically before every scan.
- **Delete** sends the file to the **Recycle Bin / Trash**, so you can restore it. It asks *Sure?* first; click again within 3 seconds to confirm. It only works on files inside the watched folders.
- **✕** and **Clear removed** only tidy up the list. They never touch files.

## Troubleshooting

- **"AI offline"**: start Ollama. Rule-based files keep being sorted while it's offline. AI files retry three times, then wait for review.
- **"Model missing"**: run `ollama pull qwen3.5:4b`, or change `model` in the config.
- **"File is open in another program"**: close the file, then press **Retry**.
- **Port 8765 busy**: the app is already running and will open the existing window. To change the port, edit `port` in the config.

Sister app: [translate-agent](https://github.com/arkenisAI/translate-agent), a local translator on the same Ollama.

## For AI agents (Claude Code, Codex, …)

Read this before you install or run the apps for a user.

**Install (Windows PowerShell)**
```
winget install Ollama.Ollama astral-sh.uv --accept-source-agreements --accept-package-agreements
# your shell won't see the new commands yet: reload PATH instead of asking for a new window
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
ollama pull qwen3.5:4b
uvx --from https://github.com/arkenisAI/tidy-agent/archive/refs/heads/main.zip tidy-agent
```
On macOS: `brew install ollama uv`, then the last two lines. `uv` downloads its own Python; don't install Python separately.

**Check it works**
- `uvx --from https://github.com/arkenisAI/translate-agent/archive/refs/heads/main.zip translate-agent --selftest` prints `selftest ok`.
- `uvx --from https://github.com/arkenisAI/tidy-agent/archive/refs/heads/main.zip tidy-agent --where` prints the folder that holds `config.json` and the history database.
- Ollama answers at `http://127.0.0.1:11434/api/tags`.

**Where things run**
| App | Address | Change the port |
|---|---|---|
| Tidy | `http://127.0.0.1:8765` | `port` in `config.json` |
| Translate.agent | `http://127.0.0.1:8766` | `--port N` |

Both listen on 127.0.0.1 only. They refuse requests whose `Host` isn't `127.0.0.1:<port>` or `localhost:<port>`, and requests with any other `Origin`. Call them from the same machine without an `Origin` header.

**Tidy API** (JSON body, all `POST` except the first)
- `GET /api/state`: counts, the review queue (`review`), recent activity, categories, mode.
- `/api/scan`, `/api/pause`, `/api/resume`
- `/api/mode` `{"mode": "review" | "auto"}`
- `/api/approve` `{"id": N, "category": "<one of state.categories>"}`, `/api/reject` `{"id": N}`, `/api/undo` `{"id": N}`
- `/api/trash` `{"id": N}` sends the file to the Recycle Bin / Trash, never a permanent delete.

**Translate API**: `POST /api/run` `{"model": "qwen3.5:4b", "mode": "translate" | "word" | "sentence" | "synonyms", "src": "Auto", "tgt": "English", "text": "..."}` streams plain text. `GET /api/models` lists models and their languages.

**Rules**
- Ask the user before you switch Tidy to Autopilot (`mode: auto`), approve or trash files, or add folders to `sources` in `config.json`. Tidy moves real files.
- Keep both apps on 127.0.0.1. Don't put them behind a tunnel or bind them to `0.0.0.0`.
- Don't send file contents anywhere else. The point of the apps is that nothing leaves the machine.
- To repeat the benchmark: `python bench/kiyas.py` (builds the 24-file test set, writes `bench/KIYAS.md`).

## License

MIT, see [LICENSE](LICENSE). The software comes with no warranty.
