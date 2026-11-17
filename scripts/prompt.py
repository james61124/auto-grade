"""由 rubric/<題號>.json + grading_policy.md 組出送給 Gemini 的評分 prompt。"""
import json

import config

POLICY_FILE = config.ROOT / "grading_policy.md"


def load_rubric(question=config.QUESTION):
    rubric = json.loads((config.RUBRIC_DIR / f"{question}.json").read_text(encoding="utf-8"))
    if "subparts" not in rubric or any("subpart" not in it for it in rubric["items"]):
        raise SystemExit(f"{question}.json 尚未標註小題（items[].subpart 與頂層 subparts），請先用 Gemini 補上。")
    return rubric


def output_schema(rubric):
    sub = {}
    for name in rubric["subparts"]:
        sub[name] = {
            "items": [{"id": it["id"], "awarded": "<0 到 該項 points 的數字>", "unreadable": False,
                       "reason": "<給助教看的理由>",
                       "note": "<給學生看的一句英文短評>"}
                      for it in rubric["items"] if it["subpart"] == name],
            "total": "<各項 awarded 加總>",
        }
    return {"transcription": f"<逐字抄寫學生在 {config.QUESTION} 各小題寫的重點（含算式/結論）>",
            "subparts": sub,
            "confidence": "<high | medium | low>",
            "flags": ["<需要助教注意的事，沒有就空陣列>"]}


def build_prompt(rubric):
    policy = POLICY_FILE.read_text(encoding="utf-8").strip()
    rubric_view = {k: rubric[k] for k in
                   ("question", "prompt_text", "reference_answer", "subparts", "items",
                    "accept_alternatives", "common_mistakes") if k in rubric}
    return f"""{policy}

=== 題目與 Rubric（JSON）===
{json.dumps(rubric_view, ensure_ascii=False, indent=1)}

=== 學生答案 ===
附件圖片是一位學生考卷中 {config.QUESTION} 所在的頁面（可能包含其他題目，只評 {config.QUESTION} 的 {"、".join(rubric["subparts"])} 小題）。

=== 輸出格式 ===
只輸出一個 ```json 程式碼區塊，不要任何其他文字。格式：
{json.dumps(output_schema(rubric), ensure_ascii=False, indent=1)}
"""


if __name__ == "__main__":
    # 只印長度與結構檢查，不印內容
    r = load_rubric()
    p = build_prompt(r)
    print(f"prompt 長度：{len(p)} 字元；小題：{list(r['subparts'])}；items：{len(r['items'])}")
