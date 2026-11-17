"""本機審核介面：逐位學生檢查 / 修改 Gemini 的評分，核准後寫入 approved.json。

用法：.venv/bin/python scripts/review_server.py   → 打開 http://localhost:8765
只在本機（127.0.0.1）執行；資料不會離開這台電腦。
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config

PORT = 8765
APPROVED = config.APPROVED_FILE
PAGES_ROOT = config.PAGES_DIR
HTML = config.ROOT / "scripts" / "review.html"
NOTES = config.NOTES_DIR


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def parse_raw(student):
    p = config.RAW_DIR / f"{student}.txt"
    if not p.exists():
        return None, "尚未評分"
    text = p.read_text(encoding="utf-8")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None, "Gemini 回覆中找不到 JSON"
    try:
        return json.loads(m.group(0)), None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失敗：{e.msg}"


def check(result, rubric):
    """自動檢查：回傳問題清單。"""
    issues = []
    pts = {it["id"]: it for it in rubric["items"]}
    for name, sp in rubric["subparts"].items():
        r = (result.get("subparts") or {}).get(name)
        if not r:
            issues.append(f"缺少小題 {name}")
            continue
        s = 0
        for it in r.get("items", []):
            ref = pts.get(it.get("id"))
            if not ref or ref["subpart"] != name:
                issues.append(f"{name}: 不存在的項目 {it.get('id')}")
                continue
            a = it.get("awarded")
            if not isinstance(a, (int, float)) or a < 0 or a > ref["points"]:
                issues.append(f"{name}/{it['id']}: 分數 {a} 超出 0–{ref['points']}")
            else:
                s += a
            if it.get("unreadable"):
                issues.append(f"{name}/{it['id']}: Gemini 標記看不清楚")
        if r.get("total") != s:
            issues.append(f"{name}: total {r.get('total')} ≠ 各項加總 {s}")
    if result.get("confidence") != "high":
        issues.append(f"信心：{result.get('confidence')}")
    for f in result.get("flags") or []:
        if f and not f.startswith("<"):
            issues.append(f"Gemini 註記：{f}")
    return issues


def build_state():
    rubric = json.loads((config.RUBRIC_DIR / f"{config.QUESTION}.json").read_text(encoding="utf-8"))
    students = load(config.STUDENTS_FILE, [])
    names = load(config.NAMES_FILE, {})
    approved = load(APPROVED, {})
    rows = []
    for s in students:
        sid = s["student"]
        result, err = parse_raw(sid)
        imgs = sorted((PAGES_ROOT / str(s["pdf_key"])).glob("p*.png"),
                      key=lambda p: int(re.sub(r"\D", "", p.stem)))
        rows.append({
            "student": sid, "name": names.get(sid, ""),
            "images": [f"/img/{s['pdf_key']}/{p.name}" for p in imgs],
            "result": result, "error": err,
            "issues": check(result, rubric) if result else [],
            "approved": approved.get(sid),
            "gemini_url": load(config.RAW_DIR / f"{sid}.meta.json", {}).get("conversation_url"),
            "regrade_note": (NOTES / f"{sid}.txt").read_text(encoding="utf-8")
                            if (NOTES / f"{sid}.txt").exists() else "",
        })
    return {"question": config.QUESTION, "rubric": rubric, "students": rows}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, json.dumps(build_state(), ensure_ascii=False))
        m = re.fullmatch(r"/img/(\w+)/(p\d+\.png)", path)
        if m:
            f = PAGES_ROOT / m.group(1) / m.group(2)
            if f.is_file():
                return self._send(200, f.read_bytes(), "image/png")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path == "/api/regrade_note":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            sid, text = body["student"], (body.get("note") or "").strip()
            if not re.fullmatch(r"S\d{3}", sid):
                return self._send(400, json.dumps({"error": "bad student"}))
            NOTES.mkdir(exist_ok=True)
            f = NOTES / f"{sid}.txt"
            if text:
                f.write_text(text, encoding="utf-8")
            elif f.exists():
                f.unlink()
            return self._send(200, json.dumps({"ok": True}))
        if urlparse(self.path).path != "/api/approve":
            return self._send(404, "not found", "text/plain")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        rubric = json.loads((config.RUBRIC_DIR / f"{config.QUESTION}.json").read_text(encoding="utf-8"))
        approved = load(APPROVED, {})
        sid = body["student"]
        if body.get("unapprove"):
            approved.pop(sid, None)
        else:
            # 伺服器端再檢查一次分數範圍
            pts = {it["id"]: it["points"] for it in rubric["items"]}
            for name, sp in body["subparts"].items():
                tot = 0
                for it in sp["items"]:
                    a = float(it["awarded"])
                    if a < 0 or a > pts[it["id"]]:
                        return self._send(400, json.dumps({"error": f"{it['id']} 分數超出範圍"}))
                    tot += a
                sp["total"] = round(tot, 2)
                sp["deduction"] = round(tot - rubric["subparts"][name]["max_points"], 2)
            approved[sid] = {"subparts": body["subparts"]}
        APPROVED.write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
        self._send(200, json.dumps({"ok": True}))


if __name__ == "__main__":
    print(f"[{config.QUESTION}] 審核介面：http://localhost:{PORT}   （Ctrl+C 結束）")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
