"""從 Gradescope 評分頁逐份抓學生答案（唯讀，不改任何分數）。

用法：
  .venv/bin/python scripts/fetch_answers.py            # 抓目前打開的這一小題的所有 submission
  .venv/bin/python scripts/fetch_answers.py --limit 3  # 先試抓 3 份

操作：在跳出的 Chrome 打開某一小題（例如 2.1）的「第一份」評分頁，程式會自動開始，
一路按 Next 直到最後一份。可隨時 Ctrl+C，重跑會跳過已抓過的。

三個小題（2.1 / 2.2 / 2.3）各跑一次。同一張照片只下載一次。

輸出（都在本機，不含學生姓名）：
  submissions/pages/<page_key>.jpg          原始整頁圖片（page_key = Gradescope 頁面檔案 ID）
  submissions/q<question_id>/<sid>_viewer.png  評分頁畫面截圖（對照用）
  submissions/manifest.jsonl                每份一行：小題、submission、評分網址、page_keys
"""
import argparse
import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

import config

GS = config.GS
MANIFEST = config.MANIFEST
URL_RE = re.compile(r"/courses/(\d+)/questions/(\d+)/submissions/(\d+)")


def current_ids(frame):
    href = frame.evaluate("() => location.href")
    m = URL_RE.search(href)
    if not m:
        raise RuntimeError("目前頁面不是評分頁")
    return m.group(2), m.group(3), href.split("?")[0]


def wait_images_loaded(frame, timeout_s=20):
    frame.wait_for_selector(GS["page_img"], state="attached", timeout=timeout_s * 1000)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        done = frame.evaluate(
            "(s) => { const imgs=[...document.querySelectorAll(s)];"
            " return imgs.length>0 && imgs.every(i => i.complete && i.naturalWidth>0); }",
            GS["page_img"])
        if done:
            return
        time.sleep(0.5)
    print("  （圖片載入逾時，仍嘗試儲存）")


PAGES_DIR = config.SUBMISSIONS_DIR / "pages"
PAGE_KEY_RE = re.compile(r"/uploads/page/file/(\d+)/")


def page_key(src):
    """Gradescope 每張頁面圖片的檔案 ID：同一位學生在各小題看到的是同一個 ID。"""
    m = PAGE_KEY_RE.search(src)
    return m.group(1) if m else re.sub(r"\W+", "_", src.split("?")[0])[-60:]


PDF_DIR = config.PDF_DIR

JS_PROPS = """() => {
  const el = document.querySelector('[data-react-class="SubmissionGrader"]');
  if (!el) return null;
  const p = JSON.parse(el.getAttribute('data-react-props') || '{}');
  return {pdf_url: p.pdf_url || null,
          assignment_submission_id: (p.assignment_submission && p.assignment_submission.id)
                                    || (p.submission && p.submission.assignment_submission_id) || null,
          question_submission_id: (p.evaluation && p.evaluation.question_submission_id)
                                  || (p.submission && p.submission.id) || null};
}"""


def read_props(frame, sid, url):
    """讀評分頁資料；確認是『目前這一份』的資料（否則重新載入一次）。"""
    for attempt in range(2):
        info = frame.evaluate(JS_PROPS)
        if info and str(info.get("question_submission_id")) == str(sid):
            return info
        frame.goto(url)
        frame.wait_for_selector(GS["point_adjustment"], timeout=30000)
        time.sleep(2)
    raise RuntimeError("頁面資料與目前 submission 對不上")


def fetch_pdf(ctx, frame, sid, url):
    """用頁面資料裡的 pdf_url 下載整份考卷。回傳 pdf_key（= assignment_submission_id）。
    同一位學生在 2.1 / 2.2 / 2.3 的 pdf_key 相同 → 用來配對；PDF 只下載一次。"""
    info = read_props(frame, sid, url)
    key = info.get("assignment_submission_id")
    if not key:
        print("  頁面資料沒有 assignment_submission_id")
        return None
    key = str(key)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / f"{key}.pdf"
    if not dest.exists():
        if not info.get("pdf_url"):
            print("  頁面資料沒有 pdf_url")
            return key
        r = ctx.request.get(info["pdf_url"])
        data = r.body() if r.ok else b""
        if data[:4] == b"%PDF":
            dest.write_bytes(data)
        else:
            print(f"  PDF 下載失敗（HTTP {r.status}）")
    return key


def save_submission(ctx, frame, qid, sid, url):
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    wait_images_loaded(frame)
    time.sleep(1)

    srcs = frame.evaluate("(s) => [...document.querySelectorAll(s)].map(i => i.src)", GS["page_img"])
    keys = []
    for src in dict.fromkeys(srcs):               # 去重、保留順序
        key = page_key(src)
        keys.append(key)
        dest = PAGES_DIR / f"{key}.jpg"
        if dest.exists():
            continue                              # 其他小題已下載過同一張
        r = ctx.request.get(src)
        if r.ok:
            dest.write_bytes(r.body())
        else:
            print(f"  圖片下載失敗 HTTP {r.status}")

    view_dir = config.SUBMISSIONS_DIR / f"q{qid}"
    view_dir.mkdir(parents=True, exist_ok=True)
    try:
        frame.locator(GS["viewer"]).screenshot(path=str(view_dir / f"{sid}_viewer.png"))
    except Exception as e:
        print(f"  viewer 截圖失敗：{type(e).__name__}")

    pdf_key = fetch_pdf(ctx, frame, sid, url)

    heading = frame.locator(GS["question_heading"]).first.inner_text().strip()
    rec = {"question_id": qid, "question_label": heading, "submission_id": sid,
           "grade_url": url, "page_keys": keys, "pdf_key": pdf_key}
    with MANIFEST.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(keys)


def go_next(frame, prev_sid, timeout_s=20):
    btn = frame.locator(GS["next_btn"])
    if btn.count() == 0 or "disabled" in (btn.get_attribute("class") or ""):
        return False
    btn.click()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if current_ids(frame)[1] != prev_sid:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("按 Next 後頁面沒有切換")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多抓幾份（0 = 全部）")
    args = ap.parse_args()

    config.SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    config.WORK.mkdir(parents=True, exist_ok=True)
    done = set()
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["question_id"], r["submission_id"]))

    with sync_playwright() as pw:
        ctx = config.launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CANVAS_ENTRY)
        print("請在跳出的 Chrome 打開某一小題（如 2.1）的『第一份』評分頁 …")
        frame = config.wait_for_grading_frame(ctx)
        if frame is None:
            print("逾時，沒偵測到評分頁。")
            sys.exit(1)
        time.sleep(3)

        count, seen = 0, set()
        while True:
            qid, sid, url = current_ids(frame)
            if sid in seen:
                print("回到已看過的 submission，結束。")
                break
            seen.add(sid)
            if (qid, sid) in done:
                print(f"[跳過] q{qid} / {sid}（已抓過）")
            else:
                n = save_submission(ctx, frame, qid, sid, url)
                count += 1
                print(f"[{count}] q{qid} / {sid}：{n} 張頁面")
                if args.limit and count >= args.limit:
                    print(f"已達 --limit {args.limit}")
                    break
            time.sleep(1.5)
            if not go_next(frame, sid):
                print("已是最後一份。")
                break

        print(f"\n完成，本次新抓 {count} 份。清單：{MANIFEST}")
        ctx.close()


if __name__ == "__main__":
    main()
