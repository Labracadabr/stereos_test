// UI: keeps uploaded files in memory and sends them to the local server for analysis.
const PRESET_QUESTIONS = [
  'Какой срок поставки действует по договору STN-101 на 10 марта 2026 года?',
  'Какова сумма договора STN-102?',
  'Какова сумма договора STN-103?',
  'По каким договорам поставка запланирована раньше 1 апреля 2026 года и есть ли несогласованные изменения срока?',
];
const STATUS_LABELS = {ok: '', no_data: 'нет данных', review: 'требует проверки'};

const state = {files: new Map(), analysis: null, selected: null, viewing: null};
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
function fmtDate(iso) { return iso ? iso.split('-').reverse().join('.') : ''; }

async function readFileB64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function payload(extra = {}) {
  return JSON.stringify({
    files: [...state.files].map(([name, content_b64]) => ({name, content_b64})),
    check_date: $('checkDate').value,
    ...extra,
  });
}

async function post(url, extra) {
  const res = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: payload(extra)});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function refresh() {
  if (!state.files.size) {
    state.analysis = null;
    render();
    return;
  }
  state.analysis = await post('/api/analyze');
  const numbers = state.analysis.contracts.map((c) => c.number);
  if (!numbers.includes(state.selected)) state.selected = numbers[0] ?? null;
  render();
  if (state.viewing) openSource(state.viewing);
}

// ---------------------------------------------------------------- rendering

function citeLink(c, label) {
  if (!c) return '';
  const text = label ?? `${c.file}, стр. ${c.line}`;
  return `<a href="#" class="cite" data-file="${esc(c.file)}" data-line="${c.line}" data-start="${c.start}" data-end="${c.end}" title="${esc(c.quote)}">${esc(text)}</a>`;
}
function quote(c) {
  return c ? `<blockquote>«${esc(c.quote)}» <span class="src">${citeLink(c)}</span></blockquote>` : '';
}
function badge(text, cls) { return text ? `<span class="badge ${cls}">${esc(text)}</span>` : ''; }

function render() {
  const a = state.analysis;
  $('docsCount').textContent = state.files.size ? `(${state.files.size})` : '';
  $('emptyHint').hidden = !!state.files.size;
  $('tableSection').hidden = !a;
  $('askSection').hidden = !a;
  renderDocList(a);
  if (!a) {
    $('answer').innerHTML = '';
    $('viewerBody').innerHTML = '';
    return;
  }
  $('tableDate').textContent = fmtDate(a.check_date);
  renderTable(a);
  renderDetails(a);
}

function docRoles(a) {
  const roles = new Map();
  for (const c of a.contracts) {
    roles.set(c.file, [{text: 'Договор ' + c.number, cls: 'neutral'}]);
    for (const r of c.related) {
      const list = roles.get(r.file) ?? [];
      list.push({text: `${c.number}: ${r.role_label.toLowerCase()}`, cls: r.role, title: r.reason});
      roles.set(r.file, list);
    }
  }
  for (const u of a.unlinked) roles.set(u.file, [{text: 'не связан: ' + u.reason, cls: 'needs_review'}]);
  return roles;
}

function renderDocList(a) {
  const roles = a ? docRoles(a) : new Map();
  const docs = new Map((a?.documents ?? []).map((d) => [d.name, d]));
  $('docList').innerHTML = [...state.files.keys()].map((name) => {
    const d = docs.get(name);
    const tags = (roles.get(name) ?? []).map((r) => `<span class="badge ${r.cls}" title="${esc(r.title ?? '')}">${esc(r.text)}</span>`).join('');
    return `<li><a href="#" class="open-doc" data-file="${esc(name)}">${esc(name)}</a>
      <span class="muted small">${esc(d?.kind_label ?? '')}</span> ${tags}
      <button class="link remove" data-file="${esc(name)}" title="Убрать файл">×</button></li>`;
  }).join('');
}

function fieldCell(f) {
  let html = `<div class="value ${f.status}">${esc(f.status === 'ok' ? f.display : STATUS_LABELS[f.status])}</div>`;
  if (f.original) html += `<div class="small"><s>${esc(f.original.display)}</s> → изменено ${citeLink(f.citation, f.changed_by.file)}</div>`;
  else if (f.citation) html += `<div class="small">${citeLink(f.citation)}</div>`;
  else if (f.hints.length) html += `<div class="small">${citeLink(f.hints[0], 'почему нет данных')}</div>`;
  for (const p of f.proposals) {
    html += `<div class="small proposal">не согласовано: ${esc(p.display)} (${citeLink(p.citation, p.file)})</div>`;
  }
  return html;
}

