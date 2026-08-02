"""policy_text() 的抽取規則測試（不需網路、不需 pytest）：python collector/test_policy_text.py

政策頁雜湊只算主文。這支釘住的是「哪些東西不可以進雜湊」——
導覽列、頁尾、語言選單、cookie 提示都會隨抓取來源的地區而變，
放進雜湊就會讓排程（美國 runner）與本機（台灣）互相誤報成政策變動。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import base

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)


# 外框（會隨地區變）與本文（不會）分開寫，方便下面直接斷言誰在誰不在。
CHROME_HEAD = '<header>Sign in</header><nav>Products Pricing</nav>'
CHROME_FOOT = '<footer>Manage Cookies English United States 中文（繁體中文） 日本語</footer>'
BODY = 'Effective: October 29, 2025 Do not use this for evil.'

# 1. <article>：OpenAI、Anthropic 的政策頁長這樣
html = f'<html><body>{CHROME_HEAD}<main><article>{BODY}</article></main>{CHROME_FOOT}</body></html>'
text = base.policy_text(html)
check("article 抓得到本文", text == BODY, text)
check("article 不含頁首", "Sign in" not in text, text)
check("article 不含頁尾語言選單", "日本語" not in text, text)

# 2. role="article"：Google 的政策頁用 role 屬性而不是語意標籤
html = f'<html><body>{CHROME_HEAD}<div role="main"><nav>Overview FAQ</nav>' \
       f'<div role="article">{BODY}</div></div>{CHROME_FOOT}</body></html>'
text = base.policy_text(html)
check("role=article 抓得到本文", text == BODY, text)
check("role=article 不含頁內導覽", "Overview FAQ" not in text, text)

# 3. 只有 <main>：退一層，仍然把頁首頁尾擋掉
html = f'<html><body>{CHROME_HEAD}<main>{BODY}</main>{CHROME_FOOT}</body></html>'
text = base.policy_text(html)
check("main 抓得到本文", text == BODY, text)
check("main 不含頁尾", "Manage Cookies" not in text, text)

# 4. 沒有任何容器：退回整頁（DeepSeek 的政策頁是 CDN 上的靜態 HTML，整頁就是本文）
html = f'<html><body><h1>Terms</h1><p>{BODY}</p></body></html>'
text = base.policy_text(html)
check("沒有容器就退回整頁", text == f"Terms {BODY}", text)

# 5. script/style 一律不算（build id、nonce 藏在這裡，每次抓都不一樣）
html = f'<html><body><script>var buildId="{"x" * 8}";</script>' \
       f'<style>.a{{color:red}}</style><article>{BODY}</article></body></html>'
check("不算 script 與 style", base.policy_text(html) == BODY, base.policy_text(html))

# 6. 只有外框變動時，雜湊必須不動 —— 這就是整件事要防的誤報
a = f'<html><body>{CHROME_HEAD}<article>{BODY}</article><footer>English United States</footer></body></html>'
b = f'<html><body>{CHROME_HEAD}<article>{BODY}</article><footer>English Taiwan</footer></body></html>'
check("外框變、本文沒變 → 雜湊不變",
      base.sha256_text(base.policy_text(a)) == base.sha256_text(base.policy_text(b)),
      (base.policy_text(a), base.policy_text(b)))

# 7. 對照組：本文真的改了就一定要變（別把第 6 條做過頭）
c = a.replace("Do not use this for evil.", "Do not use this for evil or for profit.")
check("本文變了 → 雜湊要變",
      base.sha256_text(base.policy_text(a)) != base.sha256_text(base.policy_text(c)))

# 8. 口徑常數要跟著 policy_page() 寫進資料，diff 靠它判斷要不要重新取基準
check("policy_page 帶出 hash_method", base.POLICY_HASH_METHOD == "body-v2", base.POLICY_HASH_METHOD)

print("\n" + (f"{len(fails)} 個測試失敗" if fails else "全部通過"))
sys.exit(1 if fails else 0)
