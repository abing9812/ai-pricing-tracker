"""deepseek.py 解析器測試（不需網路、不需 pytest）：python collector/test_deepseek_parser.py

2026-08-18 官方把離峰折扣加回來，價格列拆成 OFF-PEAK／PEAK 兩小列，舊解析器整排
錯開一格：deepseek-v4-flash 的價格解析成 'OFF-PEAK'（沿用舊價、標待覆核），
deepseek-v4-pro 卻靜默拿到 flash 的離峰價。這裡用兩種版本的表格片段驗證：
取對時段、跨格對齊不錯位、時段標籤改名要拋錯而不是抓錯格。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import base
from providers import deepseek as ds

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)

def raises(fn):
    try:
        fn(); return None
    except base.FetchError as e:
        return str(e)


# 2026-08-18 的頁面縮影：PRICING rowspan=6、價格列標籤 rowspan=2、
# CONTEXT LENGTH 的值 colspan=2 橫跨兩個模型。
TIERED = """
<table>
<tr><td colspan="3">MODEL</td><td>deepseek-v4-flash</td><td>deepseek-v4-pro</td></tr>
<tr><td colspan="3">CONTEXT LENGTH</td><td colspan="2">1M</td></tr>
<tr>
  <td rowspan="6">PRICING (1)</td>
  <td rowspan="2">1M INPUT TOKENS (CACHE HIT)</td>
  <td>OFF-PEAK</td><td>$0.007</td><td>$0.022</td>
</tr>
<tr><td>PEAK</td><td>$0.014</td><td>$0.044</td></tr>
<tr>
  <td rowspan="2">1M INPUT TOKENS (CACHE MISS)</td>
  <td>OFF-PEAK</td><td>$0.22</td><td>$0.66</td>
</tr>
<tr><td>PEAK</td><td>$0.44</td><td>$1.32</td></tr>
<tr>
  <td rowspan="2">1M OUTPUT TOKENS</td>
  <td>OFF-PEAK</td><td>$0.66</td><td>$1.98</td>
</tr>
<tr><td>PEAK</td><td>$1.32</td><td>$3.96</td></tr>
</table>
"""

models = ds._parse(TIERED)
by_id = {m["id"]: m for m in models}
flash, pro = by_id.get("deepseek-v4-flash"), by_id.get("deepseek-v4-pro")

check("解析出兩個模型", sorted(by_id) == ["deepseek-v4-flash", "deepseek-v4-pro"], sorted(by_id))

# 1. 取 PEAK（標準價），不是 OFF-PEAK 的半價
check("flash 取尖峰輸入價", flash["input_price_per_mtok"] == 0.44, flash)
check("flash 取尖峰輸出價", flash["output_price_per_mtok"] == 1.32, flash)
check("pro 取尖峰輸入價", pro["input_price_per_mtok"] == 1.32, pro)
check("pro 取尖峰輸出價", pro["output_price_per_mtok"] == 3.96, pro)

# 2. 錯位的兩個徵狀都不能再出現：第一欄不是時段標籤、第二欄不是第一欄的離峰價
check("價格欄位都解析得到值",
      flash["field_status"]["input_price_per_mtok"] == "ok"
      and flash["field_status"]["output_price_per_mtok"] == "ok", flash["field_status"])
check("pro 沒吃到 flash 的離峰價", pro["input_price_per_mtok"] != 0.22, pro)

# 3. 取的是 CACHE MISS 不是 CACHE HIT（兩列結構一模一樣，最容易抓錯）
check("不取 cache hit 折扣價", flash["input_price_per_mtok"] != 0.014, flash)

# 4. colspan 的 context 值要複製給每個模型
check("context 兩個模型都拿到", flash["context_window"] == 1_000_000
      and pro["context_window"] == 1_000_000, [flash["context_window"], pro["context_window"]])

# 5. 離峰價記進 raw 備查
check("raw 記下取的是哪個時段", flash["raw"]["pricing_tier"] == "PEAK", flash["raw"])
check("raw 記下離峰價", flash["raw"]["input_cache_miss_off_peak"] == "$0.22"
      and flash["raw"]["output_off_peak"] == "$0.66", flash["raw"])


# 改版前的樣子：沒有時段小列。官方哪天取消折扣要接得住。
FLAT = """
<table>
<tr><td colspan="3">MODEL</td><td>deepseek-v4-flash</td><td>deepseek-v4-pro</td></tr>
<tr><td colspan="3">CONTEXT LENGTH</td><td colspan="2">1M</td></tr>
<tr><td rowspan="3">PRICING (1)</td><td colspan="2">1M INPUT TOKENS (CACHE HIT)</td><td>$0.014</td><td>$0.044</td></tr>
<tr><td colspan="2">1M INPUT TOKENS (CACHE MISS)</td><td>$0.14</td><td>$0.435</td></tr>
<tr><td colspan="2">1M OUTPUT TOKENS</td><td>$0.28</td><td>$0.87</td></tr>
</table>
"""

flat = {m["id"]: m for m in ds._parse(FLAT)}
check("無時段分列也解析得出", flat["deepseek-v4-flash"]["input_price_per_mtok"] == 0.14
      and flat["deepseek-v4-pro"]["output_price_per_mtok"] == 0.87, flat)
check("無時段分列不寫 pricing_tier", "pricing_tier" not in flat["deepseek-v4-flash"]["raw"],
      flat["deepseek-v4-flash"]["raw"])


# 6. 時段標籤改名（例如改成 STANDARD／DISCOUNT）→ 拋錯讓整家 failed 沿用舊資料，
#    不能靜默拿第一小列（很可能是折扣價）當標準價。
RENAMED_TIER = TIERED.replace("PEAK", "STANDARD")
err = raises(lambda: ds._parse(RENAMED_TIER))
check("時段標籤改名拋錯", err, err)

# 7. 整張表換掉 → 拋錯而非回空清單
check("找不到 MODEL 列拋錯", raises(lambda: ds._parse("<table><tr><td>x</td></tr></table>")))
check("整頁沒表格拋錯", raises(lambda: ds._parse("<p>nothing</p>")))

NO_PRICE_ROWS = """
<table>
<tr><td colspan="3">MODEL</td><td>deepseek-v4-flash</td></tr>
<tr><td colspan="3">CONTEXT LENGTH</td><td>1M</td></tr>
</table>
"""
check("找不到價格列拋錯", raises(lambda: ds._parse(NO_PRICE_ROWS)))

print("\n" + (f"{len(fails)} 個測試失敗" if fails else "全部通過"))
sys.exit(1 if fails else 0)
