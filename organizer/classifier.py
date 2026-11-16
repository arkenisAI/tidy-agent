"""Decides which folder a file belongs in.

Cheap rules handle most files (by extension). The local model is only asked about
documents and unknown file types, and only has to return a JSON label.
"""
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CATEGORIES = [
    "Images", "Images/Screenshots", "Videos", "Audio", "Archives", "Installers",
    "Code", "Spreadsheets", "Presentations", "Fonts", "Design & 3D",
    "Documents/Invoices & Receipts", "Documents/Work", "Documents/Personal",
    "Documents/School & Learning", "Documents/Ebooks & Manuals", "Documents/Other",
    "Other",
]
DOC_CATEGORIES = [c for c in CATEGORIES if c.startswith("Documents/")]

EXT_RULES = {
    "Images": ".jpg .jpeg .png .gif .webp .bmp .tif .tiff .heic .heif .svg .ico .raw .cr2 .nef .avif",
    "Videos": ".mp4 .mkv .mov .avi .wmv .webm .flv .m4v .3gp .mpg .mpeg",
    "Audio": ".mp3 .wav .flac .aac .ogg .m4a .wma .opus .mid .midi",
    "Archives": ".zip .rar .7z .tar .gz .tgz .bz2 .xz .iso",
    "Installers": ".exe .msi .msix .appx .dmg .pkg .apk .aab .deb .rpm",
    "Code": ".py .js .ts .tsx .jsx .json .html .css .java .kt .swift .dart .c .cpp .h .cs"
            " .go .rs .rb .php .sh .bat .ps1 .sql .yaml .yml .xml .ipynb .toml .gradle",
    "Spreadsheets": ".xls .xlsx .xlsm .csv .ods .numbers .tsv",
    "Presentations": ".ppt .pptx .key .odp",
    "Fonts": ".ttf .otf .woff .woff2",
    "Design & 3D": ".psd .ai .fig .sketch .xd .blend .obj .fbx .stl .indd .afdesign .afphoto",
    "Documents/Ebooks & Manuals": ".epub .mobi .azw .azw3",
}
EXT_TO_CATEGORY = {ext: cat for cat, exts in EXT_RULES.items() for ext in exts.split()}
AI_DOC_EXT = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pages"}
SCREENSHOT_RE = re.compile(r"^(screenshot|screen shot|ekran g[öo]r[üu]nt[üu]s[üu]|snip|capture)",
                           re.IGNORECASE)

SYSTEM_PROMPT = (
    "You sort a user's files into folders. Pick the single best category from the list. "
    "Judge by the file name, extension and text excerpt. Names may be Turkish or English: "
    "fatura/makbuz/dekont/receipt/invoice -> Invoices & Receipts; sozlesme/contract/report/"
    "proposal/meeting -> Work; CV/resume/ID/tickets/health/personal letters -> Personal; "
    "odev/lecture/course/notes/exam -> School & Learning; manuals, guides, books -> Ebooks & Manuals. "
    "If you cannot tell, use the 'Other' option with low confidence. "
    "confidence is 0.0-1.0. reason is at most 10 words."
)


class LLMError(Exception):
    pass


