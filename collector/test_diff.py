"""diff.py 的比對規則測試（不需網路、不需 pytest）：python collector/test_diff.py

調整解析器或比對規則後跑一次，確認沒有破壞「絕不用空資料覆蓋好資料」這條鐵則。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diff import diff_provider

def P(models=None, policies=None, status="ok", **kw):
    d = {"display_name":"X","pricing_url":"u","news_url":"n","fetch_status":status,
         "models":models or [], "policy_pages":policies or []}
    d.update(kw); return d

def M(mid, i=None, o=None, cw=1000, **kw):
    m = {"id":mid,"display_name":mid,"input_price_per_mtok":i,"output_price_per_mtok":o,
         "context_window":cw,"currency":"USD","source_url":"u",
         "field_status":{"input_price_per_mtok":"ok" if i is not None else "needs_review",
                         "output_price_per_mtok":"ok" if o is not None else "needs_review",
                         "context_window":"ok" if cw is not None else "needs_review"}}
    m.update(kw); return m

T = "2026-07-17"
fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  <-- " + str(extra)))
    if not cond: fails.append(name)

# 1. 首次執行：不產生事件
m, ev = diff_provider("p", None, P([M("a", 1.0, 2.0)]), T)
check("首次執行不產生事件", ev == [], ev)
check("首次執行設 last_changed", m["models"][0]["last_changed"] == T)

# 2. 一般降價
prev = P([M("a", 2.0, 4.0, last_changed="2026-07-01")])
m, ev = diff_provider("p", prev, P([M("a", 1.8, 4.0)]), T)
check("降價產生 1 筆 price_change", len(ev) == 1 and ev[0]["type"] == "price_change", ev)
check("降價 old/new 正確", ev[0]["old"] == 2.0 and ev[0]["new"] == 1.8, ev)
check("價格變動更新 last_changed", m["models"][0]["last_changed"] == T)
check("正常變動不標 needs_review", not m["models"][0].get("needs_review"))

# 3. 價格沒變 → 保留舊 last_changed
m, ev = diff_provider("p", prev, P([M("a", 2.0, 4.0)]), T)
check("沒變動不產生事件", ev == [], ev)
check("沒變動保留舊 last_changed", m["models"][0]["last_changed"] == "2026-07-01", m["models"][0])

# 4. 異常大跌 → needs_review
m, ev = diff_provider("p", prev, P([M("a", 0.2, 4.0)]), T)
check("跌 90% 標 needs_review", m["models"][0].get("needs_review"), m["models"][0])

# 5. 欄位解析不到 → 沿用舊值、不產生事件
m, ev = diff_provider("p", prev, P([M("a", None, 4.0)]), T)
check("解析不到不產生事件", ev == [], ev)
check("解析不到沿用舊值", m["models"][0]["input_price_per_mtok"] == 2.0, m["models"][0])
check("解析不到標 needs_review", m["models"][0].get("needs_review"))

# 6. 新模型
m, ev = diff_provider("p", prev, P([M("a", 2.0, 4.0), M("b", 1.0, 2.0)]), T)
check("新模型產生 new_model", any(e["type"]=="new_model" and e["model"]=="b" for e in ev), ev)

# 7. 模型消失 → 保留 + needs_review + removed_model
prev4 = P([M(x, 1.0, 2.0) for x in "abcd"])
m, ev = diff_provider("p", prev4, P([M("a", 1.0, 2.0)]), T)
ids = {x["id"] for x in m["models"]}
check("消失的模型不被刪除", ids == set("abcd"), ids)
check("消失的模型標 needs_review", all(x.get("needs_review") for x in m["models"] if x["id"] != "a"))
check("消失的模型標 status=missing", all(x.get("status")=="missing" for x in m["models"] if x["id"] != "a"))
check("消失的模型記 missing_since", all(x.get("missing_since")==T for x in m["models"] if x["id"] != "a"))
check("產生 removed_model 事件", sum(1 for e in ev if e["type"]=="removed_model") == 3, ev)
check("模型數量驟減 → 整家 needs_review", m.get("needs_review"), m.get("review_reasons"))

# 7b. 隔天再比一次：資料續留，但不可重複發 removed_model
T2 = "2026-07-18"
m2, ev2 = diff_provider("p", P(m["models"]), P([M("a", 1.0, 2.0)]), T2)
check("持續消失仍保留資料", {x["id"] for x in m2["models"]} == set("abcd"), m2["models"])
check("持續消失不重發 removed_model", not any(e["type"]=="removed_model" for e in ev2), ev2)
check("missing_since 維持第一天", all(x.get("missing_since")==T for x in m2["models"] if x["id"] != "a"))
check("覆核理由不重複累積",
      all(len(x.get("review_reasons",[]))==1 for x in m2["models"] if x["id"] != "a"),
      [x.get("review_reasons") for x in m2["models"]])

# 7c. 消失後又出現 → 清掉 missing 標記，且不當成新模型
m3, ev3 = diff_provider("p", P(m["models"]), P([M(x, 1.0, 2.0) for x in "abcd"]), T2)
check("回來的模型清掉 missing 標記", not any(x.get("status") for x in m3["models"]), m3["models"])
check("回來的模型不算新模型", not any(e["type"]=="new_model" for e in ev3), ev3)

# 7d. 人工確認下架（把那筆從 current.json 刪掉）後，不可再冒出來
m4, ev4 = diff_provider("p", P([x for x in m["models"] if x["id"]=="a"]), P([M("a", 1.0, 2.0)]), T2)
check("人工刪掉後不再復活", {x["id"] for x in m4["models"]} == {"a"}, m4["models"])
check("人工刪掉後不再發事件", ev4 == [], ev4)

# 7e. 事件型旗標（±50%）要跨次留著，直到人工確認為止
prev_a = P([M("a", 2.0, 4.0)])
m5, _ = diff_provider("p", prev_a, P([M("a", 0.2, 4.0)]), T)          # 暴跌那次
check("暴跌記進 pending_reviews", len(m5["models"][0].get("pending_reviews", [])) == 1, m5["models"][0])
m6, _ = diff_provider("p", P(m5["models"]), P([M("a", 0.2, 4.0)]), T2)  # 隔次價格已穩定
check("事件型旗標下次仍在", m6["models"][0].get("needs_review"), m6["models"][0])
check("事件型理由下次仍在", len(m6["models"][0].get("pending_reviews", [])) == 1, m6["models"][0])

# 7f. 人工確認（ack.py 會清掉這三個 key）後就不再冒出來
acked = [dict(x) for x in m6["models"]]
for x in acked:
    for k in ("needs_review", "review_reasons", "pending_reviews"): x.pop(k, None)
    x["acknowledged_at"] = T2
m7, _ = diff_provider("p", P(acked), P([M("a", 0.2, 4.0)]), T2)
check("確認後旗標不再出現", not m7["models"][0].get("needs_review"), m7["models"][0])
check("確認後保留 acknowledged_at", m7["models"][0].get("acknowledged_at") == T2, m7["models"][0])

# 7g. 條件型旗標（解析不到值）不進 pending，狀況修好就自己消失
m8, _ = diff_provider("p", prev_a, P([M("a", None, 4.0)]), T)
check("條件型不進 pending_reviews", not m8["models"][0].get("pending_reviews"), m8["models"][0])
m9, _ = diff_provider("p", P(m8["models"]), P([M("a", 2.0, 4.0)]), T2)
check("條件型修好就自己消失", not m9["models"][0].get("needs_review"), m9["models"][0])

# 7h. 政策頁變動同樣是事件型
m10, _ = diff_provider("p", P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:aaa"}]),
                       P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:bbb"}]), T)
check("政策變動記進 pending_reviews", m10["policy_pages"][0].get("pending_reviews"), m10["policy_pages"][0])
m11, ev11 = diff_provider("p", P(policies=m10["policy_pages"]),
                          P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:bbb"}]), T2)
check("政策事件旗標下次仍在", m11["policy_pages"][0].get("needs_review"), m11["policy_pages"][0])
check("政策事件不重發變動事件", not any(e["type"]=="policy_change" for e in ev11), ev11)

# 8. 整家抓取失敗 → 沿用全部舊資料、不產生事件
m, ev = diff_provider("p", prev, P(status="failed", fetch_error="403"), T)
check("抓取失敗沿用舊模型", len(m["models"]) == 1 and m["models"][0]["input_price_per_mtok"] == 2.0, m["models"])
check("抓取失敗不產生事件", ev == [], ev)
check("抓取失敗標 needs_review", m.get("needs_review"))

# 9. 政策頁雜湊變動
prevp = P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:aaa","last_changed":"2026-01-01"}])
m, ev = diff_provider("p", prevp, P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:bbb"}]), T)
check("政策變動產生事件", any(e["type"]=="policy_change" for e in ev), ev)
check("政策變動標 needs_review", m["policy_pages"][0].get("needs_review"))

# 10. 政策頁抓不到 → 不可誤報成變動
m, ev = diff_provider("p", prevp, P(policies=[{"label":"AUP","url":"pu","content_hash":None,"fetch_error":"timeout"}]), T)
check("政策頁抓不到不誤報變動", not any(e["type"]=="policy_change" for e in ev), ev)
check("政策頁抓不到沿用舊雜湊", m["policy_pages"][0]["content_hash"] == "sha256:aaa", m["policy_pages"][0])

# 10b. 換雜湊口徑那次要重新取基準，不可誤報成政策變動
#      （舊資料沒有 hash_method 欄位，就是 None）
prev_v1 = P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:old","last_changed":"2026-01-01"}])
curr_v2 = P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:new","hash_method":"body-v2"}])
m, ev = diff_provider("p", prev_v1, curr_v2, T)
check("換口徑不產生政策事件", not any(e["type"]=="policy_change" for e in ev), ev)
check("換口徑不標 needs_review", not m["policy_pages"][0].get("needs_review"), m["policy_pages"][0])
check("換口徑收下新雜湊", m["policy_pages"][0]["content_hash"] == "sha256:new", m["policy_pages"][0])
check("換口徑保留舊 last_changed", m["policy_pages"][0]["last_changed"] == "2026-01-01", m["policy_pages"][0])

# 10c. 換完口徑之後，同口徑下的真變動照樣要抓到
m2, ev2 = diff_provider("p", P(policies=m["policy_pages"]),
                        P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:newer","hash_method":"body-v2"}]), T2)
check("換口徑後仍抓得到真變動", any(e["type"]=="policy_change" for e in ev2), ev2)
check("換口徑後真變動標 needs_review", m2["policy_pages"][0].get("needs_review"), m2["policy_pages"][0])

# 10d. 政策頁抓不到時，口徑要跟雜湊一起沿用
#      否則下次成功抓取會拿新口徑去比舊雜湊，白白多一次假警報。
prev_v2 = P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:new","hash_method":"body-v2"}])
m3, _ = diff_provider("p", prev_v2, P(policies=[{"label":"AUP","url":"pu","content_hash":None,"fetch_error":"timeout"}]), T)
check("抓不到時沿用舊口徑", m3["policy_pages"][0].get("hash_method") == "body-v2", m3["policy_pages"][0])
m4, ev4 = diff_provider("p", P(policies=m3["policy_pages"]),
                        P(policies=[{"label":"AUP","url":"pu","content_hash":"sha256:new","hash_method":"body-v2"}]), T2)
check("抓不到隔次恢復不誤報", not any(e["type"]=="policy_change" for e in ev4), ev4)

# 11. unavailable 欄位（官方根本沒公佈）→ 不可佔用待覆核區
prev_u = P([M("a", 1.0, 2.0, cw=None)])
curr_u = P([M("a", 1.0, 2.0, cw=None)])
curr_u["models"][0]["field_status"]["context_window"] = "unavailable"
m, ev = diff_provider("p", prev_u, curr_u, T)
check("unavailable 欄位不標 needs_review", not m["models"][0].get("needs_review"), m["models"][0].get("review_reasons"))
check("unavailable 欄位維持 unavailable", m["models"][0]["field_status"]["context_window"] == "unavailable")

# 12. unavailable 的價格欄位（例如嵌入模型沒有輸出價）同理
prev_e = P([M("emb", 0.15, None)])
curr_e = P([M("emb", 0.15, None)])
curr_e["models"][0]["field_status"]["output_price_per_mtok"] = "unavailable"
m, ev = diff_provider("p", prev_e, curr_e, T)
check("unavailable 價格欄位不標 needs_review", not m["models"][0].get("needs_review"), m["models"][0].get("review_reasons"))
check("unavailable 價格欄位不產生事件", ev == [], ev)

# 13. 對照組：同樣是 None，但狀態是 needs_review 就必須標記（避免第 11、12 條把真異常也吃掉）
m, ev = diff_provider("p", P([M("a", 1.0, 2.0, cw=5000)]), P([M("a", 1.0, 2.0, cw=None)]), T)
check("真的解析不到 context 仍標 needs_review", m["models"][0].get("needs_review"), m["models"][0])
check("真的解析不到 context 沿用舊值", m["models"][0]["context_window"] == 5000)

print("\n" + (f"{len(fails)} 個測試失敗" if fails else "全部通過"))
sys.exit(1 if fails else 0)
