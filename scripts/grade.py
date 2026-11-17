"""統一入口：指定題號，一步一步跑完整個流程。

  .venv/bin/python scripts/grade.py --q Q1 status        # 看這一題做到哪、下一步該做什麼
  .venv/bin/python scripts/grade.py --q Q1 <步驟> [參數]

步驟（依序）：
  login     第一次使用：開專用 Chrome 登入 Canvas/Gradescope，記下 course ID（整門課只需一次）
  fetch     抓答案：先在 Chrome 打開某一小題的『第一份』評分頁，每個小題各跑一次（可加 --limit 3 試跑）
  build     把各小題的 submission 配對成學生（S001…）
  names     抓學生姓名（只存本機，審核介面對照用）
  extract   從考卷 PDF 切出這一題的頁面（頁碼設定在 scripts/config.py 的 QUESTION_PAGES）
  check     檢查 rubric/<題號>.json 格式與 prompt 長度（不印內容）
  gemini    Gemini 逐位評分（可加 --limit 1 / --student S003 / --regrade）
  review    開審核介面 http://localhost:8765
  fill      回填 Gradescope（預設試跑；加 --apply 才寫入；--student S001 只填一位；--overwrite 覆寫）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable

STEPS = {
    "login": ("probe_gradescope.py", []),
    "fetch": ("fetch_answers.py", []),
    "build": ("build_students.py", []),
    "names": ("fetch_names.py", []),
    "extract": ("extract_pages.py", []),
    "check": ("prompt.py", []),
    "gemini": ("gemini_runner.py", []),
    "review": ("review_server.py", []),
    "fill": ("gradescope_filler.py", None),   # 沒指定學生時預設 --all
}


def count_lines(p):
    return sum(1 for l in p.open(encoding="utf-8") if l.strip()) if p.exists() else 0


def status(q):
    os.environ["GRADE_Q"] = q
    sys.path.insert(0, str(HERE))
    import config
    w = config.WORK
    ok = lambda b: "✅" if b else "⬜"
    students = json.loads(config.STUDENTS_FILE.read_text(encoding="utf-8")) if config.STUDENTS_FILE.exists() else []
    n = len(students)
    labels = sorted({v["label"].split(":")[0].strip() for s in students for v in s["subparts"].values()})
    rubric = config.RUBRIC_DIR / f"{q}.json"
    raw = len([s for s in students if (config.RAW_DIR / f"{s['student']}.txt").exists()])
    approved = json.loads(config.APPROVED_FILE.read_text(encoding="utf-8")) if config.APPROVED_FILE.exists() else {}
    pages = len([s for s in students if any((config.PAGES_DIR / str(s["pdf_key"])).glob("p*.png"))])
    filled = set()
    if config.FILL_LOG.exists():
        for l in config.FILL_LOG.open(encoding="utf-8"):
            d = json.loads(l)
            if d["status"] == "ok":
                filled.add((d["student"], d["subpart"]))
    rows = [
        ("login", config.IDS_FILE.exists(), "已記下 course ID" if config.IDS_FILE.exists() else "尚未登入／記錄"),
        ("fetch", config.MANIFEST.exists(), f"已抓 {count_lines(config.MANIFEST)} 份 submission"),
        ("build", n > 0, f"{n} 位學生；小題：{', '.join(labels) or '—'}"),
        ("names", config.NAMES_FILE.exists(), "已有姓名" if config.NAMES_FILE.exists() else "尚未抓姓名"),
        ("extract", n and pages == n, f"{pages}/{n} 位有頁面圖（PDF 第 {config.PDF_PAGES[0]}–{config.PDF_PAGES[1]} 頁）"),
        ("check", rubric.exists(), f"rubric/{q}.json {'存在' if rubric.exists() else '不存在（請先建立）'}"),
        ("gemini", n and raw == n, f"Gemini 已評 {raw}/{n}"),
        ("review", n and len(approved) == n, f"已核准 {len(approved)}/{n}"),
        ("fill", n and len(filled) == n * len(labels), f"已回填 {len(filled)}/{n * len(labels)} 個評分頁"),
    ]
    print(f"\n題號 {q}　資料夾 {w.relative_to(ROOT)}\n")
    nxt = None
    for name, done, msg in rows:
        print(f"  {ok(done)} {name:8} {msg}")
        if not done and nxt is None:
            nxt = name
    print(f"\n下一步：python scripts/grade.py --q {q} {nxt}" if nxt else "\n全部完成 🎉")
    if nxt == "fetch" or (nxt == "build" and labels):
        print("  （fetch 要每個小題各跑一次；全部小題抓完再 build）")


def main():
    args = sys.argv[1:]
    if "--q" not in args or args.index("--q") + 1 >= len(args):
        sys.exit(__doc__)
    i = args.index("--q")
    q = args[i + 1].upper()
    rest = args[:i] + args[i + 2:]
    if not rest or rest[0] in ("status", "-h", "--help"):
        if rest and rest[0] != "status":
            print(__doc__)
        return status(q)
    step, extra = rest[0], rest[1:]
    if step not in STEPS:
        sys.exit(f"不認得的步驟：{step}\n{__doc__}")
    script, _ = STEPS[step]
    if step == "fill" and "--student" not in extra:
        extra = ["--all"] + extra
    env = dict(os.environ, GRADE_Q=q)
    print(f"[{q}] 執行 {script} {' '.join(extra)}")
    sys.exit(subprocess.call([PY, str(HERE / script), *extra], env=env, cwd=str(ROOT)))


if __name__ == "__main__":
    main()
