"""探測 Gradescope Q2 評分頁的結構。

只輸出結構資訊（標籤、class、屬性名稱、UI 按鈕文字），
不輸出學生姓名、答案內容或 rubric 內文。輸出存在 grading/probe_output.txt，
請先自己看過再貼給 Claude。

用法：  python scripts/probe_gradescope.py
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

import config

# 只有這些 UI 字眼會保留原文，其他文字一律以長度代替
UI_WORDS = re.compile(
    r"^(next|previous|prev|back|save|submit|comment|comments|adjust|adjustment|point|points|"
    r"ungraded|grade|grading|rubric|submission|submissions|apply|add|cancel|done|edit|"
    r"score|total|zoom|rotate|下一|上一|儲存|送出)\b",
    re.I,
)
ID_RE = re.compile(r"\d{3,}")

JS_DUMP = r"""
() => {
  const sel = 'button, a[href], input, textarea, select, img, canvas, iframe, [role], [contenteditable], ' +
              '[class*=rubric i], [class*=submission i], [class*=comment i], [class*=adjust i], ' +
              '[class*=score i], [class*=point i], [class*=page i], [data-react-class]';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const attrs = {};
    for (const a of el.attributes) {
      if (['id','class','role','type','name','aria-label','placeholder','title','data-react-class','contenteditable'].includes(a.name))
        attrs[a.name] = a.value.slice(0, 120);
      else if (a.name.startsWith('data-')) attrs[a.name] = '<…>';
    }
    if (el.tagName === 'IMG') attrs.src = (el.getAttribute('src') || '').replace(/\?.*/, '?…');
    if (el.tagName === 'A') attrs.href = (el.getAttribute('href') || '').replace(/\?.*/, '?…');
    const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
    out.push({tag: el.tagName.toLowerCase(), attrs, text: text.slice(0, 40), textLen: text.length,
              box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
  }
  return out;
}
"""


def scrub(s):
    return ID_RE.sub("N", s) if isinstance(s, str) else s


def safe_text(item):
    t = item["text"]
    if t and UI_WORDS.match(t) and item["textLen"] <= 30:
        return scrub(t)
    return f"<text len {item['textLen']}>" if item["textLen"] else ""


def main():
    with sync_playwright() as pw:
        ctx = config.launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CANVAS_ENTRY)
        print("\n請在『剛剛自動跳出來的 Chrome 視窗』裡操作（不是你平常的 Chrome）：")
        print("1. 登入 Rice SSO + Duo")
        print("2. 進入 Gradescope → 打開任一題任一份 submission 的評分頁")
        print("偵測到評分頁會自動繼續（最多等 15 分鐘）…")
        import time
        deadline = time.time() + 900

        def grading_frame():
            """回傳正在顯示評分頁的 Gradescope frame（以頁面上有 Point Adjustment 判斷）。"""
            for p in ctx.pages:
                for f in p.frames:
                    if "gradescope" not in f.url:
                        continue
                    try:
                        if f.evaluate("() => /Point Adjustment/i.test(document.body.innerText)"):
                            return f
                    except Exception:
                        pass
            return None

        target, last_dump = None, 0
        while time.time() < deadline:
            target = grading_frame()
            if target:
                break
            if time.time() - last_dump > 20:
                last_dump = time.time()
                print("  …還在等評分頁（需要看到 Point Adjustment 欄位）")
            time.sleep(2)
        if target is None:
            print("逾時，沒偵測到評分頁。")
            ctx.close()
            sys.exit(1)
        print("偵測到評分頁，等 3 秒讓頁面載入完 …")
        time.sleep(3)
        page = target
        url = page.evaluate("() => location.href")   # iframe 內真正的網址
        m = re.search(r"/courses/(\d+)/questions/(\d+)/submissions/(\d+)", url)
        if m:
            ids = {"origin": re.match(r"https?://[^/]+", url).group(0),
                   "course_id": m.group(1), "question_id": m.group(2)}
            config.IDS_FILE.write_text(json.dumps(ids, indent=2), encoding="utf-8")
            print(f"已存 course/question ID 到 {config.IDS_FILE}")

        items = page.evaluate(JS_DUMP)
        lines = [f"URL pattern: {scrub(url.split('?')[0])}",
                 "child frames: " + ", ".join(scrub(c.url.split("?")[0])[:80] for c in page.child_frames), f"elements: {len(items)}", ""]
        for it in items:
            a = {k: scrub(v) for k, v in it["attrs"].items()}
            lines.append(f"{it['tag']:9} box={it['box']} text={safe_text(it)!r} {json.dumps(a, ensure_ascii=False)}")
        out = config.ROOT / "probe_output.txt"
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n完成：{out}\n請先自己看過，確認沒有學生資訊，再貼給 Claude。")
        input("按 Enter 關閉瀏覽器 … ")
        ctx.close()


if __name__ == "__main__":
    main()
