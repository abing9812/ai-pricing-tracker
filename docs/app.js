/**
 * 儀表板：讀 data/current.json 與 data/changelog.json 並渲染四區。
 *
 * 這兩個 JSON 是 collector 從 repo 根目錄的 data/ 鏡像過來的
 * （GitHub Pages 來源設為 docs/，只服務 docs/ 底下的檔案）。
 */

const RECENT_DAYS = 7;

/** 建 DOM 用 textContent 塞字，不用 innerHTML —— 抓來的字串一律當不可信。 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function link(url, text, className = 'source-link') {
  const a = el('a', className, text);
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
}

function fmtPrice(value) {
  if (value === null || value === undefined) return null;
  // 便宜的模型價格會低到 $0.014 / 1M，固定兩位小數會顯示成 $0.01。
  const digits = value < 1 ? 3 : 2;
  return '$' + value.toFixed(digits);
}

function fmtContext(value) {
  if (value === null || value === undefined) return null;
  if (value >= 1_000_000) return (value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0) + 'M';
  if (value >= 1_000) return Math.round(value / 1_000) + 'K';
  return String(value);
}

/** 解析不到的值顯示灰字「未知」，不要顯示 0 或空白假裝有資料。 */
function valueCell(text, fieldStatus) {
  const td = el('td', 'num');
  if (text === null) {
    td.appendChild(el('span', 'unknown', '未知'));
  } else {
    td.appendChild(document.createTextNode(text));
  }
  if (fieldStatus === 'needs_review') {
    const flag = el('span', 'flag', '⚠');
    flag.title = '此欄位待覆核，請點來源確認';
    td.appendChild(flag);
  }
  return td;
}

