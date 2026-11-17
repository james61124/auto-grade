# Gradescope 半自動批改（Gemini 評分 + 助教審核）

這套流程用來批改 COMP 582 期中考的某一題：

1. 從 Gradescope 抓學生的答案。
2. 用學校的 Gemini 網頁版依 rubric 評分。
3. 助教在本機的審核介面逐位檢查、修改並核准。
4. 程式把核准的分數和 comment 自動填回 Gradescope。

Q2 已經用這套流程改完了，資料在 `work/Q2/`。接下來要改 Q1、Q3，照下面的步驟做。

> **政策**：學校規定 AI 評分只能用 Gemini。所以題目、rubric、學生答案只會送到 Gemini 網頁版（學校帳號），不要貼給其他 AI。
> **隱私**：`work/`、`submissions/`、`profiles/`（裡面有登入 cookie）都只放在本機，不要上傳或分享。

---

## 一、第一次使用：安裝

```bash
cd grading
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chrome      # 電腦已經裝好 Google Chrome 的話可以略過
```

所有指令都從 `grading/` 資料夾執行。統一入口是：

```bash
.venv/bin/python scripts/grade.py --q Q1 status
```

`status` 會列出這一題每一步的進度，並告訴你**下一步該跑什麼**。不確定做到哪裡時，跑它就對了。

---

## 二、流程總覽

| # | 指令（`scripts/grade.py --q Q1 …`） | 做什麼 | 你要做的事 |
|---|---|---|---|
| 0 | `login` | 開專用 Chrome，記下 course ID | 在跳出的 Chrome 登入 Rice SSO + Duo，打開任一份評分頁。整門課只要做一次，已經做過了 |
| 1 | `fetch` | 抓答案和考卷 PDF（唯讀，不改分數） | 先打開 **1.1 的第一份**評分頁，再跑這個指令。**每個小題各跑一次**（1.1、1.2…） |
| 2 | `build` | 把同一位學生在各小題的 submission 配對成 S001、S002… | 看輸出有沒有「問題」 |
| 3 | `names` | 抓學生姓名，只給審核介面對照用 | 等它跑完 |
| 4 | `extract` | 從 PDF 切出這一題的頁面，存成圖片 | 抽查幾位，確認答案真的在這幾頁 |
| 5 | 建立 `rubric/Q1.json` | 給 Gemini 看的題目和 rubric | 見下方〈三、Rubric 檔〉 |
| 6 | `check` | 檢查 rubric 格式（不印內容） | 有錯就修 rubric |
| 7 | `gemini` | Gemini 逐位評分，每位學生開一個新對話 | 先跑 `--limit 1` 看品質，再跑全部 |
| 8 | `review` | 開審核介面 http://localhost:8765 | 逐位檢查、修改、核准 |
| 9 | `fill` | 回填 Gradescope | 先試跑，沒問題再加 `--apply` |

**頁碼**：各題在考卷 PDF 中的頁碼設定在 `scripts/config.py`：

```python
QUESTION_PAGES = {"Q1": (5, 10), "Q2": (11, 14), "Q3": (17, 22)}
```

如果某位學生的頁碼不一樣（例如他的 PDF 少了幾頁），在同一個檔案的 `PAGE_OVERRIDES` 裡個別覆寫，然後重跑 `extract`。

---

## 三、Rubric 檔：`rubric/Q1.json`

Rubric 檔建議用 Gemini 產生：把題目、解答和你的 rubric 貼給 Gemini，請它輸出下面這個格式，存成 `rubric/Q1.json`。

```json
{
  "question": "Q1",
  "max_points": 30,
  "prompt_text": "題目原文",
  "reference_answer": "標準答案",
  "accept_alternatives": ["其他可接受的解法…"],
  "common_mistakes": ["常見錯誤…"],
  "subparts": {
    "a": {"gradescope_label": "1.1", "max_points": 10},
    "b": {"gradescope_label": "1.2", "max_points": 20}
  },
  "items": [
    {"id": "a1", "subpart": "a", "desc": "這一項看什麼", "points": 4, "partial": "怎樣給部分分數", "gradescope_index": null},
    {"id": "a2", "subpart": "a", "desc": "…", "points": 6, "partial": "…", "gradescope_index": null}
  ]
}
```

填這個檔時要注意：
- `gradescope_label` 必須和 Gradescope 上小題標題的開頭一致，例如 `1.1`。回填時靠它找到正確的評分頁。
- 每個小題底下各 item 的 `points` 加起來要等於該小題的 `max_points`。
- 評分原則（寬鬆、給部分分數、不重複扣分，以及 comment 的語氣）寫在 `grading_policy.md`，各題共用。要改語氣或寬嚴程度就改這個檔。

---

## 四、Gemini 評分

