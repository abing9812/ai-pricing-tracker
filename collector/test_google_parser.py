"""google.py 解析器測試（不需網路、不需 pytest）：python collector/test_google_parser.py

2026-11-02 定價頁搬到 aistudio.google.com 並改成 Markdoc，舊的 HTML 解析器抓到 0 個
模型卻回報 ok，30 個模型被當成下架。這裡用最小的頁面片段驗證：標準分級不會被批次價
蓋掉、沒有分頁的段落抓得到、儲存格能跨行、嵌入模型與沒有付費層的模型維持原本的判定。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import google as g

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)

HEADER = ("Feature", "Free Tier", "Paid Tier, per 1M tokens in USD")

def table(rows, *, header=HEADER, indent="  "):
    """一張 Markdoc 表格：列之間用 --- 分隔，每格是一個「* 」項目。"""
    lines = [f"{indent}{{% table %}}"]
    for number, row in enumerate([header, *rows]):
        if number:
            lines += ["", f"{indent}---", ""]
        lines += [f"{indent}* {cell}" for cell in row]
    return "\n".join(lines + [f"{indent}{{% /table %}}"])

def tab(title, rows, **kwargs):
    return f'  {{% tab title="{title}" %}}\n{table(rows, **kwargs)}\n\n  {{% /tab %}}'

def section(model_id, body):
    return f'## {model_id}  {{% id="{model_id}" %}}\n\n{body}\n\n'

def tabbed(model_id, *tabs):
    return section(model_id, "{% tabs %}\n" + "\n".join(tabs) + "\n{% /tabs %}")

def parse(md):
    models, _notes = g._parse(md)
    return {m["id"]: m for m in models}


# 1. 分級：Batch 排在 Standard 前面也不能蓋掉標準價。
PRICED = [("Input price", "Free of charge", "$1.00"), ("Output price", "Free of charge", "$4.00")]
HALF = [("Input price", "Not available", "$0.50"), ("Output price", "Not available", "$2.00")]

by_id = parse(tabbed("gemini-a", tab("Batch", HALF), tab("Standard", PRICED), tab("Flex", HALF)))
a = by_id.get("gemini-a")
check("只取 Standard 分頁的輸入價", a and a["input_price_per_mtok"] == 1.0, a)
check("只取 Standard 分頁的輸出價", a and a["output_price_per_mtok"] == 4.0, a)

no_standard = parse(tabbed("gemini-b", tab("Batch", HALF)))
check("有分頁卻沒有 Standard 時寧可跳過", "gemini-b" not in no_standard, no_standard)

# 2. 段落之間不串價：前一段沒有表格，不能抓到下一段的。
SPLIT = section("gemini-empty", "只有文字，沒有表格。") + tabbed("gemini-c", tab("Standard", PRICED))
by_id = parse(SPLIT)
check("沒有表格的段落不算模型", "gemini-empty" not in by_id, by_id)
check("下一段照常解析", by_id.get("gemini-c", {}).get("input_price_per_mtok") == 1.0, by_id)

# 3. 沒有分頁的段落（Live 系列）：表格直接放在段落裡。
LIVE = section("gemini-live", table([
    ("Input price", "Free of charge", "$0.75 (text)    \n    $3.00 or $0.005/min (audio)"),
    ("Output price (including thinking tokens)", "Free of charge", "$4.50 (text)"),
], indent=""))
live = parse(LIVE).get("gemini-live")
check("沒有分頁的表格抓得到", live and live["input_price_per_mtok"] == 0.75, live)
check("跨行的儲存格完整留在 raw", live and "$3.00 or $0.005/min" in live["raw"]["input"], live)
check("Output price 後面有括號也認得", live and live["output_price_per_mtok"] == 4.5, live)

# 4. 跨行的時效價、Markdoc 標籤與強制換行：取第一個數字，raw 不留雜訊。
TIMED = tabbed("gemini-timed", tab("Standard", [
    ("Input price", "Free of charge", "$1.00 through December 31, 2026. \\\\\n  $2.00 starting January 1, 2027."),
    ("Output price", "Free of charge", "$5.00 or $0.01/min{% sup %}*{% /sup %} (text)"),
]))
timed = parse(TIMED).get("gemini-timed")
check("時效價取第一個數字", timed and timed["input_price_per_mtok"] == 1.0, timed)
check("raw 沒有強制換行的反斜線", timed and "\\" not in timed["raw"]["input"], timed)
check("raw 留著第二段時效價", timed and "$2.00 starting" in timed["raw"]["input"], timed)
check("raw 沒有 Markdoc 標籤", timed and "{%" not in timed["raw"]["output"], timed)

# 5. <= 和 > 是比較符號，不是 HTML 標籤，raw 不能被吃掉一段。
TIERED = tabbed("gemini-tiered", tab("Standard", [
    ("Input price", "Free of charge", "$1.25, prompts <= 200k tokens\n  $2.50, prompts > 200k tokens"),
    ("Output price", "Free of charge", "$10.00"),
]))
tiered = parse(TIERED).get("gemini-tiered")
check("分段計價取第一個數字", tiered and tiered["input_price_per_mtok"] == 1.25, tiered)
check("分段計價的第二段留在 raw", tiered and "$2.50" in tiered["raw"]["input"], tiered)

# 6. 嵌入模型：輸入列叫 Text input price，而且沒有 Output price 那一列。
EMBEDDING = tabbed("gemini-embedding-2", tab("Standard", [
    ("Text input price", "Free of charge", "$0.20"),
    ("Image input price", "Free of charge", "$0.45 ($0.00012 per image)"),
    ("Audio input price", "Free of charge", "$6.50 ($0.00016 per second)"),
]))
emb = parse(EMBEDDING).get("gemini-embedding-2")

check("Text input price 當成輸入價", emb and emb["input_price_per_mtok"] == 0.2, emb)
check("image／audio input price 不覆蓋文字價", emb and emb["raw"]["input"] == "$0.20", emb)
check("嵌入模型沒有輸出列 → unavailable",
      emb and emb["field_status"]["output_price_per_mtok"] == "unavailable", emb)

# 7. 非 token 單位的格子不能被當成每百萬 token 單價。
IMAGE = tabbed("gemini-image", tab("Standard", [
    ("Input price", "$0.30 (text / image)", "$0.30 (text / image)"),
    ("Output price", "Free of charge", "$0.039 per image{% sup %}*{% /sup %}"),
]))
image = parse(IMAGE).get("gemini-image")
check("按張計價的輸出價不進表", image and image["output_price_per_mtok"] is None, image)
check("按張計價標 unavailable",
      image and image["field_status"]["output_price_per_mtok"] == "unavailable", image)
check("繪圖模型標 modality", image and image.get("modality") == "image", image)

models, notes = g._parse(tabbed("lyria", tab("Standard", [
    ("Price", "Not available", "$0.08"),
], header=("Feature", "Free Tier", "Paid Tier, per request in USD"))))
check("表頭不是每百萬 token 就略過", models == [], models)
check("略過時留下註記", any("lyria" in note for note in notes), notes)

# 8. 付費欄明寫 Not available（只有免費層）→ unavailable，不進待覆核區。
gemma = parse(tabbed("gemma", tab("Standard", [
    ("Input price", "Free of charge", "Not available"),
    ("Output price", "Free of charge", "Not available"),
]))).get("gemma")
check("Not available → 價格為 None", gemma and gemma["input_price_per_mtok"] is None, gemma)
check("Not available → 輸入價 unavailable",
      gemma and gemma["field_status"]["input_price_per_mtok"] == "unavailable", gemma)
check("Not available → 輸出價 unavailable",
      gemma and gemma["field_status"]["output_price_per_mtok"] == "unavailable", gemma)

# 只認明確的字：讀不懂的內容仍要進待覆核，不能被一起吞掉。
weird = parse(tabbed("weird", tab("Standard", [
    ("Input price", "Free of charge", "TBD"),
    ("Output price", "Free of charge", "Contact sales"),
]))).get("weird")
check("讀不懂的輸入價仍標 needs_review",
      weird and weird["field_status"]["input_price_per_mtok"] == "needs_review", weird)
check("讀不懂的輸出價仍標 needs_review",
      weird and weird["field_status"]["output_price_per_mtok"] == "needs_review", weird)

# 9. 解析出 0 個模型要算抓取失敗，不能回空清單觸發整批假下架。
original = g.base.get_markdown
g.base.get_markdown = lambda _url: "# 改版後完全不同的頁面\n"
try:
    g.collect()
    raised = False
except g.base.FetchError:
    raised = True
finally:
    g.base.get_markdown = original
check("0 個模型要拋 FetchError", raised)


print("\n" + (f"{len(fails)} 個測試失敗" if fails else "全部通過"))
sys.exit(1 if fails else 0)
