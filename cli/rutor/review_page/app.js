// The review page. Everything the service says is data from others: it is put on the page as
// text (textContent), never as HTML, and addresses are built from numbers and a checked name.
'use strict';

const $ = (id) => document.getElementById(id);
const SHEET = /^[a-z0-9][a-z0-9-]{0,63}$/;

const state = {
  waiting: [],
  approved: [],
  /** The cell on screen: an entry of one of the lists, or null. */
  chosen: null,
  /** Counts up for every cell shown, so a slow answer for an earlier cell is dropped. */
  shown: 0,
  /** The value of `shown` whose upload is on screen. Approve works only when the two agree. */
  seen: -1,
  busy: false,
};

// A cell can be approved only while its own picture is on screen. Between two cells, and when
// the picture cannot be shown, the button is off: a late click must not approve the next cell.
function buttons() {
  $('approve').disabled = state.busy || state.seen !== state.shown;
  $('reject').disabled = state.busy;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function say(text) {
  const box = $('message');
  box.textContent = text;
  box.hidden = text === '';
}

async function ask(path, body) {
  const options = body === undefined
    ? { headers: { Accept: 'application/json' } }
    : { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) };
  let answer;
  try {
    answer = await fetch(path, options);
  } catch {
    throw new Error('Sidan svarar inte. Körs rutor review fortfarande?');
  }
  const parsed = await answer.json().catch(() => null);
  if (!answer.ok) {
    const error = new Error(parsed && typeof parsed.error === 'string' ? parsed.error : `Fel ${answer.status}`);
    error.code = parsed && typeof parsed.code === 'string' ? parsed.code : '';
    throw error;
  }
  return parsed;
}

const same = (a, b) => Boolean(a && b && a.sheet === b.sheet && a.col === b.col && a.row === b.row);
const label = (cell) => `${cell.sheet} ${cell.col}_${cell.row}`;
const place = (cell) => `${encodeURIComponent(cell.sheet)}/${cell.col}_${cell.row}`;

function when(iso) {
  const time = new Date(iso);
  if (!iso || Number.isNaN(time.getTime())) return '';
  const clock = time.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(new Date()) - day(time)) / 86400000);
  if (days === 0) return `i dag ${clock}`;
  if (days === 1) return `i går ${clock}`;
  return time.toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' });
}

function megabytes(bytes) {
  return bytes > 0 ? `${(bytes / 1e6).toLocaleString('sv-SE', { maximumFractionDigits: 1 })} MB` : '';
}

// ---- the queue

function drawList(target, cells) {
  target.replaceChildren(
    ...cells.map((cell) => {
      const button = el('button', 'queue-item');
      button.type = 'button';
      if (same(cell, state.chosen)) button.setAttribute('aria-current', 'true');
      button.append(el('span', 'name', label(cell)), el('span', 'who', [cell.login || 'okänd', when(cell.uploaded_at)].filter(Boolean).join(' · ')));
      button.addEventListener('click', () => {
        say('');
        choose(cell);
      });
      return button;
    }),
  );
}

function drawQueue() {
  const n = state.waiting.length;
  $('count').textContent = n === 1 ? '1 ruta väntar' : `${n} rutor väntar`;
  drawList($('waiting'), state.waiting);
  drawList($('approved'), state.approved);
  $('none-waiting').hidden = n > 0;
  $('approved-head').hidden = state.approved.length === 0;
}

function fromHash() {
  const found = /^#([a-z0-9][a-z0-9-]{0,63})\/(\d{1,4})_(\d{1,4})$/.exec(location.hash);
  return found ? { sheet: found[1], col: Number(found[2]), row: Number(found[3]) } : null;
}

async function loadQueue(wanted) {
  let lists;
  try {
    lists = await ask('api/queue');
  } catch (error) {
    say(error.message);
    return;
  }
  const read = (list) => (Array.isArray(list) ? list : []).filter(
    (c) => c && typeof c.sheet === 'string' && SHEET.test(c.sheet) && Number.isInteger(c.col) && Number.isInteger(c.row),
  );
  state.waiting = read(lists && lists.waiting);
  state.approved = read(lists && lists.approved);
  const all = [...state.waiting, ...state.approved];
  const next = all.find((c) => same(c, wanted)) || all.find((c) => same(c, state.chosen)) || state.waiting[0] || state.approved[0] || null;
  choose(next);
}

// ---- one cell

function tiles(stage, cell, hasMap) {
  if (!hasMap) return;
  for (let dr = -1; dr <= 1; dr += 1) {
    for (let dc = -1; dc <= 1; dc += 1) {
      const col = cell.col + dc;
      const row = cell.row + dr;
      if (col < 0 || row < 0) continue;
      const img = el('img', `tile c${dc + 1} r${dr + 1}`);
      img.alt = '';
      img.decoding = 'async';
      // Outside the map there is no tile: the ground colour shows instead.
      img.addEventListener('error', () => img.remove());
      img.src = `img/map/${encodeURIComponent(cell.sheet)}/${col}_${row}.webp`;
      stage.append(img);
    }
  }
}

function fact(list, name, value) {
  if (!value) return;
  const row = el('div');
  row.append(el('dt', '', name), el('dd', '', value));
  list.append(row);
}

