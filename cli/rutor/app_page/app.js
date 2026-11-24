// The app page: eight steps from "can this machine draw" to "your cell waits for review".
//
// The page owns the Swedish. The app on this machine answers in short words ("mac", "taken")
// and the tables below turn each word into one sentence. Everything that comes from the app,
// the service, GitHub or a child process is put on the page as text, never as markup.

"use strict";

const OKANT = "Något gick fel. Försök igen.";

const ORD = {
  dator: {
    mac: "Det här är en Mac. Ritandet kräver ett NVIDIA-kort.",
    no_card: "Inget NVIDIA-kort hittades. Det krävs ett med 12 GB.",
    small_card: "Grafikkortet har för lite minne. Det krävs 12 GB.",
    disk: "Disken är för full. Gör plats och kom tillbaka.",
  },
  nyckel: {
    missing: "Ingen nyckel är sparad än.",
    refused: "Cesium tog inte emot nyckeln. Skapa en ny och försök igen.",
    twice: "Nyckeln ser dubbel eller avklippt ut. Klistra in den en gång.",
    no_access: "Nyckeln får inte läsa kartbilden. Kontrollera kontot hos Cesium.",
    unreachable: "Cesium svarade inte. Försök igen om en stund.",
    broken: "Nyckeln har ett mellanslag i sig. Klistra in den igen.",
  },
  inlogg: {
    expired: "Koden godkändes inte i tid. Hämta en ny.",
    network: "Nätet svarade inte. Försök igen.",
    terms: "Säg ja till villkoren först.",
  },
  jobb: {
    taken: "Någon annan hann ta rutan. Välj en annan.",
    refused: "Tjänsten sa nej. Försök igen om en stund.",
    machine: "Något saknas på datorn. Titta på stegen igen.",
    network: "Nätet svarade inte. Försök igen.",
    check: "Bilden klarade inte kontrollen. Den skickades inte in.",
    failed: "Något gick fel. Försök igen.",
  },
  svar: {
    busy: "En ruta ritas redan.",
    no_cell: "Välj en ruta först.",
    not_ready: "Något saknas i ett tidigare steg. Gå tillbaka och titta.",
    terms: "Säg ja till villkoren först.",
    none_free: "Inga rutor är lediga just nu. Titta in senare.",
    taken: "Rutan är inte ledig längre. Välj en annan.",
  },
  blad: {
    none_free: "Inga rutor är lediga just nu. Titta in senare.",
    network: "Tjänsten svarade inte. Försök igen om en stund.",
  },
};

const VERKTYG = {
  torch: "PyTorch med CUDA",
  bitsandbytes: "bitsandbytes",
  render: "Pillow och NumPy",
  node: "Node 22 eller nyare",
  google_renderer: "Kartritaren i mappen cli/google-render",
};

const NAMN = ["Klarar datorn det?", "Villkoren", "Logga in", "Cesium-konto", "Ritverktyget", "Välj ruta", "Rita", "Klart"];
const FASER = ["claim", "render", "redraw", "upload"];

// The two places the page sends a person to. They are links the person presses. The page
// itself loads nothing from them.
const GITHUB_KOD = "https://github.com/login/device";
const CESIUM_ION = "https://ion.cesium.com";
const KARTANS_VARD = "karta.opensverige.se";

const FRAGA_SEKUNDER = 2;
const FRAGA_SEKUNDER_UNDER_JOBB = 1;
const RUTNAT_SEKUNDER = 20;
const RUTNAT_BREDD = 620;
const TAPPAD_EFTER = 3;

let lage = null;
let visat = 0;
let forraFas = "";
let missar = 0;
let fragar = false;
let nastaFraga = 0;

let rutnat = null;
let rutnatTid = 0;
let rutnatHamtas = false;
let rutnatMatt = null;
let vald = null;

let nyckelFel = "";
let bytNyckel = false;
let stegFel = "";
let vantaVisad = false;
let rapportVisad = false;
let upptagen = false;

// ---- small helpers

function el(id) {
  return document.getElementById(id);
}

function text(id, value) {
  const node = el(id);
  const wanted = String(value);
  if (node.textContent !== wanted) node.textContent = wanted;
}

function visa(id, on) {
  el(id).hidden = !on;
}