function renderTable(a) {
  const tbody = $('contractsTable').querySelector('tbody');
  if (!a.contracts.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">Среди загруженных файлов не найдено договоров.</td></tr>';
    return;
  }
  tbody.innerHTML = a.contracts.map((c) => {
    const f = c.fields;
    const attention = [];
    if (c.needs_review) attention.push(badge('требует проверки', 'needs_review'));
    if (Object.values(f).some((x) => x.proposals.length)) attention.push(badge('есть несогласованные изменения', 'proposal'));
    if (Object.values(f).some((x) => x.changed_by)) attention.push(badge('изменено допсоглашением', 'applied'));
    if (Object.values(f).some((x) => x.status === 'no_data')) attention.push(badge('нет данных', 'no_data'));
    return `<tr data-number="${esc(c.number)}" class="${c.number === state.selected ? 'selected' : ''}">
      <td><b>${esc(c.number)}</b><div class="small">${citeLink({file: c.file, line: 1, start: 0, end: 0, quote: c.title}, c.file)}</div></td>
      <td>${esc(c.supplier.display)}${c.supplier.citation ? `<div class="small">${citeLink(c.supplier.citation)}</div>` : ''}</td>
      <td>${fieldCell(f.amount)}</td>
      <td>${fieldCell(f.deadline)}</td>
      <td>${attention.join(' ') || '<span class="muted small">—</span>'}</td>
    </tr>`;
  }).join('');
}

function fieldDetails(f) {
  let html = `<h4>${esc(f.label)}: <span class="value ${f.status}">${esc(f.status === 'ok' ? f.display : STATUS_LABELS[f.status])}</span></h4>`;
  if (f.status === 'ok') html += `<div class="muted small">Основание${f.changed_by ? ' (действующая редакция)' : ''}:</div>` + quote(f.citation);
  if (f.original) html += `<div class="muted small">Исходное значение в договоре: <s>${esc(f.original.display)}</s></div>` + quote(f.original.citation);
  if (f.status === 'no_data') {
    html += '<div class="muted small">Значение в тексте не найдено, поэтому оно не выводится.</div>';
    for (const h of f.hints) html += quote(h);
  }
  for (const c of f.candidates ?? []) html += `<div class="muted small">Кандидат: ${esc(c.display)}</div>` + quote(c.citation);
  for (const i of f.issues) html += `<div class="issue">⚠ ${esc(i)}</div>`;
  if (f.ignored.length) {
    html += '<div class="muted small">В тексте есть другие суммы, которые не являются стоимостью договора:</div>' +
      f.ignored.map((i) => `<div class="small">${esc(i.display)}</div>` + quote(i.citation)).join('');
  }
  if (f.proposals.length) {
    html += '<div class="muted small">Несогласованные изменения (не учтены в значении):</div>' +
      f.proposals.map((p) => `<div class="small proposal">${esc(p.display)} — ${esc(p.kind_label)} ${esc(p.file)}: ${esc(p.reason)}</div>` + quote(p.citation)).join('');
  }
  return `<div class="field">${html}</div>`;
}

function renderDetails(a) {
  const c = a.contracts.find((x) => x.number === state.selected);
  if (!c) { $('details').innerHTML = ''; return; }
  const related = c.related.length
    ? c.related.map((r) => `<li>${citeLink(r.status_citation ?? {file: r.file, line: 1, start: 0, end: 0, quote: r.title}, r.file)}
        — ${esc(r.kind_label)}: ${badge(r.role_label, r.role)} <span class="muted small">${esc(r.reason)}</span></li>`).join('')
    : '<li class="muted">Связанных документов нет</li>';
  const unlinked = a.unlinked.length
    ? `<h3>Не связанные с договорами документы</h3><ul>${a.unlinked.map((u) => `<li>${esc(u.file)} — ${esc(u.kind_label)}: ${esc(u.reason)}</li>`).join('')}</ul>`
    : '';
  $('details').innerHTML = `<div class="card">
    <h3>Договор ${esc(c.number)} — основания</h3>
    ${c.issues.map((i) => `<div class="issue">⚠ ${esc(i)}</div>`).join('')}
    ${fieldDetails(c.fields.amount)}
    ${fieldDetails(c.fields.deadline)}
    <h4>Связанные документы</h4><ul>${related}</ul>
  </div>${unlinked}`;
}

function renderAnswer(ans) {
  const items = ans.items.map((it) => {
    const r = it.result;
    const basis = [r.citation, ...(r.status === 'no_data' ? r.hints : [])].filter(Boolean);
    let html = basis.length ? '<div class="muted small">Основание:</div>' + basis.map(quote).join('') : '';
    if (r.original) html += `<div class="muted small">В исходном договоре было ${esc(r.original.display)}:</div>` + quote(r.original.citation);
    for (const p of r.proposals) {
      html += `<div class="small proposal">Не согласовано (${esc(p.reason.toLowerCase())}): ${esc(p.display)}</div>` + quote(p.citation);
    }
    return `<div class="answer-item"><b>${esc(it.contract)}</b>: <span class="value ${r.status}">${esc(r.status === 'ok' ? r.display : STATUS_LABELS[r.status])}</span>
      ${html}</div>`;
  }).join('');
  const asOf = ans.as_of !== $('checkDate').value ? `<div class="muted small">Ответ рассчитан на дату из вопроса: ${fmtDate(ans.as_of)}</div>` : '';
  $('answer').innerHTML = `<div class="card"><div class="answer-text">${esc(ans.text).replace(/\n/g, '<br>')}</div>${asOf}${items}</div>`;
}

