"""診斷：評分頁的資料裡哪裡有 PDF 網址 / 整份考卷 ID。只印欄位路徑與遮掉數字的值。

用法：.venv/bin/python scripts/diag_pdf.py   （打開任一份評分頁即可）
"""
import json
import re

from playwright.sync_api import sync_playwright

import config


def scrub(v):
    s = str(v)
    s = re.sub(r"\?.*", "?…", s)
    return re.sub(r"\d{3,}", "N", s)[:140]


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


INTEREST = re.compile(r"pdf|assignment_submission|submission_id|^\.?submission\.id$|download|file_url|\.url$", re.I)


def main():
    with sync_playwright() as pw:
        ctx = config.launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CANVAS_ENTRY)
        print("請打開任一份評分頁 …")
        frame = config.wait_for_grading_frame(ctx)
        props_list = frame.evaluate(
            "() => [...document.querySelectorAll('[data-react-class]')]"
            ".map(e => [e.getAttribute('data-react-class'), e.getAttribute('data-react-props')])")
        for cls, raw in props_list:
            try:
                props = json.loads(raw or "{}")
            except Exception:
                continue
            hits = [(p, v) for p, v in walk(props)
                    if INTEREST.search(p) or (isinstance(v, str) and ".pdf" in v.lower())]
            print(f"\n== {cls}：頂層欄位 {sorted(props)[:40]}")
            for p, v in hits[:40]:
                print(f"   {p} = {scrub(v)}")

        # Open PDF 按鈕本身的屬性
        info = frame.evaluate(
            """() => [...document.querySelectorAll('button, a')]
                .filter(e => /pdf/i.test(e.innerText || ''))
                .map(e => ({tag: e.tagName, cls: e.className, href: e.getAttribute('href'),
                            target: e.getAttribute('target'), text: (e.innerText||'').trim().slice(0,20)}))""")
        print("\n== 文字含 PDF 的按鈕/連結：")
        for i in info:
            print("  ", {k: scrub(v) if v else v for k, v in i.items()})
        input("\n按 Enter 關閉 … ")
        ctx.close()


if __name__ == "__main__":
    main()
