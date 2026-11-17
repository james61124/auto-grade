"""把 approved.json 中『你核准過』的分數與 comment 回填到 Gradescope。

用法：
  .venv/bin/python scripts/gradescope_filler.py --student S001            # 試跑：只顯示要填什麼，不寫入
  .venv/bin/python scripts/gradescope_filler.py --student S001 --apply    # 實際寫入這一位
  .venv/bin/python scripts/gradescope_filler.py --all --apply             # 寫入所有已核准的學生

每個小題的做法（Gradescope 扣分制）：
  - Point Adjustment 填「總分 − 滿分」（例如 -8；滿分則 0）
  - Comment 填審核介面裡的 comment
  - 滿分時套用 rubric 第 1 項（Correct），讓 Gradescope 標記為已評分
寫入後重新載入頁面，讀回分數與 comment 確認一致。
已有分數 / comment 的頁面預設跳過（避免覆蓋別人改過的），加 --overwrite 才覆寫。
"""
import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright

import config

GS = config.GS
LOG = config.FILL_LOG


def fmt(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def read_state(frame):
    return frame.evaluate("""(s) => {
      const adj = document.querySelector(s.point_adjustment);
      const cm = document.querySelector(s.comment);
      const num = document.querySelector(s.total_numerator);
      const item1 = document.querySelector('button[aria-label="Toggle rubric item 1"]');
      const entry1 = item1 && item1.closest('.rubricEntry, .rubricItem');
      return {adjustment: adj ? adj.value : null, comment: cm ? cm.value : null,
              total: num ? num.innerText.trim() : null,
              ungraded: num ? num.className.includes('is-ungraded') : null,
              item1_class: entry1 ? entry1.className : ''};
    }""", GS)


def open_page(frame, url):
    frame.goto(url)
    frame.wait_for_selector(GS["point_adjustment"], timeout=30000)
    time.sleep(2)


def verify_identity(frame, url, pdf_key):
    """確認目前頁面就是這位學生：網址 submission id 與考卷 id（pdf_key）都要對上。"""
    from fetch_answers import JS_PROPS
    want_sid = url.rstrip("/").split("/submissions/")[1].split("/")[0]
    href = frame.evaluate("() => location.href")
    info = frame.evaluate(JS_PROPS) or {}
    if f"/submissions/{want_sid}/" not in href + "/" \
            or str(info.get("question_submission_id")) != want_sid \
            or str(info.get("assignment_submission_id")) != str(pdf_key):
        raise RuntimeError("身分核對失敗：頁面不是這位學生，已停止，未寫入")


def fill_one(frame, url, total, max_pts, comment, apply, overwrite, pdf_key):
    open_page(frame, url)
    verify_identity(frame, url, pdf_key)
    before = read_state(frame)
    adj = round(total - max_pts, 2)
    plan = {"adjustment": fmt(adj), "comment_lines": len([l for l in comment.splitlines() if l.strip()]),
            "expected_total": fmt(total)}
    already = (before["adjustment"] not in ("", "0", None)) or bool((before["comment"] or "").strip()) \
        or before["ungraded"] is False
    if already and not overwrite:
        return {"status": "skipped_existing", "before": before, "plan": plan}
    if not apply:
        return {"status": "dry_run", "before": before, "plan": plan}

    # 滿分 → 套用 rubric 第 1 項（Correct），讓題目標記為已評分
    if adj == 0 and before["ungraded"]:
        frame.locator('button[aria-label="Toggle rubric item 1"]').click()
        time.sleep(1.5)

    a = frame.locator(GS["point_adjustment"])
    a.scroll_into_view_if_needed(); a.click(force=True)
    a.fill(fmt(adj) if adj != 0 else "0")
    a.press("Tab")
    time.sleep(1.5)

    c = frame.locator(GS["comment"])
    c.scroll_into_view_if_needed(); c.click(force=True)
    c.fill(comment)
    frame.locator(GS["total_numerator"]).click()   # 讓 comment 欄失焦以觸發儲存
    time.sleep(2.5)

    # 重新載入、讀回確認
    open_page(frame, url)
    verify_identity(frame, url, pdf_key)
    after = read_state(frame)
    ok_total = after["total"] is not None and abs(float(after["total"]) - float(total)) < 1e-6
    ok_comment = (after["comment"] or "").strip() == comment.strip()
    return {"status": "ok" if (ok_total and ok_comment) else "mismatch",
            "plan": plan, "after_total": after["total"], "ungraded": after["ungraded"],
            "comment_saved": ok_comment}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--student")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="實際寫入（預設只試跑）")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    rubric = json.loads((config.RUBRIC_DIR / f"{config.QUESTION}.json").read_text(encoding="utf-8"))
    approved = json.loads(config.APPROVED_FILE.read_text(encoding="utf-8"))
    students = {s["student"]: s for s in json.loads(config.STUDENTS_FILE.read_text(encoding="utf-8"))}
    targets = [args.student] if args.student else sorted(approved)
    for t in targets:
        if t not in approved:
            sys.exit(f"{t} 尚未在審核介面核准，不能回填。")

    with sync_playwright() as pw:
        ctx = config.launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CANVAS_ENTRY)
        print("等待 Gradescope 載入 …")
        frame, deadline = None, time.time() + 600
        while frame is None and time.time() < deadline:
            for f in page.frames:
                try:
                    if f.evaluate("() => location.hostname").endswith("gradescope.com"):
                        frame = f
                except Exception:
                    pass
            time.sleep(2)
        if frame is None:
            sys.exit("逾時，沒看到 Gradescope。")

        print("模式：" + ("實際寫入" if args.apply else "試跑（不寫入）"))
        for sid in targets:
            st, ap_ = students[sid], approved[sid]
            for name, meta in rubric["subparts"].items():
                sub = next((v for v in st["subparts"].values()
                            if v["label"].strip().startswith(meta["gradescope_label"])), None)
                if sub is None:
                    print(f"{sid} {meta['gradescope_label']}: 找不到評分頁")
                    continue
                sp = ap_["subparts"][name]
                res = fill_one(frame, sub["grade_url"], sp["total"], meta["max_points"],
                               sp.get("comment_to_student", ""), args.apply, args.overwrite,
                               st["pdf_key"])
                res.update({"student": sid, "subpart": meta["gradescope_label"], "time": time.strftime("%F %T")})
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(res, ensure_ascii=False) + "\n")
                p = res.get("plan", {})
                extra = ""
                if res["status"] in ("ok", "mismatch"):
                    extra = f"→ Gradescope 顯示 {res['after_total']}，comment {'已存' if res['comment_saved'] else '未存'}" \
                            f"{'，仍標示未評分' if res['ungraded'] else ''}"
                elif res["status"] == "skipped_existing":
                    extra = "（頁面已有分數或 comment，跳過；要覆寫請加 --overwrite）"
                print(f"{sid} {meta['gradescope_label']}: {res['status']}  adjustment={p.get('adjustment')} "
                      f"預期總分={p.get('expected_total')} comment {p.get('comment_lines')} 行 {extra}")
            time.sleep(1)
        ctx.close()


if __name__ == "__main__":
    main()
