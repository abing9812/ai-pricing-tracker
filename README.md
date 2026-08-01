# AI 模型價格與政策追蹤器

每 12 小時自動抓取 OpenAI、Anthropic、Google Gemini、DeepSeek 四家的官方定價頁與政策頁，
標出近 7 天的價格變動與新模型，**每個數字旁邊都有官方連結**，點過去三秒確認。

全部跑在 GitHub 免費資源上：Actions 排程抓取 → 結果 commit 回 repo → Pages 顯示儀表板。
不需要任何 API 金鑰。

- **儀表板**：`https://<你的帳號>.github.io/<repo 名>/`
- **資料**：`data/current.json`（最新狀態）、`data/changelog.json`（變動流水帳，只追加）

## 這個工具的定位

數字**供參考**，連結**做定案**。

只追蹤三個核心欄位：輸入單價、輸出單價、情境視窗，統一換算成「每百萬 token（USD）」。
快取折扣、批次折扣、分級定價、長文加價**都不追蹤** —— 那些規則各家不同又常變，
硬要抓只會製造假訊息。要查那些請點官方連結。

政策頁**不解析語意**，只算內容雜湊，偵測「跟上次比有沒有變」，有變就標記請你自己去看。

## 快速開始

### 本機執行

```bash
pip install -r collector/requirements.txt

python collector/main.py              # 抓四家，比對後寫回 data/
python collector/main.py --only openai   # 只跑一家，調解析器時很好用
python collector/main.py --dry-run       # 只印結果，不寫檔
python collector/test_diff.py            # 比對邏輯的測試（不需網路）
```

### 本機看儀表板

```bash
python -m http.server 8000 --directory docs
# 開 http://localhost:8000
```

不能直接雙擊 `index.html`：`file://` 下 `fetch` 會被瀏覽器擋掉。

### 部署到 GitHub

1. 把這個 repo 推上 GitHub。
2. **Settings → Pages**：Source 選 `Deploy from a branch`，branch 選 `main`、
   資料夾選 **`/docs`**。
3. **Settings → Actions → General → Workflow permissions**：選
   **Read and write permissions**（Actions 要把抓取結果 commit 回 repo）。
4. **Actions → track → Run workflow** 手動跑一次，確認正常後就會自動照排程跑。

排程是 **每 12 小時一次：台北時間 08:00、20:00**（UTC 00:00、12:00），
寫在 `.github/workflows/track.yml`。

**實際開跑會晚很多，這是已知且可接受的。** 這兩格正好是 GitHub Actions 的壅塞時段
（官方明講 schedule 在高負載時會延遲、每小時開頭最嚴重）；實測本 repo 的 00:00 排程
穩定延遲 **+7～13 小時**（名目 UTC 00:00，實際都在 09:00-12:40 UTC），中位數約 9.5 小時。
價格追蹤早幾小時晚幾小時不影響判讀，所以維持整點。若哪天需要準時，把分鐘挪離整點、
避開 UTC 午夜即可（例如 `50 23,11 * * *`）。

變動事件只記日期、不記時分，記的是 **UTC 日期**（`generated_at` 也是 UTC）。
名目時間下 UTC 日期與台北日期一致（08:00 與 20:00 都落在同一個 UTC 日），
所以同一天的兩次抓取會標同一個日期，`data/changelog.json` 每個日期最多兩批事件。
不影響比對正確性（第一次抓到的變動會寫回 `current.json`，第二次就不會重複偵測）。
延遲很久時晚上那次可能跨過 UTC 午夜、標成隔天，屬正常。

## repo 裡的資料是什麼

`data/current.json` 裡是 **2026-07-17 實際抓取的真實資料**（四家共 82 個模型），
所以 clone 下來打開儀表板就能看到完整版面與真實價格，不必等第一次排程。

`data/changelog.json` 是空的 `[]` —— 變動流水帳會從你第一次跑排程之後開始累積。
（沒有塞假的變動紀錄：那些會指向真實的模型 id 卻掛著虛構的價格，看起來像真的一樣。）

### 種子資料機制

程式支援 `"seed": true` 旗標：帶有這個旗標的 `current.json` 會被當作**空的基準**——
不產生任何變動事件、清掉 changelog，然後寫入真實資料。這是為了避免拿假資料當比對基準時，
第一次真正抓取會噴出一整排假的「降價」。

