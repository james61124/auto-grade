"""用 Gemini 網頁版（學校帳號）逐位學生評分。每位學生開一個新對話。

用法：
  .venv/bin/python scripts/gemini_runner.py --probe            # 探測 Gemini 頁面結構（selector 不對時用）
  .venv/bin/python scripts/gemini_runner.py --limit 1          # 先跑 1 位
  .venv/bin/python scripts/gemini_runner.py --student S003     # 只跑指定學生
  .venv/bin/python scripts/gemini_runner.py                    # 跑全部（跳過已完成）

第一次：在跳出的 Chrome 登入學校 Google 帳號，打開 gemini.google.com 確認能用。
輸出（本機）：raw/<student>.txt（Gemini 原始回覆）、raw/<student>.png（畫面截圖）
"""
import argparse
import json
import random
import re
import sys
import time

from playwright.sync_api import sync_playwright

import config
import prompt as promptlib

GEMINI_URL = "https://gemini.google.com/app"

# 依序嘗試的 selector（Gemini 改版時在這裡調整；用 --probe 查）
SEL = {
    "input": ['rich-textarea div[contenteditable="true"]', 'div.ql-editor[contenteditable="true"]',
              'div[contenteditable="true"][role="textbox"]'],
    "send": ['button.send-button', 'button[aria-label*="Send" i]', 'button[aria-label*="傳送"]',
             'button[aria-label*="提交"]'],
    "upload_menu": ['button.upload-card-button', 'button[aria-label*="upload" i]',
                    'button[aria-label*="上傳"]', 'button[aria-label*="Add files" i]',
                    'button[aria-label*="新增檔案"]'],
    "upload_item": ['[data-test-id="local-images-files-uploader-button"]',
                    'button:has-text("Upload files")', 'button:has-text("上傳檔案")'],
    "response": ["model-response", "message-content"],
    "busy": ['button[aria-label*="Stop" i]', 'button[aria-label*="停止"]', '.stop-icon'],
    "upload_busy": ['.uploading', 'mat-progress-spinner', '[role="progressbar"]'],
}