function ord(grupp, kod) {
  const tabell = ORD[grupp];
  if (tabell && typeof kod === "string" && Object.prototype.hasOwnProperty.call(tabell, kod)) return tabell[kod];
  return OKANT;
}

function gar(s) {
  return FASER.includes(s.job.phase);
}

function duger(s) {
  const delar = ["machine", "tools", "terms", "login", "cesium", "price", "sheet", "job", "version"];
  return s !== null && typeof s === "object" && delar.every((del) => s[del] !== null && typeof s[del] === "object");
}

function rutnamn(cell) {
  const blad = lage.sheet;
  const namn = blad.id === cell.sheet && blad.name ? blad.name : cell.sheet;
  return namn + " " + cell.col + "_" + cell.row;
}

function klocka(iso) {
  const funnen = /^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})/.exec(typeof iso === "string" ? iso : "");
  return funnen ? funnen[1] + "." + funnen[2] : "";
}

function kartlank(adress) {
  // The app passes on the map's own address or nothing. Checked once more before it is a link.
  try {
    const url = new URL(adress);
    return url.protocol === "https:" && url.hostname === KARTANS_VARD ? url.href : "";
  } catch (fel) {
    return "";
  }
}

async function skicka(vag, kropp) {
  try {
    const svar = await fetch(vag, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(kropp),
      cache: "no-store",
    });
    let data = null;
    try {
      data = await svar.json();
    } catch (fel) {
      data = null;
    }
    return { ok: svar.ok, data: data !== null && typeof data === "object" ? data : {} };
  } catch (fel) {
    return { ok: false, data: {} };
  }
}

// ---- where the person is

function klara(s) {
  const klart = s.job.phase === "done";
  return [
    s.machine.ok === true,
    s.terms.accepted === true,
    Boolean(s.login.login),
    s.cesium.ok === true,
    s.tools.ready === true,
    Boolean(s.cell),
    klart,
    klart,
  ];
}

function framme(s) {
  if (s.job.phase === "done") return 8;
  if (gar(s) || s.job.phase === "failed" || s.job.phase === "stopped") return 7;
  const gjort = klara(s);
  for (let i = 0; i < 7; i += 1) {
    if (!gjort[i]) return i + 1;
  }
  return 7;
}

function ga(till) {
  const mal = Math.max(1, Math.min(Number(till) || 1, lage ? framme(lage) : 1));
  if (mal === visat) return;
  visat = mal;
  stegFel = "";
  rita();
  el("rubrik-" + visat).focus();
}

// ---- drawing the page from what the app says

function rita() {
  visa("tappad", missar >= TAPPAD_EFTER);
  if (!lage) return;
  const s = lage;
  const fas = s.job.phase;
  const langst = framme(s);

  if (visat === 0) visat = langst;
  if (gar(s)) visat = 7;
  else if (fas !== forraFas && fas === "done") visat = 8;
  else if (fas !== forraFas && (fas === "failed" || fas === "stopped")) visat = 7;
  if (visat > langst) visat = langst;
  if (fas !== forraFas) {
    rapportVisad = false;
    vantaVisad = false;
  }
  forraFas = fas;

  visa("gammal", s.version.current === false);

  const gjort = klara(s);
  for (const knapp of el("rad").querySelectorAll("button")) {
    const n = Number(knapp.dataset.steg);
    // A tick is for what lies behind the person, never for a step they have not come to.
    const klar = gjort[n - 1] && n < langst;
    knapp.classList.toggle("klar", klar);
    knapp.disabled = n > langst || (gar(s) && n !== 7);
    if (n === visat) knapp.setAttribute("aria-current", "step");
    else knapp.removeAttribute("aria-current");
    const prick = knapp.querySelector(".prick");
    const tecken = klar ? "✓" : String(n);
    if (prick.textContent !== tecken) prick.textContent = tecken;
  }
  text("mobilrad", "Steg " + visat + " av 8 · " + NAMN[visat - 1]);
  el("mobilmatare").style.width = (visat / 8) * 100 + "%";

  for (let n = 1; n <= 8; n += 1) visa("steg-" + n, n === visat);
  STEG[visat - 1](s);
}