function daysAgoISO(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/* ---------- 區塊 1：待覆核 ---------- */

function renderReview(current) {
  const list = document.getElementById('review-list');
  const items = [];

  for (const [pid, p] of Object.entries(current.providers || {})) {
    const name = p.display_name || pid;

    if (p.needs_review) {
      items.push({
        title: `${name}：整家資料待覆核`,
        reasons: p.review_reasons || ['抓取或解析有問題'],
        url: p.pricing_url,
        urlText: '官方定價頁',
        ackKey: pid,
      });
    }

    for (const m of p.models || []) {
      if (!m.needs_review) continue;
      items.push({
        title: `${name} — ${m.display_name || m.id}`,
        reasons: m.review_reasons || ['欄位待覆核'],
        url: m.source_url || p.pricing_url,
        urlText: '官方定價頁',
        ackKey: `${pid}/${m.id}`,
      });
    }

    for (const page of p.policy_pages || []) {
      if (!page.needs_review) continue;
      items.push({
        title: `${name} — ${page.label}`,
        reasons: page.review_reasons || ['政策頁有變動'],
        url: page.url,
        urlText: '官方政策頁',
        ackKey: `${pid}/${page.label}`,
      });
    }
  }

  if (!items.length) {
    list.appendChild(el('p', 'empty', '目前無待覆核項目。'));
  } else {
    for (const item of items) {
      const box = el('div', 'review-item');
      box.appendChild(el('div', 'review-title', item.title));
      for (const reason of item.reasons) {
        box.appendChild(el('p', 'review-reason', reason));
      }
      if (item.url) box.appendChild(link(item.url, item.urlText));
      // 儀表板是靜態頁，改不了 repo 裡的 JSON。看過之後要清旗標得回終端機跑
      // ack.py，所以直接把可以整行複製的指令印在這裡。
      if (item.ackKey) {
        const cmd = el('code', 'review-ack', `python collector/ack.py "${item.ackKey}"`);
        cmd.title = '看過了就在終端機跑這行，把這筆的旗標標成已確認';
        box.appendChild(cmd);
      }
      list.appendChild(box);
    }
  }
  document.getElementById('review-section').hidden = false;
}

/* ---------- 區塊 2：近 7 天變動 ---------- */

const FIELD_LABELS = {
  input_price_per_mtok: '輸入價',
  output_price_per_mtok: '輸出價',
  context_window: '情境視窗',
};

function describeChange(e, providerName, provider) {
  const model = e.model || '';

  if (e.type === 'price_change') {
    const up = e.new > e.old;
    const badge = el('span', `badge ${up ? 'badge-up' : 'badge-down'}`, up ? '漲價' : '降價');
    const text = el('span', 'change-text');
    text.appendChild(document.createTextNode(`${providerName} ${model} ${FIELD_LABELS[e.field] || e.field} `));
    text.appendChild(el('span', 'price-old', fmtPrice(e.old)));
    text.appendChild(document.createTextNode(` → ${fmtPrice(e.new)} / 1M`));
    return { badge, text };
  }

  if (e.type === 'new_model') {
    return {
      badge: el('span', 'badge badge-new', '新模型'),
      text: el('span', 'change-text', `${providerName} 新增 ${model}`),
    };
  }

  if (e.type === 'removed_model') {
    // 文案要跟總表一致，不能寫死「資料保留」：人工確認真的下架後那筆會被
    // 從 current.json 刪掉，這時再說「保留待確認」，讀的人會去總表找一列
    // 根本不存在的資料（2026-07-31 的 o1-mini 就是這樣）。
    const kept = (provider.models || []).some((m) => m.id === e.model);
    return {
      badge: el('span', 'badge badge-removed', kept ? '未再出現' : '已下架'),
      text: el(
        'span',
        'change-text',
        kept
          ? `${providerName} ${model} 這次沒抓到，資料保留待確認`
          : `${providerName} ${model} 已確認下架，已從總表移除`
      ),
    };
  }

  if (e.type === 'policy_change') {
    return {
      badge: el('span', 'badge badge-policy', '政策變動'),
      text: el('span', 'change-text', `${providerName} ${e.label || '政策頁'} 內容有變動`),
    };
  }

  return {
    badge: el('span', 'badge', e.type),
    text: el('span', 'change-text', `${providerName} ${model}`),
  };
}

function renderChanges(current, changelog) {
  const list = document.getElementById('changes-list');
  const cutoff = daysAgoISO(RECENT_DAYS);
  const recent = changelog
    .filter((e) => e.date >= cutoff)
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));

  if (!recent.length) {
    list.appendChild(el('p', 'empty', `近 ${RECENT_DAYS} 天沒有價格變動或新模型。`));
  } else {
    for (const e of recent) {
      const provider = (current.providers || {})[e.provider] || {};
      const providerName = provider.display_name || e.provider;
      const { badge, text } = describeChange(e, providerName, provider);

      const row = el('div', 'change-item');
      row.appendChild(el('span', 'change-date', e.date));
      row.appendChild(badge);
      row.appendChild(text);
      if (e.source_url) row.appendChild(link(e.source_url, '官方來源'));
      list.appendChild(row);
    }
  }
  document.getElementById('changes-section').hidden = false;
}

/* ---------- 區塊 3：四家總表 ---------- */

/**
 * 排序取值：一律取原始值，不是畫面上那串字。
 * fmtContext(1_000_000) 是 '1M'、fmtContext(200_000) 是 '200K'——
 * 照字串排會把 200K 排到 1M 前面。價格同理（'$0.014' vs '$1.10'）。
 */
const SORT_KEYS = {
  provider: (r) => r.providerName,
  model: (r) => r.modelName,
  input: (r) => r.model.input_price_per_mtok,
  output: (r) => r.model.output_price_per_mtok,
  context: (r) => r.model.context_window,
  changed: (r) => r.model.last_changed,
};

const ARROW = { asc: '↑', desc: '↓', none: '↕' };

/** 預設順序（current.json 的家別分組）。排序取消時還原成這個。 */
let tableRows = [];
let sortKey = null;
let sortDir = 'none';

/**
 * 星標：勾選的模型一律頂置在總表最上方，方便挑幾個出來並排比較。
 * 存 localStorage —— 這是使用者自己的瀏覽器偏好，不屬於抓回來的資料，
 * 不能寫進 repo 的 JSON（每次重抓會蓋掉）。
 */
const STAR_KEY = 'ai-pricing-starred';

function loadStars() {
  try {
    return new Set(JSON.parse(localStorage.getItem(STAR_KEY)) || []);
  } catch {
    return new Set();
  }
}

const starred = loadStars();