def first(page, key, must=True, timeout=10000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for s in SEL[key]:
            loc = page.locator(s)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        time.sleep(0.3)
    if must:
        raise RuntimeError(f"找不到 Gemini 元素：{key}（請跑 --probe 並把結果給 Claude）")
    return None


def any_visible(page, key):
    return any(page.locator(s).count() and page.locator(s).first.is_visible() for s in SEL[key])


def upload_images(page, files):
    # 1) 直接找隱藏的 file input
    inp = page.locator('input[type="file"]')
    if inp.count() > 0:
        inp.first.set_input_files([str(f) for f in files])
        return
    # 2) 打開上傳選單 → 檔案選擇器
    first(page, "upload_menu").click()
    time.sleep(0.8)
    inp = page.locator('input[type="file"]')
    if inp.count() > 0:
        inp.first.set_input_files([str(f) for f in files])
        return
    with page.expect_file_chooser(timeout=10000) as fc:
        first(page, "upload_item").click()
    fc.value.set_files([str(f) for f in files])


def wait_uploads_done(page, n_files, timeout_s=90):
    time.sleep(2)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not any_visible(page, "upload_busy"):
            send = first(page, "send", must=False, timeout=1000)
            if send and send.is_enabled():
                time.sleep(1.5)
                return
        time.sleep(1)
    raise RuntimeError("圖片上傳逾時")


def wait_response(page, before_count, timeout_s=300):
    """等新的回覆出現、停止產生、文字穩定 6 秒。回傳最後一則回覆的文字。"""
    resp_sel = SEL["response"][0]
    deadline = time.time() + timeout_s
    while page.locator(resp_sel).count() <= before_count:
        if time.time() > deadline:
            raise RuntimeError("Gemini 沒有回覆")
        time.sleep(1)
    last_text, stable_since = None, time.time()
    while time.time() < deadline:
        text = page.locator(resp_sel).last.inner_text()
        if text != last_text:
            last_text, stable_since = text, time.time()
        elif not any_visible(page, "busy") and time.time() - stable_since > 6 and text.strip():
            return text
        time.sleep(1)
    raise RuntimeError("等待回覆逾時")


def extract_json_text(page):
    """優先取回覆中的程式碼區塊文字（比 innerText 更乾淨）。"""
    last = page.locator(SEL["response"][0]).last
    code = last.locator("code")
    if code.count() > 0:
        return code.last.inner_text()
    return last.inner_text()


def grade_one(page, student, images, prompt_text):
    page.goto(GEMINI_URL)
    box = first(page, "input", timeout=30000)
    before = page.locator(SEL["response"][0]).count()
    print("  上傳圖片 …", flush=True)
    upload_images(page, images)
    wait_uploads_done(page, len(images))
    print("  輸入 prompt …", flush=True)
    box.click()
    box.fill(prompt_text)
    time.sleep(0.8)
    first(page, "send").click()
    print("  已送出，等待 Gemini 回覆 …", flush=True)
    full = wait_response(page, before)
    code = extract_json_text(page)
    raw = config.RAW_DIR / f"{student}.txt"
    raw.write_text(code if code.strip().startswith("{") else full, encoding="utf-8")
    page.screenshot(path=str(config.RAW_DIR / f"{student}.png"), full_page=True)
    (config.RAW_DIR / f"{student}.meta.json").write_text(
        json.dumps({"conversation_url": page.url, "graded_at": time.strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
    return raw


def probe(page):
    sys.path.insert(0, str(config.ROOT / "scripts"))
    from probe_gradescope import JS_DUMP, scrub
    items = page.evaluate(JS_DUMP)
    out = config.ROOT / "probe_gemini.txt"
    lines = []
    for it in items:
        if it["tag"] in ("a",) and "gemini.google.com/app/" in it["attrs"].get("href", ""):
            continue  # 側邊欄的歷史對話連結，略過
        a = {k: scrub(v) for k, v in it["attrs"].items() if k != "title"}
        lines.append(f"{it['tag']:9} box={it['box']} textLen={it['textLen']} {json.dumps(a, ensure_ascii=False)}")
    tags = page.evaluate("() => [...new Set([...document.querySelectorAll('*')].map(e=>e.tagName.toLowerCase()).filter(t=>t.includes('-')))]")
    lines.insert(0, "custom tags: " + ", ".join(sorted(tags)))
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已輸出 {out}（不含對話文字），請先看過再交給 Claude。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--student")
    ap.add_argument("--regrade", action="store_true", help="重評所有有『重評說明』的學生")
    ap.add_argument("--min-wait", type=int, default=20)
    ap.add_argument("--max-wait", type=int, default=45)
    args = ap.parse_args()

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = config.launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(GEMINI_URL)

        if args.probe:
            print("等待頁面出現任何可輸入區塊 …")
            page.wait_for_selector('[contenteditable="true"], textarea', timeout=600000)
            time.sleep(4)
            probe(page)
            ctx.close()
            return

        print("等待 Gemini 輸入框出現（若要求登入，請在 Chrome 登入學校 Google 帳號）…")
        first(page, "input", timeout=600000)
        time.sleep(2)
        students = json.loads(config.STUDENTS_FILE.read_text(encoding="utf-8"))
        rubric = promptlib.load_rubric()
        prompt_text = promptlib.build_prompt(rubric)
        pages_root = config.PAGES_DIR

        notes_dir = config.NOTES_DIR
        todo = []
        for s in students:
            note_file = notes_dir / f"{s['student']}.txt"
            if args.regrade:
                if not note_file.exists():
                    continue
            elif args.student and s["student"] != args.student:
                continue
            elif (config.RAW_DIR / f"{s['student']}.txt").exists() and not args.student:
                continue
            imgs = sorted((pages_root / str(s["pdf_key"])).glob("p*.png"),
                          key=lambda p: int(re.sub(r"\D", "", p.stem)))
            if not imgs:
                print(f"[略過] {s['student']}：沒有 {config.QUESTION} 頁面圖片")
                continue
            todo.append((s["student"], imgs, note_file if note_file.exists() else None))
        if args.limit:
            todo = todo[:args.limit]
        print(f"待評分：{len(todo)} 位")

        for i, (student, imgs, note_file) in enumerate(todo, 1):
            text = prompt_text
            if note_file:
                text += ("\n=== 助教補充說明（請依此重新評分）===\n"
                         + note_file.read_text(encoding="utf-8").strip() + "\n")
            try:
                raw = grade_one(page, student, imgs, text)
                if note_file and args.regrade:
                    done_dir = notes_dir / "done"
                    done_dir.mkdir(exist_ok=True)
                    note_file.rename(done_dir / f"{student}_{time.strftime('%m%d%H%M')}.txt")
                print(f"[{i}/{len(todo)}] {student} 完成 → {raw.name}")
            except Exception as e:
                print(f"[{i}/{len(todo)}] {student} 失敗：{type(e).__name__}: {str(e)[:120]}")
                page.screenshot(path=str(config.RAW_DIR / f"{student}_error.png"))
            if i < len(todo):
                time.sleep(random.uniform(args.min_wait, args.max_wait))
        print("全部完成。")
        ctx.close()


if __name__ == "__main__":
    main()