function steg1(s) {
  const m = s.machine;
  visa("dator-tittar", false);
  visa("dator-rader", true);
  const kortFel = m.problem === "mac" || m.problem === "no_card" || m.problem === "small_card";
  const kort = el("dator-kort");
  text("dator-kort", m.card ? m.card + " · " + m.card_gb + " GB" + (kortFel ? "" : " ✓") : "Inget NVIDIA-kort");
  kort.classList.toggle("nej", kortFel);
  kort.classList.toggle("ok", !kortFel);
  const disk = el("dator-disk");
  const diskFel = m.problem === "disk";
  text("dator-disk", m.free_gb + " GB · " + m.needed_gb + " behövs" + (diskFel ? "" : " ✓"));
  disk.classList.toggle("nej", diskFel);
  disk.classList.toggle("ok", !diskFel);
  visa("dator-minnesrad", typeof m.ram_gb === "number");
  if (typeof m.ram_gb === "number") text("dator-minne", m.ram_gb + " GB");

  const besked = el("dator-besked");
  besked.hidden = false;
  besked.classList.toggle("nejruta", !m.ok);
  if (m.ok) {
    text("rubrik-1", "Klarar din dator det?");
    text("dator-besked", "Din dator klarar att rita.");
  } else {
    text("rubrik-1", "Den här datorn kan inte rita");
    text("dator-besked", ord("dator", m.problem));
  }
  visa("dator-ingress", m.ok);
  visa("dator-trost", m.problem === "mac");
  visa("dator-vidare", m.ok);
}

function steg2(s) {
  const lista = el("villkor");
  const rader = Array.isArray(s.terms.lines) ? s.terms.lines : [];
  if (lista.children.length !== rader.length) {
    lista.replaceChildren();
    for (const rad of rader) {
      // Word for word, numbers included, as the terms are written.
      const punkt = document.createElement("li");
      punkt.textContent = String(rad);
      lista.append(punkt);
    }
  }
  const kryss = el("villkor-ja");
  if (s.terms.accepted) {
    kryss.checked = true;
    kryss.disabled = true;
  }
  el("villkor-vidare").disabled = !kryss.checked || upptagen;
  visa("villkor-fel", Boolean(stegFel));
  text("villkor-fel", stegFel);
}

function steg3(s) {
  const l = s.login;
  const inne = Boolean(l.login) && !l.waiting;
  visa("inlogg-ingress", !inne);
  visa("inlogg-kod", l.waiting && Boolean(l.code));
  text("inlogg-kod", l.code || "");
  visa("inlogg-vantar", l.waiting === true);
  text("inlogg-vantar", l.code ? "Väntar på att du godkänner koden" : "Hämtar en kod från GitHub");
  visa("inlogg-klar", inne);
  if (inne) text("inlogg-klar", "Du är inloggad som " + l.login + ".");
  const fel = stegFel || (!l.waiting && !inne && l.problem ? ord("inlogg", l.problem) : "");
  visa("inlogg-fel", Boolean(fel));
  text("inlogg-fel", fel);

  visa("inlogg-start", !l.waiting && !inne);
  el("inlogg-start").disabled = upptagen;
  const oppna = el("inlogg-oppna");
  // Only GitHub's own device page is ever a link here, whatever the app said.
  const lankbar = l.waiting && Boolean(l.code) && l.url === GITHUB_KOD;
  if (lankbar) oppna.href = GITHUB_KOD;
  else oppna.removeAttribute("href");
  oppna.hidden = !lankbar;
  visa("inlogg-kopiera", l.waiting && Boolean(l.code));
  visa("inlogg-vidare", inne);
}

function steg4(s) {
  const c = s.cesium;
  const behovs = s.sheet.kind === "google" || !s.sheet.id;
  const klar = behovs && c.ok === true && !bytNyckel;
  const fraga = behovs && !klar;
  el("nyckel-lank").href = CESIUM_ION;
  visa("nyckel-ingress", behovs);
  visa("nyckel-tre", fraga);
  visa("nyckel-falt", fraga);
  visa("nyckel-prova", fraga);
  el("nyckel-prova").disabled = upptagen;
  visa("nyckel-klar", klar);
  visa("nyckel-behovs-inte", !behovs);
  visa("nyckel-vidare", c.ok === true && !fraga);
  visa("nyckel-byt", klar);
  let fel = "";
  if (fraga) {
    if (stegFel) fel = stegFel;
    else if (nyckelFel) fel = ord("nyckel", nyckelFel);
    else if (c.set && !c.ok && !bytNyckel) fel = ord("nyckel", c.problem);
  }
  visa("nyckel-fel", Boolean(fel));
  text("nyckel-fel", fel);
}

