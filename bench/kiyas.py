#!/usr/bin/env python3
"""Qwen (Ollama) vs Laya on the same labelled files, using Tidy's own classify(). Moves nothing.
usage:  python bench/kiyas.py                      both backends, model from config (qwen3.5:4b)
        python bench/kiyas.py --only ollama --model qwen3.5:4b
        python bench/kiyas.py --selftest
Writes bench/dataset/ (24 made-up documents, no personal data) and bench/KIYAS.md (results table)."""
import argparse, json, statistics, sys, time, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from organizer.classifier import LLMError, classify  # noqa: E402
from organizer.config import default_config  # noqa: E402

INV, WORK, PERS, SCH, BOOK, OTH = ("Documents/Invoices & Receipts", "Documents/Work", "Documents/Personal",
                                   "Documents/School & Learning", "Documents/Ebooks & Manuals", "Documents/Other")
# (file name, expected folder, text). Some names are meaningless on purpose so the model must read the text.
DATA = [
 ("fatura_2026_10.txt", INV, "E-ARŞİV FATURA\nFatura No: GIB2026000001234\nTarih: 12.10.2026\nHizmet: İnternet hizmet bedeli\nKDV %20: 66,00 TL\nÖdenecek tutar: 396,00 TL"),
 ("scan_0012.txt", INV, "RECEIPT\nCoffee Corner\n2x Latte 9.00\nTotal 9.00 EUR\nPaid by card\nThank you!"),
 ("dekont.md", INV, "Para transferi dekontu\nTutar: 1.250,00 TL\nİşlem tarihi: 03.11.2026\nAçıklama: kira"),
 ("order-confirmation.docx", INV, "Order #A-77812 confirmed. Items: USB-C cable x2. Subtotal 18.98 USD, tax 1.52 USD, total 20.50 USD."),
 ("toplanti_notlari.txt", WORK, "Proje toplantısı, 4 Kasım\nKatılımcılar: ürün, yazılım, tasarım\nKarar: sürüm 1.2 cuma çıkacak\nAksiyon: test planı güncellenecek"),
 ("Q3_report_final.docx", WORK, "Quarterly Report Q3 2026. Revenue grew 12% quarter over quarter. Key risks: supplier delays. Next steps: hire two support engineers."),
 ("hizmet_sozlesmesi.txt", WORK, "HİZMET SÖZLEŞMESİ\nMadde 1: Taraflar\nMadde 2: Sözleşmenin konusu web sitesi bakım hizmetidir.\nMadde 3: Süre 12 aydır."),
 ("proposal_v2.md", WORK, "Project proposal: website redesign\nScope: 6 pages, CMS migration\nTimeline: 5 weeks\nBudget: 4,800 EUR"),
 ("CV_2026.docx", PERS, "Curriculum Vitae. Experience: software developer, 2019-2026. Education: BSc Computer Engineering. Skills: Python, Flutter, SQL."),
 ("ozgecmis.txt", PERS, "ÖZGEÇMİŞ\nDeneyim: muhasebe uzmanı (2018-2025)\nEğitim: işletme lisans\nYetenekler: Excel, SAP"),
 ("boarding_pass.txt", PERS, "BOARDING PASS\nFlight TK 1975 IST to AMS\nSeat 14C, gate B12\nBoarding 07:35"),
 ("tahlil_sonuclari.md", PERS, "Kan tahlili sonuç raporu\nHemoglobin: 14.1 g/dL (referans 13-17)\nGlukoz: 92 mg/dL\nDoktor notu: 6 ay sonra kontrol"),
 ("odev3_fizik.docx", SCH, "Fizik 101, Ödev 3. Soru 1: 2 kg kütleli cisme 10 N kuvvet uygulanıyor. İvmeyi bulunuz. Teslim: 20 Kasım."),
 ("lecture_05_notes.md", SCH, "Lecture 5: Linear Algebra\nEigenvalues and eigenvectors\nExample: A = [[2,0],[0,3]]\nHomework: exercises 5.1-5.4"),
 ("vize_calisma.txt", SCH, "Vize çalışma notları: Mikroekonomi\nArz-talep dengesi\nEsneklik formülü\nÖrnek sorular sayfa 45-60"),
 ("exam_schedule.txt", SCH, "Final exam schedule, fall 2026\nMATH 201: Dec 15, 09:00, room A3\nCS 105: Dec 18, 13:00, lab 2"),
 ("manual_EN.txt", BOOK, "User manual: wireless router AX3000\n1. Safety instructions\n2. Connecting to the modem\n3. Factory reset: hold the reset button for 10 seconds"),
 ("kullanim_kilavuzu.docx", BOOK, "Çamaşır makinesi kullanım kılavuzu. Program seçimi. Bakım ve temizlik. Hata kodları: E01 su girişi."),
 ("chapter1.md", BOOK, "Chapter 1: Getting started\nIn this chapter you will install Python and write your first program. By the end of the book you will build three projects."),
 ("doc_5531.txt", BOOK, "Quick start guide\nStep 1: charge the headphones for 2 hours\nStep 2: hold the power button to pair\nWarranty information on page 12"),
 ("notlar.txt", OTH, "süt\nyumurta\nekmek\nçamaşır suyu"),
 ("todo.md", OTH, "- call plumber\n- water the plants\n- book haircut"),
 ("lyrics_draft.txt", OTH, "Verse 1\nneon lights on an empty road\nChorus: drive, drive, drive"),
 ("untitled.txt", OTH, "asdf test 123"),
]