def _excerpt(path: Path, limit=600) -> str:
    ext = path.suffix.lower()
    text = ""
    try:
        if ext in {".txt", ".md", ".csv", ".rtf"}:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read(limit * 2)
        elif ext in {".docx", ".odt"}:
            member = "word/document.xml" if ext == ".docx" else "content.xml"
            with zipfile.ZipFile(path) as zf:
                xml = zf.read(member).decode("utf-8", errors="ignore")[:200_000]
            text = re.sub(r"<[^>]+>", " ", xml)
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader  # optional dependency

                reader = PdfReader(str(path))
                text = (reader.pages[0].extract_text() or "") if reader.pages else ""
            except Exception:
                text = ""
    except Exception:
        text = ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _ask_model(path: Path, allowed, cfg, hints=None) -> tuple:
    size_kb = max(1, path.stat().st_size // 1024)
    excerpt = _excerpt(path) or "(no readable text)"
    hints = hints or {}

    def line(c):  # built-ins are explained in the system prompt; describe the user's own ones
        h = hints.get(c) if c not in CATEGORIES else None
        return f"- {c}: {h}" if h else f"- {c}"

    user = (
        "Categories:\n" + "\n".join(line(c) for c in allowed) +
        f"\n\nFile name: {path.name}\nExtension: {path.suffix.lower() or '(none)'}"
        f"\nSize: {size_kb} KB\nText excerpt: \"\"\"{excerpt}\"\"\""
    )
    schema = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": allowed},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["category", "confidence", "reason"],
    }
    payload = {
        "model": cfg["model"],
        "stream": False,
        "think": False,
        "format": schema,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": cfg.get("num_ctx", 4096), "num_predict": 80},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    }
    data = _post(cfg["ollama_url"] + "/api/chat", payload)
    try:
        content = data["message"]["content"]
        result = json.loads(content)
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"Model returned something that isn't JSON: {exc}")
    category = result.get("category")
    if category not in allowed:
        category = allowed[-1]
    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(result.get("reason", ""))[:120]
    return category, confidence, "ai", reason


def _post(url, payload, timeout=180):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        # Older Ollama builds reject the "think" field - retry once without it.
        if exc.code == 400 and "think" in detail and "think" in payload:
            payload = dict(payload)
            payload.pop("think")
            return _post(url, payload, timeout)
        raise LLMError(f"Ollama error {exc.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise LLMError(f"Can't reach Ollama: {exc}")


CATEGORY_HINTS = {
    "Documents/Invoices & Receipts": "Invoices, bills, receipts, payment confirmations (fatura, makbuz, dekont)",
    "Documents/Work": "Contracts, reports, proposals, meeting notes (sozlesme, rapor)",
    "Documents/Personal": "CV, resume, ID copies, tickets, personal letters",
    "Documents/School & Learning": "Homework, lecture notes, courses, exams (odev, ders)",
    "Documents/Ebooks & Manuals": "Books, user manuals, guides",
    "Documents/Other": "Any other document",
    "Other": "Anything that fits nowhere else",
}

_laya_router = None


def _ask_laya(path: Path, allowed, cfg, hints=None) -> tuple:
    """Optional backend: Laya decision model (pip install laya). Small, multilingual, CPU-friendly."""
    global _laya_router
    try:
        from laya import Router
    except ImportError:
        raise LLMError("Laya is not installed - run: pip install laya")
    try:
        if _laya_router is None:
            _laya_router = Router(preload=True)
        questions = {"category": {
            "type": "choice",
            "instructions": "Which folder should this file be sorted into?",
            "criteria": {c: (hints or CATEGORY_HINTS).get(c) or c for c in allowed},
        }}
        state = {"file_name": path.name, "extension": path.suffix.lower(),
                 "text_excerpt": _excerpt(path, 400)}
        answer = _laya_router.predict(state, questions)["answers"]["category"]
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"Laya failed: {exc}")
    category = answer.get("choice") if isinstance(answer, dict) else answer
    if category not in allowed:
        category = allowed[-1]
    confidence = 0.0
    if isinstance(answer, dict):
        for key in ("confidence", "probability", "score"):
            if isinstance(answer.get(key), (int, float)):
                confidence = float(answer[key])
                break
        else:
            probs = answer.get("probabilities")
            if isinstance(probs, dict) and probs:
                confidence = float(max(probs.values()))
    return category, max(0.0, min(1.0, confidence)), "ai", "Laya decision model"


# ---------- custom categories ----------
_BAD_CHARS = set('\\:*?"<>|')


