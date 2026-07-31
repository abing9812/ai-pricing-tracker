"""openai.py 解析器測試（不需網路、不需 pytest）：python collector/test_openai_parser.py

2026-07-28 OpenAI 定價頁改版（JS rows= 陣列 → Markdown 表格、短／長情境分欄計價），
解析器重寫過。這裡用固定的 Markdown 片段驗證：取對表、取對欄、錯誤要拋得出來。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import base
from providers import openai as op

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)

def raises(fn):
    try:
        fn(); return None
    except base.FetchError as e:
        return str(e)

# 2026-07-28 改版後的頁面縮影：Standard 在前、Batch 在後，模型同名但價格減半。
NEW_PAGE = """
# Pricing

### Standard pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-sol | $5.00 | $0.50 | $6.25 | $30.00 | $10.00 | $1.00 | $12.50 | $45.00 |
| gpt-5.5 (<272K context length) | $5.00 | $0.50 | - | $30.00 | $10.00 | $1.00 | - | $45.00 |
| gpt-5-nano | $0.05 | $0.005 | - | $0.40 | - | - | - | - |
| broken-row | - | - | - | - | - | - | - | - |

### Batch pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-sol | $2.50 | $0.25 | $3.125 | $15.00 | $5.00 | $0.50 | $6.25 | $22.50 |
"""

table = op._standard_table(NEW_PAGE)
models = op._parse_table(table, "u")
by_id = {m["id"]: m for m in models}

# 1. 只取 Standard 表，不吃到後面的 Batch 半價
check("解析出 Standard 表的 4 列", len(models) == 4, [m["id"] for m in models])
check("取 Standard 價非 Batch 價", by_id["gpt-5.6-sol"]["input_price_per_mtok"] == 5.0,
      by_id["gpt-5.6-sol"])

# 2. 短／長情境分欄：主要價格取短情境欄，長情境進 raw
m = by_id["gpt-5.6-sol"]
check("輸出價取短情境欄", m["output_price_per_mtok"] == 30.0, m)
check("長情境價記進 raw", m["raw"].get("long_context_input_per_mtok") == 10.0
      and m["raw"].get("long_context_output_per_mtok") == 45.0, m["raw"])

# 3. 名稱裡的計價註記要剝掉
check("剝除 (<272K context length) 註記", "gpt-5.5" in by_id, sorted(by_id))
check("剝註記後保留原始 label", by_id["gpt-5.5"]["raw"]["label"] == "gpt-5.5 (<272K context length)")

# 4. 沒有長情境價的模型 raw 不塞 None 欄位
check("無長情境價不進 raw", "long_context_input_per_mtok" not in by_id["gpt-5-nano"]["raw"],
      by_id["gpt-5-nano"]["raw"])

# 5. '-' 解析成 None → needs_review（讓 diff 沿用舊值，而不是寫入 0 或假資料）
b = by_id["broken-row"]
check("'-' 價格解析成 None", b["input_price_per_mtok"] is None and b["output_price_per_mtok"] is None, b)
check("None 價格標 needs_review", b["field_status"]["input_price_per_mtok"] == "needs_review", b)
check("context window 維持 unavailable", b["field_status"]["context_window"] == "unavailable", b)

# 6. 若官方改回單一 Input/Output 欄位也接得住
OLD_STYLE = """
### Standard pricing data

| Model | Input | Cached input | Output |
| --- | --- | --- | --- |
| gpt-x | $1.25 | $0.125 | $10.00 |
"""
simple = op._parse_table(op._standard_table(OLD_STYLE), "u")
check("單一 Input/Output 表頭也解析得出", simple[0]["input_price_per_mtok"] == 1.25
      and simple[0]["output_price_per_mtok"] == 10.0, simple)

# 7. 頁面結構改了要拋 FetchError（整家 failed → diff 沿用舊資料），不能靜默回空
check("找不到 Standard 標題拋錯", raises(lambda: op._standard_table("# Pricing\n\nnothing here")))
check("標題在但沒表格拋錯", raises(lambda: op._standard_table("### Standard pricing data\n\n字而已")))

RENAMED = """
### Standard pricing data

| Model | Prompt price | Completion price |
| --- | --- | --- |
| gpt-x | $1.00 | $2.00 |
"""
check("欄名改掉拋錯而非抓錯欄", raises(lambda: op._parse_table(op._standard_table(RENAMED), "u")))

EMPTY = """
### Standard pricing data

| Model | Input | Output |
| --- | --- | --- |
"""
check("表格空的拋錯而非回空清單", raises(lambda: op._parse_table(op._standard_table(EMPTY), "u")))

print("\n" + (f"{len(fails)} 個測試失敗" if fails else "全部通過"))
sys.exit(1 if fails else 0)