def write_docx(path, text):
    """Smallest .docx Word opens; Tidy only reads word/document.xml."""
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + esc + '</w:t></w:r></w:p></w:body></w:document>')

def build_dataset(d):
    d.mkdir(parents=True, exist_ok=True)
    for name, _, text in DATA:
        (write_docx(d / name, text) if name.endswith(".docx") else (d / name).write_text(text, "utf-8"))
    (d / "labels.json").write_text(json.dumps({n: c for n, c, _ in DATA}, ensure_ascii=False, indent=1), "utf-8")

def run(backend, model, d):
    cfg = default_config(); cfg.update(backend=backend, model=model)
    try:  # warm-up: first call loads the model, keep it out of the timing
        classify(d / DATA[0][0], cfg)
    except LLMError as e:
        return {"backend": backend, "error": str(e), "rows": []}
    rows = []
    for name, want, _ in DATA:
        t0 = time.perf_counter()
        try:
            cat, conf, method, reason = classify(d / name, cfg)
        except LLMError as e:
            cat, conf, method, reason = "ERROR", 0.0, "error", str(e)
        rows.append({"file": name, "want": want, "got": cat, "ok": cat == want, "conf": round(conf, 2),
                     "sec": round(time.perf_counter() - t0, 2), "reason": reason})
        print(f"  {backend:6} {'✓' if cat == want else '✗'} {name:26} {cat} ({conf:.2f}, {rows[-1]['sec']} s)")
    return {"backend": backend, "model": model if backend == "ollama" else "laya", "rows": rows}

def summary(r):
    rows = r["rows"]
    if not rows:
        return f"| {r['backend']} | - | - | - | - | {r.get('error', '')} |"
    ok = sum(x["ok"] for x in rows); secs = [x["sec"] for x in rows]
    return (f"| {r['model']} | {ok}/{len(rows)} (%{100 * ok // len(rows)}) | {statistics.mean(secs):.1f} s | "
            f"{statistics.median(secs):.1f} s | %{100 * statistics.mean(x['conf'] for x in rows):.0f} | |")

def report(results, out):
    lines = ["# Tidy kıyas: aynı 24 belge", "", "| Model | Doğru | Ort. süre/dosya | Medyan | Ort. güven | Not |", "|---|---|---|---|---|---|"]
    lines += [summary(r) for r in results]
    names = [r for r in results if r["rows"]]
    if names:
        lines += ["", "| Dosya | Beklenen | " + " | ".join(r["model"] for r in names) + " |", "|---|---|" + "---|" * len(names)]
        for i, (n, want, _) in enumerate(DATA):
            cells = [("✓ " if r["rows"][i]["ok"] else "✗ ") + r["rows"][i]["got"].replace("Documents/", "") for r in names]
            lines.append(f"| {n} | {want.replace('Documents/', '')} | " + " | ".join(cells) + " |")
    out.write_text("\n".join(lines) + "\n", "utf-8"); print("\n".join(lines[:4 + len(results)]))

def selftest():
    assert len(DATA) == 24 and len({n for n, _, _ in DATA}) == 24
    assert all(sum(c == k for _, c, _ in DATA) == 4 for k in (INV, WORK, PERS, SCH, BOOK, OTH))
    import tempfile
    d = Path(tempfile.mkdtemp())
    build_dataset(d)
    from organizer.classifier import _excerpt, needs_ai
    assert all(needs_ai(d / n) for n, _, _ in DATA), "every file must go to the model, not a rule"
    assert "USB-C" in _excerpt(d / "order-confirmation.docx") and "Kan tahlili" in _excerpt(d / "tahlil_sonuclari.md")
    fake = {"backend": "ollama", "model": "m", "rows": [{"ok": i < 18, "sec": 1.0, "conf": 0.5, "got": "x"} for i in range(24)]}
    assert "18/24 (%75)" in summary(fake)
    print("selftest ok")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["ollama", "laya"]); ap.add_argument("--model"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit()
    d = HERE / "dataset"; build_dataset(d)
    model = a.model or default_config()["model"]
    results = [run(b, model, d) for b in (["ollama", "laya"] if not a.only else [a.only])]
    report(results, HERE / "KIYAS.md")