function saveStars() {
  try {
    localStorage.setItem(STAR_KEY, JSON.stringify([...starred]));
  } catch {
    // 私密視窗等寫不進去就算了，星標仍在當前頁面生效。
  }
}

function toggleStar(key) {
  if (starred.has(key)) starred.delete(key);
  else starred.add(key);
  saveStars();
  paintTable();
}

function buildTableRows(current, changelog) {
  const cutoff = daysAgoISO(RECENT_DAYS);
  const changedRecently = new Set(
    changelog.filter((e) => e.date >= cutoff && e.model).map((e) => `${e.provider}/${e.model}`)
  );

  const rows = [];
  for (const [pid, p] of Object.entries(current.providers || {})) {
    for (const m of p.models || []) {
      rows.push({
        model: m,
        key: `${pid}/${m.id}`,
        providerName: p.display_name || pid,
        modelName: m.display_name || m.id,
        sourceUrl: m.source_url || p.pricing_url,
        rowClass: m.needs_review
          ? 'needs-review'
          : changedRecently.has(`${pid}/${m.id}`)
            ? 'recently-changed'
            : null,
      });
    }
  }
  return rows;
}

function compareRows(a, b, key, sign) {
  const va = SORT_KEYS[key](a);
  const vb = SORT_KEYS[key](b);

  // 「未知」永遠沉底，不跟著升／降冪翻面：情境視窗有大半是 null，
  // 若讓它跟著翻，升冪時整片未知會蓋在最上面，正好擋住要比的東西。
  const aNull = va === null || va === undefined || va === '';
  const bNull = vb === null || vb === undefined || vb === '';
  if (aNull || bNull) return aNull && bNull ? 0 : aNull ? 1 : -1;

  const cmp =
    typeof va === 'number' && typeof vb === 'number'
      ? va - vb
      : String(va).localeCompare(String(vb), 'zh-Hant');
  return cmp * sign;
}

function sortedRows() {
  let rows = tableRows;
  if (sortKey && sortDir !== 'none') {
    const sign = sortDir === 'asc' ? 1 : -1;
    // slice()：不要就地排序 tableRows，否則預設的家別分組順序就回不去了。
    // Array#sort 是穩定的 → 同價的列維持預設順序，不必再給次要鍵。
    rows = tableRows.slice().sort((a, b) => compareRows(a, b, sortKey, sign));
  }
  // 星標列一律頂置，兩群內部各自維持目前的排序（或預設分組）順序。
  if (!starred.size) return rows;
  return [...rows.filter((r) => starred.has(r.key)), ...rows.filter((r) => !starred.has(r.key))];
}

function rowEl(r) {
  const tr = el('tr', r.rowClass);
  const status = r.model.field_status || {};

  const isStarred = starred.has(r.key);
  const starTd = el('td', 'star-cell');
  const starBtn = el('button', isStarred ? 'star-btn starred' : 'star-btn', isStarred ? '★' : '☆');
  starBtn.type = 'button';
  starBtn.title = isStarred ? '取消星標' : '星標：頂置到最上方方便比較';
  starBtn.setAttribute('aria-pressed', String(isStarred));
  starBtn.addEventListener('click', () => toggleStar(r.key));
  starTd.appendChild(starBtn);
  tr.appendChild(starTd);

  tr.appendChild(el('td', null, r.providerName));
  const nameTd = el('td', 'model-name', r.modelName);
  if (r.model.modality === 'image') {
    nameTd.appendChild(el('span', 'badge badge-image', '繪圖'));
  }
  if (r.model.status === 'missing') {
    // 官方頁已經看不到它了，這列是保留下來的舊資料——價格可能已經沒有意義，
    // 要在總表上講清楚，不能讓它跟在架上的模型長得一樣。
    const badge = el('span', 'badge badge-removed', '未再出現');
    badge.title = `${r.model.missing_since || ''} 起官方頁就沒再出現，價格為當時保留值`;
    nameTd.appendChild(badge);
  }
  tr.appendChild(nameTd);
  tr.appendChild(valueCell(fmtPrice(r.model.input_price_per_mtok), status.input_price_per_mtok));
  tr.appendChild(valueCell(fmtPrice(r.model.output_price_per_mtok), status.output_price_per_mtok));
  tr.appendChild(valueCell(fmtContext(r.model.context_window), status.context_window));
  tr.appendChild(el('td', null, r.model.last_changed || '—'));

  const srcTd = el('td');
  srcTd.appendChild(link(r.sourceUrl, '官方'));
  tr.appendChild(srcTd);
  return tr;
}

