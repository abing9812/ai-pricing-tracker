"""anthropic.py 解析器測試（不需網路、不需 pytest）：python collector/test_anthropic_parser.py

2026-09-02 官方把定價表表頭從 `Base Input Tokens` 改成 `Base input tokens`，畫面上
只差大小寫、表格一列沒動，`_find_table` 的精確比對卻對不上，整家標 failed 沿用舊
資料，Claude 全系列價格凍了一天多。這裡驗證：新舊兩種大小寫都認得、取對欄不會拿到
隔壁的快取寫入價、表格真的不見時還是要拋得出 FetchError。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import base
from providers import anthropic as an

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)

def raises(fn):
    try:
        fn(); return None
    except base.FetchError as e:
        return str(e)


# 2026-09-02 起的頁面縮影：表頭小寫，輸入與輸出中間隔著三欄快取價。
LOWER = """
## Model pricing

The following table shows pricing for all Claude models:

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Fable 5.1 | $10 / MTok | $12.50 / MTok | $20 / MTok | $1 / MTok | $50 / MTok |
| Claude Sonnet 5 (through August 31, 2026) | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
| Claude Sonnet 5 (starting September 1, 2026) | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
"""

# 2026-09-01 之前的同一張表：表頭是 Title Case，其餘一模一樣。
UPPER = LOWER.replace("Base input tokens", "Base Input Tokens").replace(
    "Output tokens", "Output Tokens"
)

CONTEXTS = {"Claude Fable 5.1": 500_000, "Claude Sonnet 5": 1_000_000}

models = an._parse_pricing(LOWER, CONTEXTS)
by_id = {m["id"]: m for m in models}
fable = by_id.get("claude-fable-5.1")

# 1. 小寫表頭要認得 —— 就是這次壞掉的那個
check("小寫表頭解析得出模型", len(models) == 3, sorted(by_id))
check("小寫表頭取到輸入價", fable and fable["input_price_per_mtok"] == 10.0, fable)

# 2. 取對欄：輸出價是最後一欄的 $50，不是隔壁快取寫入的 $12.50
check("輸出價不會拿到快取寫入價", fable and fable["output_price_per_mtok"] == 50.0, fable)

# 3. 舊的 Title Case 表頭不能因為這次修改而壞掉
upper_ids = {m["id"] for m in an._parse_pricing(UPPER, CONTEXTS)}
check("大寫表頭照樣解析得出", upper_ids == set(by_id), upper_ids)

# 4. 表頭多幾個空白、換個大小寫組合也一樣認得（正規化順便收斂空白）
SPACED = LOWER.replace("| Base input tokens |", "|  BASE   Input Tokens  |")
check("表頭空白與大小寫都不影響", len(an._parse_pricing(SPACED, CONTEXTS)) == 3)

# 5. 時效字樣的兩列各自獨立，後者不會蓋掉前者（既有行為，一起釘住）
sonnet = [m for m in models if m["id"].startswith("claude-sonnet-5")]
check("Sonnet 5 兩段時效價各自成列", len(sonnet) == 2, [m["id"] for m in sonnet])
check(
    "時效字樣拿掉後才對得到 context window",
    all(m["context_window"] == 1_000_000 for m in sonnet),
    sonnet,
)

# 6. 表格真的不見時要拋 FetchError，不能默默回空清單（回空會被誤判成整批下架）
NO_TABLE = """
## Pricing

| Model | Additional input tokens |
| --- | --- |
| Claude Opus 5 | 325 tokens |
"""
msg = raises(lambda: an._parse_pricing(NO_TABLE, CONTEXTS))
check("找不到定價表要拋 FetchError", msg is not None, msg)

# 7. 比較表整張沒讀到時，context 全空 → 由 make_model 標 needs_review 而不是靜默 unavailable
blind = an._parse_pricing(LOWER, {})
check(
    "比較表全空時 context 標 needs_review",
    all(m["field_status"]["context_window"] == "needs_review" for m in blind),
    [m["field_status"] for m in blind],
)

print()
print(f"{len(fails)} 個失敗" if fails else "全部通過")
sys.exit(1 if fails else 0)
