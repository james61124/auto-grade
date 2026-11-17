"""共用設定。selector 之後依 probe 結果調整。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUBRIC_DIR = ROOT / "rubric"
# 要改哪一題：用環境變數 GRADE_Q 指定（Q1 / Q2 / Q3），建議透過 scripts/grade.py --q Q1 執行
QUESTION = os.environ.get("GRADE_Q", "Q2").upper()

# 每一題在考卷 PDF 中的頁碼（1 起算，含頭尾）。新的一題在這裡加一行。
QUESTION_PAGES = {"Q1": (5, 10), "Q2": (11, 14), "Q3": (17, 22)}
# 個別學生頁碼不同時覆寫，例如 {"Q1": {"S008": (4, 9)}}（學生編號是該題自己的編號）
PAGE_OVERRIDES = {}

SUBMISSIONS_DIR = ROOT / "submissions"           # 各題共用：考卷 PDF、原始頁面圖
PDF_DIR = SUBMISSIONS_DIR / "pdfs"
WORK = ROOT / "work" / QUESTION                  # 這一題自己的所有資料
MANIFEST = WORK / "manifest.jsonl"
STUDENTS_FILE = WORK / "students.json"
NAMES_FILE = WORK / "names.json"
PAGES_DIR = WORK / "pages"                       # 這一題的 PNG（給 Gemini 與審核介面）
APPROVED_FILE = WORK / "approved.json"
NOTES_DIR = WORK / "regrade_notes"
FILL_LOG = WORK / "fill_log.jsonl"
RAW_DIR = WORK / "raw"
RESULTS_DIR = ROOT / "results"
PROFILE_DIR = ROOT / "profiles" / "chrome"      # Canvas / Gradescope / Gemini 共用的專用 Chrome profile
IDS_FILE = ROOT / "gradescope_ids.json"          # probe 自動寫入 course_id / question_id
SELECTORS_FILE = ROOT / "selectors.json"         # probe 後由我們填

CANVAS_ENTRY = "https://canvas.rice.edu/courses/90218/external_tools/10595"


def load_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def launch(pw, headless=False):
    """開專用 Chrome（保留登入狀態）。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="chrome", headless=headless,
        viewport={"width": 1400, "height": 900},
    )


# ---- Gradescope 評分頁（由 probe 結果確認）----
GS = {
    "viewer": ".submissionGraderViewer",
    "page_img": ".submissionGraderViewer img",
    "next_btn": "button.js-nextSubmissionButton",
    "question_heading": "h1.submissionGrader--questionSwitcherHeading",
    "rubric_key": "button.rubricItem--key",          # aria-label="Toggle rubric item N"
    "point_adjustment": "#pointAdjustment",
    "comment": "#adjustment-comment",
    "total_numerator": ".totalPoints--numerator",
}


def wait_for_grading_frame(ctx, timeout_s=900):
    """等到任一分頁 / iframe 出現 Gradescope 評分頁（有 Point Adjustment），回傳該 frame。"""
    import time
    deadline, last = time.time() + timeout_s, 0
    while time.time() < deadline:
        for p in ctx.pages:
            for f in p.frames:
                if "gradescope" not in f.url:
                    continue
                try:
                    if f.evaluate("() => !!document.querySelector('#pointAdjustment')"):
                        return f
                except Exception:
                    pass
        if time.time() - last > 20:
            last = time.time()
            print("  …等待 Gradescope 評分頁（右側要有 Point Adjustment）")
        time.sleep(2)
    return None

if QUESTION not in QUESTION_PAGES:
    raise SystemExit(f"config.QUESTION_PAGES 裡沒有 {QUESTION} 的頁碼，請先加上。")
PDF_PAGES = QUESTION_PAGES[QUESTION]