// ---------------------------------------------------------------- source viewer

function openSource({file, line, start, end}) {
  const doc = state.analysis?.documents.find((d) => d.name === file);
  if (!doc) return;
  state.viewing = {file, line, start, end};
  const meta = [doc.kind_label];
  if (doc.signed === true) meta.push('подписан ' + fmtDate(doc.signed_date));
  if (doc.signed === false) meta.push('не подписан');
  if (doc.effective_date) meta.push('вступает в силу ' + fmtDate(doc.effective_date));
  $('viewerTitle').textContent = file;
  $('viewerMeta').textContent = meta.join(' · ');
  $('viewerBody').innerHTML = doc.paragraphs.map((p) => {
    let text = esc(p.text);
    const hit = p.line === line && end > start;
    if (hit) text = esc(p.text.slice(0, start)) + '<mark>' + esc(p.text.slice(start, end)) + '</mark>' + esc(p.text.slice(end));
    return `<div class="para ${p.line === line ? 'current' : ''}" id="line-${p.line}"><span class="ln">${p.line}</span><span>${text}</span></div>`;
  }).join('');
  document.getElementById('line-' + line)?.scrollIntoView({block: 'center', behavior: 'smooth'});
}

// ---------------------------------------------------------------- events

const isTxt = (f) => /\.txt$/i.test(f.name) || f.type === 'text/plain';

async function addFiles(fileList) {
  const all = [...fileList];
  const accepted = all.filter(isTxt);
  const skipped = all.filter((f) => !isTxt(f)).map((f) => f.name);
  const replaced = accepted.filter((f) => state.files.has(f.name)).map((f) => f.name);
  const contents = await Promise.all(accepted.map(readFileB64));
  accepted.forEach((f, i) => state.files.set(f.name, contents[i]));

  const notes = [];
  if (accepted.length) notes.push(`Добавлено файлов: ${accepted.length}.`);
  if (replaced.length) notes.push(`Заменены файлы с тем же именем: ${replaced.join(', ')}.`);
  if (skipped.length) notes.push(`Пропущены (не TXT): ${skipped.join(', ')}.`);
  $('uploadNote').textContent = notes.join(' ');
  $('uploadNote').className = 'small ' + (skipped.length ? 'issue' : 'muted');
  $('uploadNote').hidden = !notes.length;
  if (accepted.length) await refresh();
}

// Drag-and-drop: files can be dropped anywhere on the page; the drop zone lights up while dragging.
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes('Files');
document.addEventListener('dragenter', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  if (dragDepth++ === 0) document.body.classList.add('dragging');
});
document.addEventListener('dragover', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
});
document.addEventListener('dragleave', (e) => {
  if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('dragging'); }
});
document.addEventListener('drop', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('dragging');
  addFiles(e.dataTransfer.files).catch(showError);
});
$('dropZone').addEventListener('click', () => $('fileInput').click());
$('dropZone').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('fileInput').click(); }
});

document.addEventListener('click', (e) => {
  const cite = e.target.closest('.cite');
  if (cite) {
    e.preventDefault();
    e.stopPropagation();
    const d = cite.dataset;
    openSource({file: d.file, line: +d.line, start: +d.start, end: +d.end});
    return;
  }
  const open = e.target.closest('.open-doc');
  if (open) {
    e.preventDefault();
    openSource({file: open.dataset.file, line: 1, start: 0, end: 0});
    return;
  }
  const remove = e.target.closest('.remove');
  if (remove) {
    state.files.delete(remove.dataset.file);
    if (state.viewing?.file === remove.dataset.file) state.viewing = null;
    refresh().catch(showError);
    return;
  }
  const row = e.target.closest('#contractsTable tbody tr[data-number]');
  if (row) {
    state.selected = row.dataset.number;
    render();
  }
});

$('fileInput').addEventListener('change', (e) => { addFiles(e.target.files).catch(showError); e.target.value = ''; });
$('checkDate').addEventListener('change', () => refresh().catch(showError));
$('clearBtn').addEventListener('click', () => {
  state.files.clear();
  state.viewing = null;
  $('uploadNote').hidden = true;
  refresh();
});
$('demoBtn').addEventListener('click', async () => {
  const data = await (await fetch('/api/samples')).json();
  for (const f of data.files) state.files.set(f.name, f.content_b64);
  refresh().catch(showError);
});
$('askForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const question = $('question').value.trim();
  if (question) renderAnswer(await post('/api/ask', {question}).catch(showError));
});
$('presets').innerHTML = PRESET_QUESTIONS.map((q, i) => `<button type="button" data-q="${i}">${esc(q)}</button>`).join('');
$('presets').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-q]');
  if (!b) return;
  $('question').value = PRESET_QUESTIONS[+b.dataset.q];
  $('askForm').requestSubmit();
});

function showError(err) {
  $('answer').innerHTML = `<div class="issue">Ошибка: ${esc(err.message)}</div>`;
  throw err;
}