目前 repo 附的是真實資料所以用不到它。若你想從零開始（例如想自己確認首次執行的行為），
把 `data/current.json` 刪掉即可，或改成 `{"seed": true, "generated_at": "...", "providers": {...}}`。

## 資料來源（2026-07 實測）

⚠ **這四個網址都跟一般直覺不同，改動前請先讀這段。**

| 家別 | 定價來源 | 為什麼是這個 |
|------|----------|--------------|
| OpenAI | [`developers.openai.com/api/docs/pricing.md`](https://developers.openai.com/api/docs/pricing.md) | `openai.com/api/pricing/` 無 UA 會 **403**，帶瀏覽器 UA 會被導到企業方案頁，**那裡根本沒有 token 單價**。`platform.openai.com` 已 301 到 `developers.openai.com`。文件站有 Markdown 版，不擋機器人。 |
| Anthropic | [`platform.claude.com/docs/en/about-claude/pricing.md`](https://platform.claude.com/docs/en/about-claude/pricing.md) | `anthropic.com/pricing` 已導向 `claude.com/pricing`，那是**消費者訂閱方案**頁，沒有 API 單價。文件站從 `docs.anthropic.com` 搬到 `platform.claude.com`。完全不擋機器人。 |
| Google | [`ai.google.dev/gemini-api/docs/pricing`](https://ai.google.dev/gemini-api/docs/pricing) | 沒有 Markdown 版也沒有 JSON，只能解析 HTML（好在價格在原始 HTML 裡）。 |
| DeepSeek | [`api-docs.deepseek.com/quick_start/pricing/`](https://api-docs.deepseek.com/quick_start/pricing/) | 規格原網址正確，只是會補尾斜線。沒有 JSON，文件原始碼未公開。 |

政策頁：OpenAI [usage policies](https://openai.com/policies/usage-policies/)、
Anthropic [AUP](https://www.anthropic.com/legal/aup)、
Google [prohibited use policy](https://policies.google.com/terms/generative-ai/use-policy)、
DeepSeek [terms of service](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)。

### 四個會咬人的坑

改解析器前務必知道，每個都已經在程式碼裡處理掉並註解了：

1. **Google 千萬不要送瀏覽器 UA。** 送 Chrome UA 會被 302 進 OAuth 登入流程，
   拿回一張沒有價格的頁面。用 requests 預設 UA 反而直接 200。**與其他三家完全相反。**
2. **DeepSeek 不能用狀態碼判斷成功。** 它的 Docusaurus 對**任何**不存在的路徑都回
   200 + 首頁 HTML。網址一旦失效，你會拿到一份看似正常、實則沒有價格的文件。
   `collect()` 裡有內容斷言擋這件事。
3. **各家定價頁都有多張分級表。** OpenAI 有 standard/batch/flex/priority 四張、
   Google 71 張表裡 standard 只佔 21 張。同一個模型在每張表都出現、價格不同，
   無腦全解析會讓 batch 半價覆蓋掉標準價。解析器只認 standard。
4. **`.md` 網址不是全站通用。** OpenAI 的 `models/*.md` 回 200 但 content-type 是
   `text/html`。`base.get_markdown()` 會驗 content-type，不只看 200。

## 「待覆核」是怎麼決定的

這是本工具的半自動核心。以下情況會讓項目進入儀表板最上方的 ⚠ 待覆核區：

- 某家抓取失敗（`fetch_status` 為 `failed` / `partial`）
- 某欄位這次解析不到值（沿用上次的數字，並標記）
- 價格單次變動超過 **±50%**（多半是解析錯位，不是真降價）
- 某家模型數量掉到上次的 **75% 以下**
- 政策頁雜湊改變
- 解析出重複的模型 id

### 旗標什麼時候會消失：條件型 vs 事件型

**條件型**（解析不到值、抓取失敗、模型未再出現、模型數量驟減）每次比對都會重新判定，
狀況修好旗標就自己消失。不需要人工介入，人工清了也會再回來 —— 因為問題還在。

**事件型**（價格單次變動超過 ±50%、政策頁雜湊改變）只在事發那一次算得出來：
下次比對時價格已經寫回 `current.json`、雜湊也已經對上，旗標會自己消失。
排程是 12 小時一次，晚上 20:00 抓到的事情，早上 08:00 那次一跑就被清掉了。

所以事件型理由會另外記進項目的 `pending_reviews`，跨次帶著，**直到人工確認為止**：

```bash
python collector/ack.py                       # 列出目前所有待覆核項目
python collector/ack.py openai/gpt-5.6-luna   # 確認一筆模型
python collector/ack.py google/"Prohibited use policy"   # 確認一個政策頁
python collector/ack.py openai                # 確認 OpenAI 底下全部
python collector/ack.py --all                 # 全部確認
python collector/ack.py --all --dry-run       # 只看會清掉什麼，不寫檔
```

確認後該筆的 `needs_review` / `review_reasons` / `pending_reviews` 會被清掉，
換成 `acknowledged_at`（比對時會一路帶著，之後的抓取不會再把同一件事亮回來）。
`ack.py` 會同時更新 `data/` 與 `docs/data/`，**改完記得 commit**，否則線上儀表板不會變。

儀表板每一筆待覆核底下都印了對應的 `ack.py` 指令，整行複製到終端機就行
（靜態頁改不了 repo 裡的 JSON，所以清旗標一定得回終端機跑）。

確認的意思是「我看過了」，不是「這筆沒問題」：真的抓錯就去修解析器，
真的下架就把該筆從 `current.json` 刪掉（見下一節）。

### 模型從來源消失時

不會自動刪除，而是保留舊資料並標上：

- `status: "missing"` 與 `missing_since`（第一次沒抓到的日期）
- 總表那列會出現「未再出現」標籤，並進待覆核區

`removed_model` 事件**只在消失的第一天發一次**。保留下來的模型會一直留在上次的資料裡，
若每次比對都發事件，changelog 會天天多一筆同樣的下架通知。

人工確認真的下架後，**把那筆從 `data/current.json`（與 `docs/data/current.json`）刪掉**即可，
下次比對不會再把它加回來，儀表板也會把當初那筆事件改顯示成「已確認下架，已從總表移除」。
確認之前不要動它 —— 模型消失多半是解析失敗而不是真的下架
（2026-07-28 OpenAI 定價頁改版就讓 45 個模型整批「消失」了四天）。

### 欄位的三種狀態

`field_status` 裡每個欄位是 `ok` / `needs_review` / `unavailable`：

- `needs_review` — 該來源本來有這個欄位，但這次沒解析到 → **進待覆核區**，多半是頁面改版。
- `unavailable` — **官方來源根本沒公佈這個欄位** → 顯示「未知」但**不進待覆核區**。

分這兩種是刻意的設計決定，與規格書字面（「欄位解析不到值就標 needs_review」）有一小步偏離。
原因：OpenAI 與 Google 的定價頁完全沒有 context window，若照字面做，待覆核區會**永遠**
塞著 65 筆點過去也沒用的項目，把真正的異常淹掉 —— 那等於廢掉這個區塊的價值。
永久性的已知缺口記在下面的「已知缺口」，不佔用人工注意力。

判斷刻意保守：只有解析器**明確知道**這是結構性缺口時才標 `unavailable`。
例如 Anthropic 的視窗對照表整張讀不到時，所有模型會標 `needs_review`（真的出事了）；
只有在對照表讀到了、但某個退役模型不在表上時，才標 `unavailable`。

## 已知缺口

| 缺口 | 影響 | 之後想補的話 |
|------|------|--------------|
| **OpenAI 沒有 context window** | 45 個模型的視窗顯示「未知」 | 定價頁沒有這個欄位。每個模型的獨立頁面（`/api/docs/models/<id>`）有，但要逐一抓 45 次，且只能靠 tailwind class 定位、很脆弱。 |
| **Google 沒有 context window** | 20 個模型的視窗顯示「未知」 | 定價頁沒有；models 頁的規格表是 client-rendered（原始 HTML 的 `<table>` 數為 0）。ListModels API 有 `inputTokenLimit`，但**需要 API 金鑰**（無金鑰回 403），且該 API 不含價格。若你願意加金鑰，可用 repo secret 存放後只用來補這個欄位。 |
| **Anthropic 部分模型沒有視窗** | 退役／限量機種（Mythos 5、Opus 4、Sonnet 4、Haiku 3.5）顯示「未知」 | 它們不在官方的「最新模型比較表」裡。這是官方文件的結構，不是解析失敗。 |
| **OpenAI 只記標準區間價** | `gpt-5.5` 等模型顯示的是 272K 以內的價格 | 超過 272K 的請求官方另以 2× input / 1.5× output 計價，屬分級定價，依規格 §5 不追蹤。原始標籤留在 `raw.label`。 |
| **Google 多值格只取第一個** | 例如「$0.25 (text/image/video) $0.50 (audio)」只記 0.25 | 依 modality 或 ≤/>200k 分歧的價格屬分級定價，不追蹤。完整原文留在 `raw`。 |
| **按張計價的模型不記輸出價** | `gemini-2.5-flash-image` 輸出顯示「未知」 | 它是 `$0.039 per image`，不是每百萬 token，無法放進本 schema。硬記會在儀表板上變成便宜到荒謬的假數字。 |
| **OpenAI 繪圖模型的口徑** | `gpt-image` 系列的輸入價＝文字提示、輸出價＝圖片輸出 | 官方表把每個模型拆成 Image / Text 兩列。文生圖的主要成本路徑是文字進、圖片出，故取這兩格；圖片輸入（編輯／參考圖）與快取價在 `raw.modalities`。 |
| **Imagen 4 / Veo / sora 不追蹤** | Google Imagen 4（$0.02–0.06/張）、Veo 與 OpenAI sora（按秒）不在表上 | 它們整組都是按張／按秒計價，沒有任何每百萬 token 的數字可記。要查請點官方定價頁。 |

## 之後怎麼一家一家把解析規則調準

各家解析器完全獨立（`collector/providers/<家別>.py`），一家壞掉不影響其他家。

```bash
python collector/main.py --only google --dry-run   # 只跑一家、不寫檔，看解析結果
```

每支解析器最上面的 docstring 都記著**那一家的網址為什麼是這個、有哪些坑**。
改完後跑 `python collector/test_diff.py` 確認沒有破壞比對規則。

頁面改版時，解析器該做的是**拋例外**（`raise base.FetchError(...)`），不是回傳空清單 ——
拋例外會讓該家標 `failed` 並**沿用上次的好資料**；回傳空清單則可能被誤判成「模型全下架」。

## 專案結構

```
.github/workflows/track.yml   排程 + 手動觸發，跑完 commit 結果
collector/
  main.py                     進入點：協調抓取、比對、寫檔
  diff.py                     比對與變動偵測規則
  ack.py                      人工確認：看過待覆核項目後清掉旗標
  test_diff.py                比對邏輯測試（不需網路）
  providers/
    base.py                   共用抓取／解析工具
    openai.py  anthropic.py  google.py  deepseek.py
data/
  current.json                目前最新狀態（附種子資料）
  changelog.json              變動流水帳，只追加
docs/                         GitHub Pages 根目錄
  index.html  app.js  style.css
  data/                       ← data/ 的鏡像，由 main.py 自動產生
```

### 為什麼 `docs/data/` 是鏡像

GitHub Pages 的來源設為 `docs/` 時，**只會服務 `docs/` 底下的檔案**，儀表板讀不到外面的
`data/`。所以 `main.py` 寫完 `data/`（正本）後會複製一份到 `docs/data/`，儀表板讀
`./data/current.json`，本機與線上行為一致。兩份都會被 commit。

## 穩健性

- **絕不用空資料覆蓋好資料。** 抓取失敗時保留該家上次的資料，只把 `fetch_status`
  設為 `failed` 並標待覆核。
- 單一家出錯不會讓整個流程崩潰，四家用 try/except 各自隔離。
- 模型「消失」時**不會自動刪除**，保留舊資料並標 `status: "missing"` 待覆核 ——
  多半是解析失敗，不是真的下架；`removed_model` 事件只發一次，不會天天重複通知。
- 政策頁抓不到時沿用上次的雜湊，不會誤報成「政策變動」。
- 事件型的待覆核旗標（價格暴跌、政策頁改內容）跨次留著，直到人工用 `ack.py` 確認 ——
  不會因為下一次排程跑過就自己消失。
- 每次執行具冪等性，可重複安全執行。
- 政策頁雜湊算的是**可見文字**而非原始 HTML，避免 build id、nonce 每天變動造成誤報。

## 免責

自動抓取的結果可能有誤或落後於官方頁面。**任何決策請以官方頁面為準**，
這也是為什麼每個數字旁邊都放了官方連結。