function steg5(s) {
  const t = s.tools;
  const saknas = Array.isArray(t.missing) ? t.missing : [];
  text("verktyg-ingress", t.ready ? "Allt som behövs finns på datorn." : "Det här saknas på datorn.");
  const lista = el("verktyg-saknas");
  const nyckel = saknas.join(",");
  if (lista.dataset.visar !== nyckel) {
    lista.dataset.visar = nyckel;
    lista.replaceChildren();
    for (const namn of saknas) {
      const punkt = document.createElement("li");
      const kant = typeof namn === "string" && Object.prototype.hasOwnProperty.call(VERKTYG, namn);
      punkt.textContent = kant ? VERKTYG[namn] : String(namn);
      lista.append(punkt);
    }
  }
  visa("verktyg-saknas", saknas.length > 0);
  visa("verktyg-hjalp", !t.ready);
  visa("verktyg-modell", t.ready === true && t.model_cached === false);
  visa("verktyg-vidare", t.ready === true);
}

function steg6(s) {
  const blad = s.sheet;
  const finns = Boolean(blad.id) && !blad.problem;
  visa("ruta-ingress", finns);
  visa("ruta-karta", finns && rutnat !== null);
  visa("ruta-forklaring", finns && rutnat !== null);
  const fel = stegFel || (blad.problem ? ord("blad", blad.problem) : "");
  visa("ruta-fel", Boolean(fel));
  text("ruta-fel", fel);

  const mal = malruta(s);
  visa("ruta-vald", finns && mal !== null);
  if (mal !== null) text("ruta-namn", rutnamn(mal));
  visa("ruta-ta", finns && mal !== null);
  visa("ruta-nasta", finns);
  el("ruta-ta").disabled = upptagen;
  el("ruta-nasta").disabled = upptagen;

  if (finns) {
    const gammalt = Date.now() - rutnatTid > RUTNAT_SEKUNDER * 1000;
    if ((rutnat === null || gammalt || rutnat.blad !== blad.id) && !rutnatHamtas) hamtaRutnat();
    else ritaRutnat();
  }
}

function steg7(s) {
  const j = s.job;
  const pagar = gar(s);
  const slut = j.phase === "failed" || j.phase === "stopped";
  const fraga = !pagar && !slut;
  visa("rita-fraga", fraga);
  visa("rita-gar", pagar);
  visa("rita-slut", slut);

  const dyrt = s.price.decision === "WAIT";
  visa("rita-start", fraga);
  el("rita-start").disabled = upptagen || !s.cell;
  visa("rita-vanta", fraga && dyrt);
  visa("rita-rad", fraga && dyrt);
  visa("rita-avbryt", pagar);
  el("rita-avbryt").disabled = upptagen;
  visa("slut-igen", slut && j.problem !== "taken");
  el("slut-igen").disabled = upptagen;
  visa("slut-annan", slut);
  visa("slut-kopiera", j.phase === "failed");

  if (fraga) {
    text("rubrik-7", "Får datorn rita i ungefär 20 minuter?");
    text("rita-ruta", s.cell ? rutnamn(s.cell) : "");
    const pris = el("rita-pris");
    text("rita-pris", dyrt ? "högt just nu" : s.price.decision === "RUN" ? "lågt" : "okänt");
    pris.classList.toggle("ok", s.price.decision === "RUN");
    pris.classList.toggle("nej", dyrt);
    visa("rita-vanta-rad", vantaVisad && dyrt);
    if (vantaVisad) {
      const tid = klocka(s.price.after);
      text("rita-vanta-rad", tid ? "Priset väntas vara lågt efter kl. " + tid + ". Kom tillbaka då." : "Kom tillbaka i natt. Då är elen oftast billigare.");
    }
    visa("rita-fel", Boolean(stegFel));
    text("rita-fel", stegFel);
    return;
  }

  if (pagar) {
    text("rubrik-7", "Datorn ritar din ruta");
    const nu = FASER.indexOf(j.phase);
    for (const rad of el("faser").children) {
      const plats = FASER.indexOf(rad.dataset.fas);
      rad.classList.toggle("gjord", plats < nu);
      rad.classList.toggle("nu", plats === nu);
    }
    text("fas-rita-tid", s.tools.model_cached === false ? "20 GB hämtas först" : "ungefär 2 min");
    text("rita-detalj", j.detail || "");
    return;
  }

  const avbrutet = j.phase === "stopped";
  text("rubrik-7", avbrutet ? "Ritandet avbröts" : "Rutan blev inte ritad");
  el("slut-besked").classList.toggle("nejruta", !avbrutet);
  text("slut-besked", stegFel || (avbrutet ? "Ritandet stannade. Kortet är ledigt igen." : ord("jobb", j.problem)));
  text("slut-las", j.held ? "Rutan är fortfarande låst. Låset släpps av sig självt inom två dygn." : "Rutan är inte låst. Inget skickades in.");
  visa("slut-rapport-rad", rapportVisad);
  visa("slut-rapport", rapportVisad && Boolean(el("slut-rapport").value));
}