async function choose(cell) {
  state.chosen = cell;
  state.shown += 1;
  const turn = state.shown;
  buttons();
  drawQueue();
  const main = $('cell');
  const facts = $('facts');
  if (!cell) {
    main.hidden = true;
    facts.hidden = true;
    history.replaceState(null, '', location.pathname);
    return;
  }
  history.replaceState(null, '', `#${cell.sheet}/${cell.col}_${cell.row}`);
  const isApproved = state.approved.some((c) => same(c, cell));
  $('approve').hidden = isApproved;
  $('reject').textContent = isApproved ? 'Ta tillbaka och avvisa' : 'Avvisa';
  $('comment').value = '';
  $('comment').classList.remove('missing');
  $('view-new').replaceChildren();
  $('view-now').replaceChildren();
  $('fact-list').replaceChildren();
  $('told-list').replaceChildren();
  $('view-note').textContent = '';
  $('full').href = `img/cell/${place(cell)}.png`;
  main.hidden = false;
  facts.hidden = false;

  let info;
  try {
    info = await ask(`api/cell/${place(cell)}`);
  } catch (error) {
    if (turn === state.shown) say(error.message);
    return;
  }
  if (turn !== state.shown) return;
  const hasMap = info.map === true;
  const box = Array.isArray(info.box) && info.box.length === 3 && info.box.every((n) => typeof n === 'number' && n >= 0 && n <= 1) ? info.box : null;

  const fresh = $('view-new');
  const now = $('view-now');
  tiles(fresh, cell, hasMap);
  tiles(now, cell, hasMap);
  const upload = el('img', 'upload');
  upload.alt = `Den nya rutan ${cell.col}_${cell.row}`;
  if (box) {
    const [start, size, edge] = box;
    upload.style.left = upload.style.top = `${start * 100}%`;
    upload.style.width = upload.style.height = `${size * 100}%`;
    upload.style.setProperty('--edge', `${edge * 100}%`);
  } else {
    // A size the page does not know: the upload is shown alone, over the whole square.
    upload.classList.add('whole');
    upload.style.left = upload.style.top = '0';
    upload.style.width = upload.style.height = '100%';
  }
  upload.addEventListener('load', () => {
    if (turn !== state.shown) return;
    state.seen = turn;
    buttons();
  });
  upload.addEventListener('error', async () => {
    if (turn !== state.shown) return;
    upload.remove();
    // The server says why, as JSON, at the picture's own address.
    const why = await ask(`img/cell/${place(cell)}.png`).then(() => null, (error) => error);
    if (turn !== state.shown) return;
    say(why && why.code === 'not_plain'
      ? `Filen är inte en vanlig bild: den kan vara genomskinlig, animerad eller ha en färgprofil. Kartan kunde få en annan bild än den du ser, så den visas inte. Avvisa rutan. (${why.message})`
      : `Rutans bild visas inte, så den går inte att godkänna. ${why ? why.message : ''}`.trim());
  });
  upload.src = `img/cell/${place(cell)}.png`;
  fresh.append(upload, el('div', 'outline'));
  now.append(el('div', 'outline'));

  $('view-note').textContent = !box
    ? 'Rutans storlek saknas i manifestet. Den visas ensam, utan grannar.'
    : hasMap
      ? 'Den vita ramen är rutan. Runt den syns grannarna, så att skarvar märks. Kanten tonas mot grannarna, ungefär som på kartan.'
      : 'Staden har ingen karta än. Rutan visas ensam.';

  // What the service has itself checked or recorded.
  const known = $('fact-list');
  fact(known, 'Byggare', cell.login || 'okänd');
  fact(known, 'Uppladdad', when(cell.uploaded_at));
  fact(known, 'Storlek', [info.side ? `${info.side} px` : '', megabytes(cell.bytes)].filter(Boolean).join(' · '));
  fact(known, 'Mall', info.pipeline);
  // What the builder's own machine wrote into the manifest. Nobody has checked it.
  const told = $('told-list');
  fact(told, 'Kvalitetskontroll', info.check || 'saknas');
  fact(told, 'Källa', info.provider);
  fact(told, 'Underlag', info.version);
  fact(told, 'Grafikkort', info.gpu);
  fact(told, 'Omritad', when(info.rendered_at));
}

// ---- the two buttons

async function decide(verb) {
  const cell = state.chosen;
  if (!cell || state.busy) return;
  if (verb === 'approve' && state.seen !== state.shown) return;
  const body = { sheet: cell.sheet, col: cell.col, row: cell.row };
  if (verb === 'reject') {
    const input = $('comment');
    body.comment = input.value.trim();
    if (!body.comment) {
      input.classList.add('missing');
      input.focus();
      say('Skriv ett skäl först.');
      return;
    }
  }
  state.busy = true;
  buttons();
  say('');
  // After a yes or a no the next waiting cell comes up. After an error this one stays, and
  // the queue is read again so that the page shows what the service now has.
  let next = state.waiting.find((c) => !same(c, cell)) || null;
  try {
    await ask(`api/${verb}`, body);
  } catch (error) {
    say(`${label(cell)}: ${error.message}`);
    next = cell;
  }
  // Still busy while the queue is read: the buttons come back with the next cell's picture.
  await loadQueue(next);
  state.busy = false;
  buttons();
}

$('approve').addEventListener('click', () => decide('approve'));
$('reject').addEventListener('click', () => decide('reject'));
$('comment').addEventListener('input', () => $('comment').classList.remove('missing'));
$('refresh').addEventListener('click', () => {
  say('');
  loadQueue(state.chosen);
});
$('show-frame').addEventListener('change', (event) => $('views').classList.toggle('bare', !event.target.checked));
$('zoom').addEventListener('click', () => {
  const zoomed = $('views').classList.toggle('zoomed');
  $('zoom').setAttribute('aria-pressed', String(zoomed));
  $('zoom').textContent = zoomed ? 'Visa grannarna' : 'Zooma in på rutan';
});

loadQueue(fromHash());
