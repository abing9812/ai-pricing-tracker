"""google.py 解析器測試（不需網路、不需 pytest）：python collector/test_google_parser.py

2026-09-11 起官方 HTML 在 gemini-robotics-er-2-streaming 那段漏了 </table>，lxml 把
後面幾張表全包進去，streaming 拿到 gemma-4 的「Not available」、標待覆核。同一天
gemini-embedding-2 因為輸入列叫「Text input price」被整個跳過，沒有任何提示。
這裡用最小的頁面片段驗證：巢狀表不串價、嵌入模型抓得到、沒有付費層不進待覆核。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import google as g

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)

HEADER = "<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>"

def section(model_id, rows, *, tier="standard", close=True):
    """一個模型段落：h2 + 分級 h3 + 表格。close=False 模擬官方漏掉 </table>。"""
    body = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in rows)
    h3 = f'<h3 id="{tier}">Standard</h3>' if tier else ""
    end = "</tbody></table>" if close else "</tbody>"
    return (f'<h2 id="{model_id}">{model_id}</h2>{h3}'
            f'<table class="pricing-table"><tbody>{HEADER}{body}{end}')

def parse(html):
    models, _notes = g._parse(html)
    return {m["id"]: m for m in models}


# 1. 漏掉 </table>：後面沒有分級 h3 的段落被 lxml 包進前一張表。
#    舊寫法 streaming 會一路讀到最後一列 Input price（gemma 的 Not available）。
UNCLOSED = (
    section("robotics-streaming", [
        ("Input price", "Free of charge", "$1.00 (text / image / video / audio)"),
        ("Output price", "Free of charge", "$5.00"),
    ], tier="standard_25", close=False)
    + section("computer-use", [
        ("Input price", "Not available", "$1.25, prompts <= 200k tokens"),
        ("Output price", "Not available", "$10.00, prompts <= 200k tokens"),
    ], tier=None)
    + section("gemma", [
        ("Input price", "Free of charge", "Not available"),
        ("Output price", "Free of charge", "Not available"),
    ], tier=None)
)

by_id = parse(UNCLOSED)
streaming = by_id.get("robotics-streaming")

check("前提：lxml 確實把後面的表包進來了",
      len(g.base.soup_of(UNCLOSED).find("table").find_all("table")) >= 1)
check("漏 </table> 時 streaming 仍拿到自己的輸入價",
      streaming and streaming["input_price_per_mtok"] == 1.0, streaming)
check("漏 </table> 時 streaming 仍拿到自己的輸出價",
      streaming and streaming["output_price_per_mtok"] == 5.0, streaming)
check("streaming 價格欄位 ok、沒吃到別人的 Not available",
      streaming and streaming["field_status"]["input_price_per_mtok"] == "ok"
      and streaming["raw"]["input"].startswith("$1.00"), streaming)
check("被包進去的表仍各自解析", by_id.get("computer-use", {}).get("input_price_per_mtok") == 1.25,
      by_id.get("computer-use"))


# 2. 嵌入模型：輸入列叫 Text input price，而且沒有 Output price 那一列。
EMBEDDING = section("gemini-embedding-2", [
    ("Text input price", "Free of charge", "$0.20"),
    ("Image input price", "Free of charge", "$0.45 ($0.00012 per image)"),
    ("Audio input price", "Free of charge", "$6.50 ($0.00016 per second)"),
])
emb = parse(EMBEDDING).get("gemini-embedding-2")

check("Text input price 當成輸入價", emb and emb["input_price_per_mtok"] == 0.2, emb)
check("image／audio input price 不覆蓋文字價", emb and emb["raw"]["input"] == "$0.20", emb)
check("嵌入模型沒有輸出列 → unavailable",
      emb and emb["field_status"]["output_price_per_mtok"] == "unavailable", emb)


# 3. 付費欄明寫 Not available（只有免費層）→ unavailable，不進待覆核區。
gemma = by_id.get("gemma")
check("Not available → 價格為 None", gemma and gemma["input_price_per_mtok"] is None, gemma)
check("Not available → 輸入價 unavailable",
      gemma and gemma["field_status"]["input_price_per_mtok"] == "unavailable", gemma)
check("Not available → 輸出價 unavailable",
      gemma and gemma["field_status"]["output_price_per_mtok"] == "unavailable", gemma)

# 只認明確的字：空格或讀不懂的內容仍要進待覆核，不能被一起吞掉。
UNREADABLE = section("weird", [
    ("Input price", "Free of charge", ""),
    ("Output price", "Free of charge", "Contact sales"),
])
weird = parse(UNREADABLE).get("weird")
check("空的付費欄仍標 needs_review",
      weird and weird["field_status"]["input_price_per_mtok"] == "needs_review", weird)
check("讀不懂的付費欄仍標 needs_review",
      weird and weird["field_status"]["output_price_per_mtok"] == "needs_review", weird)


print("\n" + (f"{len(fails)} 個測試失敗" if fails else "全部通過"))
sys.exit(1 if fails else 0)