function steg8(s) {
  const j = s.job;
  text("klart-ruta", rutnamn({ sheet: j.sheet, col: j.col, row: j.row }) + " · väntar på granskning");
  const lank = el("klart-karta");
  const adress = kartlank(j.url);
  if (adress) lank.href = adress;
  else lank.removeAttribute("href");
  lank.hidden = !adress;
}

const STEG = [steg1, steg2, steg3, steg4, steg5, steg6, steg7, steg8];

// ---- step 6: the cells of the sheet

function malruta(s) {
  if (vald !== null) return { sheet: s.sheet.id, col: vald.col, row: vald.row };
  // A cell that has been through a job is not offered again: it waits for review, or failed.
  if (s.cell && s.cell.sheet === s.sheet.id && s.job.phase === "idle") return s.cell;
  return null;
}

async function hamtaRutnat() {
  rutnatHamtas = true;
  try {
    const svar = await fetch("api/grid", { cache: "no-store" });
    const data = svar.ok ? await svar.json() : null;
    if (data && data.sheet && Array.isArray(data.cells)) {
      const rutor = new Map();
      for (const rad of data.cells) {
        if (Array.isArray(rad) && Number.isInteger(rad[0]) && Number.isInteger(rad[1]) && typeof rad[2] === "string") {
          rutor.set(rad[0] + "_" + rad[1], rad[2]);
        }
      }
      rutnat = { blad: String(data.sheet.id), cols: Number(data.sheet.cols) || 0, rows: Number(data.sheet.rows) || 0, rutor: rutor };
      if (vald !== null && rutor.get(vald.col + "_" + vald.row) !== "fri") vald = null;
    }
  } catch (fel) {
    // The state says so when the service cannot be reached. The old grid stays meanwhile.
  }
  rutnatTid = Date.now();
  rutnatHamtas = false;
  if (lage && visat === 6) rita();
}

