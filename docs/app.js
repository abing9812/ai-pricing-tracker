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
      });
    }

    for (const m of p.models || []) {
      if (!m.needs_review) continue;
      items.push({
        title: `${name} — ${m.display_name || m.id}`,
        reasons: m.review_reasons || ['欄位待覆核'],
        url: m.source_url || p.pricing_url,
        urlText: '官方定價頁',
      });
    }

    for (const page of p.policy_pages || []) {
      if (!page.needs_review) continue;
      items.push({
        title: `${name} — ${page.label}`,
        reasons: page.review_reasons || ['政策頁有變動'],
        url: page.url,
        urlText: '官方政策頁',
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

function describeChange(e, providerName) {
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
    return {
      badge: el('span', 'badge badge-removed', '未再出現'),
      text: el('span', 'change-text', `${providerName} ${model} 這次沒抓到，資料保留待確認`),
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
      const { badge, text } = describeChange(e, providerName);

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

function renderTable(current, changelog) {
  const body = document.getElementById('models-body');
  const cutoff = daysAgoISO(RECENT_DAYS);
  const changedRecently = new Set(
    changelog.filter((e) => e.date >= cutoff && e.model).map((e) => `${e.provider}/${e.model}`)
  );

  for (const [pid, p] of Object.entries(current.providers || {})) {
    for (const m of p.models || []) {
      const tr = el('tr');
      const status = m.field_status || {};

      if (m.needs_review) tr.classList.add('needs-review');
      else if (changedRecently.has(`${pid}/${m.id}`)) tr.classList.add('recently-changed');

      tr.appendChild(el('td', null, p.display_name || pid));

      const nameTd = el('td', 'model-name', m.display_name || m.id);
      tr.appendChild(nameTd);

      tr.appendChild(valueCell(fmtPrice(m.input_price_per_mtok), status.input_price_per_mtok));
      tr.appendChild(valueCell(fmtPrice(m.output_price_per_mtok), status.output_price_per_mtok));
      tr.appendChild(valueCell(fmtContext(m.context_window), status.context_window));
      tr.appendChild(el('td', null, m.last_changed || '—'));

      const srcTd = el('td');
      srcTd.appendChild(link(m.source_url || p.pricing_url, '官方'));
      tr.appendChild(srcTd);

      body.appendChild(tr);
    }
  }

  if (!body.children.length) {
    body.appendChild(el('tr')).appendChild(el('td', 'empty', '目前沒有任何模型資料。')).colSpan = 7;
  }
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