```bash
.venv/bin/python scripts/grade.py --q Q1 gemini --limit 1     # 先評 1 位，到審核介面看品質
.venv/bin/python scripts/grade.py --q Q1 gemini               # 評其餘全部（已評過的會跳過）
.venv/bin/python scripts/grade.py --q Q1 gemini --student S005 # 重評指定學生（會覆蓋）
```

- 跑的時候 Chrome 會自動操作 Gemini，請不要去動那個視窗。第一次跑要先登入**學校的 Google 帳號**。
- 每位學生之間會隨機等 20 到 45 秒，避免被限流。34 位大約要 40 到 60 分鐘。
- Gemini 每次評分會有幾分的差異，所以一定要經過人工審核。

---

## 五、審核介面

```bash
.venv/bin/python scripts/grade.py --q Q1 review      # 打開 http://localhost:8765
```

介面分三欄：左邊是學生列表（顯示姓名和總分），中間是答案圖片（點圖片可放大），右邊是評分。

右邊每個 rubric 項目都有這些欄位：
- **分數**：可以直接改。
- **note**：給學生看的一句話。
- **reason**：給助教看的理由。

**Comment** 是實際會寫進 Gradescope、學生看得到的內容，格式是每項一行「(得分/配分) note」。
- 改分數或 note 時，comment 會自動更新。
- 你也可以直接改 comment 的文字。直接改過之後，它就不會再自動更新。
- 按「依分數重新產生」會把 comment 恢復成自動產生的版本，你手動改的內容會被蓋掉。

**核准（快捷鍵 A）** 會把目前的分數和 comment 存進 `work/Q1/approved.json`，回填時用的就是這份。**沒按核准就切到別的學生，修改會不見。**

橘色標記代表自動檢查發現了問題，例如分數超出範圍、加總不符、Gemini 信心不高，或 Gemini 留下的註記。這些學生請特別仔細看。

**想請 Gemini 重評**：在「重評說明」欄寫下理由並儲存，例如「2(b) 的答案寫在第 8 頁，請一併評」。之後執行：

```bash
.venv/bin/python scripts/grade.py --q Q1 gemini --regrade
```

---

## 六、回填 Gradescope

```bash
.venv/bin/python scripts/grade.py --q Q1 fill                    # 試跑：只顯示要填什麼，不寫入
.venv/bin/python scripts/grade.py --q Q1 fill --student S001 --apply   # 先實填一位，到 Gradescope 確認
.venv/bin/python scripts/grade.py --q Q1 fill --apply            # 填入全部已核准的學生
```

回填採扣分制：Point Adjustment 填「得分 − 滿分」，Comment 填審核介面裡的 comment。拿滿分的話會套用 rubric 第 1 項（Correct）。

有幾道安全機制：
- **只會填已核准的學生。**
- **身分核對**：每打開一頁，先確認網址的 submission ID 和考卷 ID 都跟這位學生的紀錄一致，對不上就立刻停止，不寫入。
- 寫完會重新載入頁面讀回來，確認分數和 comment 都存好了。
- 已經有分數或 comment 的頁面預設會跳過。要覆寫（例如核准後又改了分數）就加 `--overwrite`。
- 中途斷掉的話直接重跑同一個指令。已經填好的頁面會自動跳過。

---

## 七、常見問題

| 狀況 | 處理 |
|---|---|
| `profile is already in use` | 有另一支程式還開著專用 Chrome。把它關掉（Ctrl+C）再重跑 |
| 一直等不到 Gradescope 或評分頁 | 確認你操作的是**程式跳出來的那個 Chrome**，不是你平常用的 Chrome；需要的話重新登入 Duo |
| 找不到 Gemini 元素 | Gemini 改版了。跑 `.venv/bin/python scripts/gemini_runner.py --probe`，再依結果調整 `gemini_runner.py` 裡的 `SEL` |
| 某位學生的答案不在設定的頁碼範圍內 | 在 `config.PAGE_OVERRIDES` 覆寫該學生的頁碼，重跑 `extract`，再用 `gemini --student Sxxx` 重評 |
| 分數普遍太低或太高 | 修改 `grading_policy.md` 的原則，先用 `--student` 重評一兩位比較結果，再決定要不要全部重評 |

## 檔案位置

```
grading/
  README.md              這份說明
  grading_policy.md      評分原則和 comment 語氣（各題共用）
  rubric/Q1.json …       各題的 rubric
  gradescope_ids.json    course ID（login 時產生）
  scripts/grade.py       統一入口
  scripts/config.py      頁碼、Canvas 網址、Gradescope selector
  submissions/           各題共用：考卷 PDF、原始頁面圖
  work/Q1/               Q1 的所有資料：答案頁、Gemini 回覆、核准紀錄、回填 log
  profiles/              專用 Chrome 的登入狀態（不要分享）
```