function ritaRutnat() {
  if (rutnat === null || !lage) return;
  // Only the part of the sheet that is offered is drawn, so each cell gets room.
  let minC = Infinity;
  let maxC = -Infinity;
  let minR = Infinity;
  let maxR = -Infinity;
  for (const [plats, tillstand] of rutnat.rutor) {
    if (tillstand === "ute") continue;
    const delar = plats.split("_");
    const c = Number(delar[0]);
    const r = Number(delar[1]);
    minC = Math.min(minC, c);
    maxC = Math.max(maxC, c);
    minR = Math.min(minR, r);
    maxR = Math.max(maxR, r);
  }
  if (minC === Infinity) return;
  minC = Math.max(0, minC - 1);
  minR = Math.max(0, minR - 1);
  maxC = Math.min(rutnat.cols - 1, maxC + 1);
  maxR = Math.min(rutnat.rows - 1, maxR + 1);
  const kol = maxC - minC + 1;
  const rad = maxR - minR + 1;
  const sida = Math.max(3, Math.min(28, Math.floor(RUTNAT_BREDD / kol)));
  const skala = window.devicePixelRatio || 1;

  const duk = el("rutnat");
  const bredd = Math.round(kol * sida * skala);
  const hojd = Math.round(rad * sida * skala);
  if (duk.width !== bredd) duk.width = bredd;
  if (duk.height !== hojd) duk.height = hojd;
  // The frame is as wide as the cells drawn in it, plus its own padding on both sides.
  el("ruta-karta").style.maxWidth = kol * sida + 20 + "px";
  rutnatMatt = { minC: minC, minR: minR, kol: kol, rad: rad };

  const farg = getComputedStyle(document.documentElement);
  const farger = {
    live: farg.getPropertyValue("--moss-soft"),
    godkand: farg.getPropertyValue("--ochre"),
    vantar: farg.getPropertyValue("--ochre"),
    last: farg.getPropertyValue("--rust"),
    fri: farg.getPropertyValue("--yta"),
  };
  const penna = duk.getContext("2d");
  penna.setTransform(skala, 0, 0, skala, 0, 0);
  penna.clearRect(0, 0, kol * sida, rad * sida);
  const glipa = sida >= 6 ? 1 : 0;
  for (const [plats, tillstand] of rutnat.rutor) {
    if (!Object.prototype.hasOwnProperty.call(farger, tillstand)) continue;
    const delar = plats.split("_");
    const x = (Number(delar[0]) - minC) * sida;
    const y = (Number(delar[1]) - minR) * sida;
    penna.fillStyle = farger[tillstand];
    penna.fillRect(x, y, sida - glipa, sida - glipa);
    if (tillstand === "fri" && sida >= 6) {
      penna.strokeStyle = farg.getPropertyValue("--line-strong");
      penna.lineWidth = 1;
      penna.strokeRect(x + 0.5, y + 0.5, sida - glipa - 1, sida - glipa - 1);
    }
  }
  const mal = malruta(lage);
  if (mal !== null) {
    const x = (mal.col - minC) * sida;
    const y = (mal.row - minR) * sida;
    penna.fillStyle = farg.getPropertyValue("--yta");
    penna.fillRect(x, y, sida - glipa, sida - glipa);
    penna.strokeStyle = farg.getPropertyValue("--ink");
    penna.lineWidth = Math.max(2, sida / 8);
    penna.strokeRect(x - 1, y - 1, sida - glipa + 2, sida - glipa + 2);
    if (sida < 12) {
      // On a large sheet a cell is a few pixels wide. A ring shows where the chosen one is.
      penna.lineWidth = 1.5;
      penna.beginPath();
      penna.arc(x + sida / 2, y + sida / 2, 10, 0, 2 * Math.PI);
      penna.stroke();
    }
  }
}

function klickPaRutnat(handelse) {
  if (rutnat === null || rutnatMatt === null || upptagen) return;
  const duk = el("rutnat");
  const kant = duk.getBoundingClientRect();
  if (kant.width === 0 || kant.height === 0) return;
  const col = rutnatMatt.minC + Math.floor(((handelse.clientX - kant.left) / kant.width) * rutnatMatt.kol);
  const row = rutnatMatt.minR + Math.floor(((handelse.clientY - kant.top) / kant.height) * rutnatMatt.rad);
  if (rutnat.rutor.get(col + "_" + row) === "fri") {
    vald = { col: col, row: row };
    stegFel = "";
  } else {
    stegFel = "Den rutan är inte ledig.";
  }
  rita();
}

// ---- asking the app, and telling it what the person pressed

async function fraga() {
  if (fragar) return;
  fragar = true;
  window.clearTimeout(nastaFraga);
  try {
    const svar = await fetch("api/state", { cache: "no-store" });
    const data = svar.ok ? await svar.json() : null;
    if (!duger(data)) throw new Error("state");
    lage = data;
    missar = 0;
  } catch (fel) {
    missar += 1;
  }
  fragar = false;
  rita();
  const paus = lage && gar(lage) ? FRAGA_SEKUNDER_UNDER_JOBB : FRAGA_SEKUNDER;
  nastaFraga = window.setTimeout(fraga, paus * 1000);
}