def clean_category_name(name: str) -> str:
    """'Work / Clients ' -> 'Work/Clients'. Raises ValueError if it can't be a folder path."""
    parts = [p.strip() for p in str(name or "").replace("\\", "/").split("/") if p.strip()]
    if not parts:
        raise ValueError("Give the category a name")
    if len(parts) > 2:
        raise ValueError("Use at most one sub-folder, e.g. Work/Clients")
    for p in parts:
        if p in (".", "..") or any(c in _BAD_CHARS for c in p) or p.endswith("."):
            raise ValueError(f'"{p}" can\'t be used as a folder name')
    cleaned = "/".join(parts)
    if len(cleaned) > 60:
        raise ValueError("Keep the name under 60 characters")
    return cleaned


def _split_list(value):
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip().lower() for v in (value or []) if str(v).strip()]


def custom_categories(cfg) -> list:
    out = []
    for c in cfg.get("custom_categories", []) or []:
        try:
            name = clean_category_name(c.get("name"))
        except ValueError:
            continue
        exts = [e if e.startswith(".") else "." + e for e in _split_list(c.get("extensions"))]
        out.append({"name": name, "hint": str(c.get("hint") or "").strip(),
                    "keywords": _split_list(c.get("keywords")), "extensions": exts})
    return out


def all_categories(cfg) -> list:
    """Built-in + custom, with the catch-all 'Other' kept last."""
    names = [c for c in CATEGORIES if c != "Other"]
    for c in custom_categories(cfg):
        if c["name"] not in names:
            names.append(c["name"])
    return names + ["Other"]


def _allowed_for(ext, cfg):
    custom = [c["name"] for c in custom_categories(cfg) if c["name"] not in CATEGORIES]
    if ext in AI_DOC_EXT:  # documents: document folders + custom ones, "Documents/Other" last
        docs = [c for c in DOC_CATEGORIES if c != "Documents/Other"]
        return docs + custom + ["Documents/Other"]
    return all_categories(cfg)


def _custom_rule(path: Path, cfg):
    """Your own categories win over the built-in rules when their keyword or extension matches."""
    low_name = path.name.lower()
    ext = path.suffix.lower()
    for c in custom_categories(cfg):
        for kw in c["keywords"]:
            if kw and kw in low_name:
                return c["name"], f'name contains "{kw}"'
        if ext and ext in c["extensions"]:
            return c["name"], f"{ext} file"
    return None


def classify(path: Path, cfg) -> tuple:
    """Returns (category, confidence, method, reason). Raises LLMError if the AI is needed but down."""
    ext = path.suffix.lower()
    custom = _custom_rule(path, cfg)
    if custom:
        return custom[0], 1.0, "rule", custom[1]
    if ext in EXT_TO_CATEGORY:
        category = EXT_TO_CATEGORY[ext]
        if category == "Images" and SCREENSHOT_RE.match(path.stem):
            return "Images/Screenshots", 1.0, "rule", "Screenshot file name"
        return category, 1.0, "rule", f"{ext} file"
    allowed = _allowed_for(ext, cfg)
    hints = dict(CATEGORY_HINTS)
    hints.update({c["name"]: c["hint"] for c in custom_categories(cfg) if c["hint"]})
    if cfg.get("backend") == "laya":
        return _ask_laya(path, allowed, cfg, hints)
    return _ask_model(path, allowed, cfg, hints)


def needs_ai(path: Path) -> bool:
    return path.suffix.lower() not in EXT_TO_CATEGORY


_status_cache = {"at": 0.0, "value": None}


def ollama_status(cfg) -> dict:
    """Cheap health check, cached for 10 s."""
    now = time.time()
    if _status_cache["value"] and now - _status_cache["at"] < 10:
        return _status_cache["value"]
    value = {"online": False, "model_ready": False, "model": cfg["model"]}
    try:
        with urllib.request.urlopen(cfg["ollama_url"] + "/api/tags", timeout=2) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = {m.get("name", "") for m in tags.get("models", [])}
        value["online"] = True
        want = cfg["model"]
        value["model_ready"] = want in names or f"{want}:latest" in names
    except Exception:
        pass
    _status_cache.update(at=now, value=value)
    return value
