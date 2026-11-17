"""從每份考卷 PDF 取出這一題的頁面（config.QUESTION_PAGES），轉成 PNG 給 Gemini。

用法：.venv/bin/python scripts/extract_pages.py [--dpi 200]
輸入：submissions/pdfs/<pdf_key>.pdf
輸出：work/<題號>/pages/<pdf_key>/pNN.png
只印統計，不印內容。
"""
import argparse

import pypdfium2 as pdfium

import config

PDF_DIR = config.PDF_DIR
OUT_DIR = config.PAGES_DIR


def extract(pdf_path, out_dir, first, last, dpi):
    pdf = pdfium.PdfDocument(str(pdf_path))
    n = len(pdf)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for pno in range(first, min(last, n) + 1):
        dest = out_dir / f"p{pno}.png"
        if not dest.exists():
            pdf[pno - 1].render(scale=dpi / 72).to_pil().save(dest)
        saved += 1
    return n, saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()
    first, last = config.PDF_PAGES
    import json
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    students = json.loads(config.STUDENTS_FILE.read_text(encoding="utf-8")) \
        if config.STUDENTS_FILE.exists() else []
    ov = config.PAGE_OVERRIDES.get(config.QUESTION, {})
    override = {str(s["pdf_key"]): ov[s["student"]] for s in students if s["student"] in ov}
    if not pdfs:
        raise SystemExit(f"{PDF_DIR} 裡沒有 PDF，請先跑 fetch_answers.py")
    short, page_counts = [], {}
    for p in pdfs:
        f, l = override.get(p.stem, (first, last))
        if p.stem in override:
            import shutil
            shutil.rmtree(OUT_DIR / p.stem, ignore_errors=True)   # 用新的頁碼重新產生
        n, saved = extract(p, OUT_DIR / p.stem, f, l, args.dpi)
        page_counts[n] = page_counts.get(n, 0) + 1
        if saved < l - f + 1:
            short.append(p.stem)
    print(f"PDF 數：{len(pdfs)}；頁數分布：{dict(sorted(page_counts.items()))}")
    print(f"每份取第 {first}–{last} 頁 → {OUT_DIR}")
    if short:
        print(f"⚠ {len(short)} 份頁數不足，缺部分 {config.QUESTION} 頁面（pdf_key：{', '.join(short)}）")
    if len(page_counts) > 1:
        print(f"⚠ 各份 PDF 頁數不一致，{config.QUESTION} 不一定都在第 {first}–{last} 頁，請抽查。")


if __name__ == "__main__":
    main()