async function gor(vag, kropp, grupp) {
  // One press at a time: the buttons are shut until the app has answered.
  if (upptagen) return null;
  upptagen = true;
  stegFel = "";
  rita();
  const svar = await skicka(vag, kropp);
  upptagen = false;
  if (!svar.ok) stegFel = ord(grupp, svar.data.code);
  await fraga();
  return svar.ok ? svar.data : null;
}

async function kopiera(textAttKopiera) {
  try {
    await navigator.clipboard.writeText(textAttKopiera);
    return true;
  } catch (fel) {
    return false;
  }
}

function koppla() {
  document.addEventListener("click", (handelse) => {
    const mal = handelse.target instanceof Element ? handelse.target.closest("button") : null;
    if (!mal || !lage) return;
    if (mal.dataset.steg !== undefined) ga(mal.dataset.steg);
    else if (mal.dataset.till !== undefined) {
      if (mal.id === "klart-en-till" || mal.id === "slut-annan") {
        vald = null;
        rutnatTid = 0;
      }
      ga(mal.dataset.till);
    }
  });

  el("villkor-ja").addEventListener("change", rita);
  el("villkor-vidare").addEventListener("click", async () => {
    if (lage.terms.accepted || (await gor("api/terms", { accept: true }, "svar")) !== null) ga(3);
  });

  el("inlogg-start").addEventListener("click", () => gor("api/login", {}, "inlogg"));
  el("inlogg-kopiera").addEventListener("click", async () => {
    const gick = await kopiera(lage.login.code || "");
    text("inlogg-kopiera", gick ? "Kopierad" : "Markera koden och kopiera");
    window.setTimeout(() => text("inlogg-kopiera", "Kopiera koden"), 2500);
  });

  el("nyckel-prova").addEventListener("click", async () => {
    const falt = el("nyckel");
    const nyckel = falt.value.trim();
    if (!nyckel) {
      nyckelFel = "";
      stegFel = "Klistra in nyckeln först.";
      rita();
      return;
    }
    // The key is sent once and then leaves the page, whatever the answer is.
    falt.value = "";
    nyckelFel = "";
    const svar = await gor("api/cesium", { token: nyckel }, "svar");
    if (svar !== null && svar.ok === true) bytNyckel = false;
    else if (svar !== null) nyckelFel = typeof svar.problem === "string" ? svar.problem : "unreachable";
    rita();
  });
  el("nyckel").addEventListener("keydown", (handelse) => {
    if (handelse.key === "Enter") el("nyckel-prova").click();
  });
  el("nyckel-byt").addEventListener("click", () => {
    bytNyckel = true;
    nyckelFel = "";
    rita();
    el("nyckel").focus();
  });

  el("rutnat").addEventListener("click", klickPaRutnat);
  el("ruta-ta").addEventListener("click", async () => {
    const mal = malruta(lage);
    if (mal === null) return;
    if ((await gor("api/cell", { sheet: mal.sheet, col: mal.col, row: mal.row }, "svar")) !== null) {
      vald = null;
      ga(7);
    } else {
      rutnatTid = 0;
    }
  });
  el("ruta-nasta").addEventListener("click", async () => {
    if ((await gor("api/cell", { next: true }, "svar")) !== null) {
      vald = null;
      ga(7);
    }
  });

  el("rita-start").addEventListener("click", () => gor("api/build", {}, "svar"));
  el("slut-igen").addEventListener("click", () => gor("api/build", {}, "svar"));
  el("rita-avbryt").addEventListener("click", () => gor("api/stop", {}, "svar"));
  el("rita-vanta").addEventListener("click", () => {
    vantaVisad = true;
    rita();
  });
  el("slut-kopiera").addEventListener("click", async () => {
    let rapport = "";
    try {
      const svar = await fetch("api/report", { cache: "no-store" });
      rapport = svar.ok ? await svar.text() : "";
    } catch (fel) {
      rapport = "";
    }
    const gick = rapport !== "" && (await kopiera(rapport));
    // When the browser will not copy, the report is shown so that the person can.
    el("slut-rapport").value = gick ? "" : rapport;
    rapportVisad = true;
    text("slut-kopiera", gick ? "Kopierad" : "Kopiera felrapport");
    window.setTimeout(() => text("slut-kopiera", "Kopiera felrapport"), 2500);
    rita();
  });
}

koppla();
fraga();
