"""從 Gradescope『該小題所有 submissions』列表頁取得學生姓名，只存在本機。

用法：.venv/bin/python scripts/fetch_names.py
輸出：submissions/names.json  {"S001": "姓名", ...}（只供本機審核介面顯示；不會送給 Claude 或 Gemini）
terminal 只印人數。
"""
import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

import config

JS_ROWS = r"""(qid) => {
  const re = new RegExp('/questions/' + qid + '/submissions/(\\d+)');
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    const m = (a.getAttribute('href') || '').match(re);
    const row = a.closest('tr');
    if (!m || !row || seen.has(m[1])) continue;
    seen.set(m[1], [...row.cells].map(c => (c.innerText || '').trim()));
  }
  return [...seen.entries()];
}"""


def pick_name_column(rows):
    """選出『每列都不同、含字母、不是純數字』的欄位當姓名欄。"""
    if not rows:
        return None
    ncol = min(len(cells) for _, cells in rows)
    best = None
    for c in range(ncol):
        vals = [cells[c] for _, cells in rows]
        distinct = len(set(vals))
        alpha = sum(bool(re.search(r"[^\W\d_]", v)) and not re.fullmatch(r"[\d./\s-]+", v) for v in vals)
        score = (alpha == len(vals), distinct)
        if best is None or score > best[0]:
            best = (score, c)
    return best[1]


def main():
    ids = json.loads(config.IDS_FILE.read_text(encoding="utf-8"))
    students = json.loads(config.STUDENTS_FILE.read_text(encoding="utf-8"))
    # 用這一題第一個小題（例如 1.1）的 submission 列表頁
    labels = {q: v["label"] for s in students for q, v in s["subparts"].items()}
    qid = min(labels, key=lambda q: labels[q])
    by_sub = {s["subparts"][qid]["submission_id"]: s["student"] for s in students if qid in s["subparts"]}

    with sync_playwright() as pw:
        ctx = config.launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CANVAS_ENTRY)
        print("等待 Gradescope 載入（若要求登入請完成登入）…")
        frame, deadline = None, time.time() + 600
        while frame is None and time.time() < deadline:
            for f in page.frames:
                try:
                    if "gradescope" in f.url and f.evaluate("() => location.hostname").endswith("gradescope.com"):
                        frame = f
                except Exception:
                    pass
            time.sleep(2)
        if frame is None:
            sys.exit("逾時，沒看到 Gradescope。")

        frame.goto(f"{ids['origin']}/courses/{ids['course_id']}/questions/{qid}/submissions")
        frame.wait_for_load_state("domcontentloaded")
        time.sleep(3)
        rows = frame.evaluate(JS_ROWS, qid)
        ctx.close()

    col = pick_name_column(rows)
    names = {}
    for sub_id, cells in rows:
        if sub_id in by_sub and col is not None and cells[col]:
            names[by_sub[sub_id]] = cells[col]
    print(f"表格欄數：{min((len(c) for _, c in rows), default=0)}；使用第 {col} 欄當姓名；"
          f"不重複值：{len(set(names.values()))}")
    out = config.NAMES_FILE
    out.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"列表頁連結數：{len(rows)}；對到姓名的學生：{len(names)} / {len(students)}")
    print(f"已存：{out}（只在本機）")


if __name__ == "__main__":
    main()
