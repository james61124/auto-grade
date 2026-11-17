"""把各小題的 submission 依『共用的頁面圖片』配對成每位學生一筆。

用法：.venv/bin/python scripts/build_students.py
輸入：submissions/manifest.jsonl（fetch_answers.py 產生）
輸出：submissions/students.json
  [{"student": "S001", "pdf_key": "...", "pages": [...page_key], "subparts": {"<question_id>": {"label", "submission_id", "grade_url"}}}]
只印統計數字，不印學生資料。
"""
import json
import sys

import config

MANIFEST = config.MANIFEST
OUT = config.STUDENTS_FILE


def build(records):
    # union-find：共用任何一張頁面圖片 → 同一位學生
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in records:
        keys = ([f"pdf-{r['pdf_key']}"] if r.get("pdf_key") else []) + list(r.get("page_keys") or [])
        keys = keys or [f"nopage-{r['question_id']}-{r['submission_id']}"]
        r["_keys"] = keys
        for k in keys[1:]:
            parent[find(k)] = find(keys[0])

    groups = {}
    for r in records:
        groups.setdefault(find(r["_keys"][0]), []).append(r)

    question_ids = sorted({r["question_id"] for r in records})
    students, problems = [], []
    for i, (_, recs) in enumerate(sorted(groups.items(), key=lambda g: min(min(r["_keys"]) for r in g[1])), 1):
        sid = f"S{i:03d}"
        pages = list(dict.fromkeys(k for r in recs for k in r["_keys"] if not k.startswith(("nopage-", "pdf-"))))
        pdf_keys = sorted({r["pdf_key"] for r in recs if r.get("pdf_key")})
        if len(pdf_keys) > 1:
            problems.append(f"{sid}: 對到多份 PDF")
        if not pdf_keys:
            problems.append(f"{sid}: 沒有 PDF")
        subparts = {}
        for r in recs:
            if r["question_id"] in subparts:
                problems.append(f"{sid}: 小題 q{r['question_id']} 對到多份 submission")
            subparts[r["question_id"]] = {"label": r["question_label"],
                                          "submission_id": r["submission_id"],
                                          "grade_url": r["grade_url"]}
        missing = [q for q in question_ids if q not in subparts]
        if missing:
            problems.append(f"{sid}: 缺小題 {', '.join('q' + q for q in missing)}")
        students.append({"student": sid, "pdf_key": pdf_keys[0] if pdf_keys else None,
                         "pages": pages, "subparts": subparts})
    return students, question_ids, problems


def main():
    if not MANIFEST.exists():
        sys.exit(f"找不到 {MANIFEST}，請先跑 fetch_answers.py")
    # 同一 (question, submission) 若重複出現，保留最後一筆
    latest = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            latest[(r["question_id"], r["submission_id"])] = r
    students, qids, problems = build(list(latest.values()))
    OUT.write_text(json.dumps(students, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"小題數：{len(qids)}（question_id：{', '.join(qids)}）")
    for q in qids:
        print(f"  q{q}: {sum(q in s['subparts'] for s in students)} 份")
    print(f"配對出學生數：{len(students)}")
    print("問題：" + ("無" if not problems else ""))
    for p in problems:
        print("  - " + p)
    print(f"輸出：{OUT}")


if __name__ == "__main__":
    main()