function paintTable() {
  const body = document.getElementById('models-body');
  const rows = sortedRows();

  if (!rows.length) {
    const td = el('td', 'empty', '目前沒有任何模型資料。');
    td.colSpan = 8;
    const tr = el('tr');
    tr.appendChild(td);
    body.replaceChildren(tr);
    return;
  }
  body.replaceChildren(...rows.map(rowEl));
}

function paintSortIndicators() {
  for (const th of document.querySelectorAll('#models-table th[data-sort-key]')) {
    const active = th.dataset.sortKey === sortKey && sortDir !== 'none';
    const dir = active ? sortDir : 'none';
    th.setAttribute(
      'aria-sort',
      dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'
    );
    th.classList.toggle('sorted', active);
    th.querySelector('.sort-arrow').textContent = ARROW[dir];
  }
}

function toggleSort(key) {
  // 同一欄循環：升冪 → 降冪 → 取消。留「取消」是因為預設的家別分組本身有用，
  // 少了它就只能重新整理才回得去。
  if (sortKey !== key) {
    sortKey = key;
    sortDir = 'asc';
  } else if (sortDir === 'asc') {
    sortDir = 'desc';
  } else {
    sortKey = null;
    sortDir = 'none';
  }
  paintSortIndicators();
  paintTable();
}

/** 表頭包成 button：Tab 走得到、Enter/Space 直接可用，不必自己接 keydown。 */
function initSortHeaders() {
  for (const th of document.querySelectorAll('#models-table th[data-sort-key]')) {
    const btn = el('button', 'sort-btn');
    btn.type = 'button';
    btn.append(el('span', null, th.textContent), el('span', 'sort-arrow'));
    btn.addEventListener('click', () => toggleSort(th.dataset.sortKey));
    th.replaceChildren(btn);
  }
  paintSortIndicators();
}

function renderTable(current, changelog) {
  tableRows = buildTableRows(current, changelog);
  initSortHeaders();
  paintTable();
  document.getElementById('table-section').hidden = false;
}

/* ---------- 區塊 4：頁尾 ---------- */

function renderFooter(current) {
  const stamp = document.getElementById('generated-at');
  stamp.textContent = current.generated_at
    ? `最後更新：${current.generated_at.replace('T', ' ').replace('Z', '')} UTC`
    : '最後更新時間未知';

  if (current.seed) {
    stamp.textContent += '（目前顯示的是範例種子資料，尚未真正抓取）';
  }

  const links = document.getElementById('source-links');
  for (const [pid, p] of Object.entries(current.providers || {})) {
    const group = el('div', 'source-group');
    group.appendChild(el('strong', null, p.display_name || pid));
    if (p.pricing_url) group.appendChild(link(p.pricing_url, '定價頁'));
    if (p.news_url) {
      group.appendChild(document.createTextNode(' · '));
      group.appendChild(link(p.news_url, '發佈頁'));
    }
    links.appendChild(group);
  }
  document.getElementById('footer').hidden = false;
}

/* ---------- 載入 ---------- */

async function loadJSON(path, fallback) {
  const resp = await fetch(path, { cache: 'no-store' });
  if (!resp.ok) {
    if (fallback !== undefined) return fallback;
    throw new Error(`讀取 ${path} 失敗（HTTP ${resp.status}）`);
  }
  return resp.json();
}

async function init() {
  try {
    const [current, changelog] = await Promise.all([
      loadJSON('data/current.json'),
      loadJSON('data/changelog.json', []),
    ]);

    document.getElementById('loading').remove();
    renderReview(current);
    renderChanges(current, changelog);
    renderTable(current, changelog);
    renderFooter(current);
  } catch (err) {
    const box = document.getElementById('loading');
    box.className = 'section error';
    box.textContent = `載入資料失敗：${err.message}。若是在本機開啟，請用靜態伺服器（python -m http.server）而非直接雙擊 index.html。`;
  }
}

init();
