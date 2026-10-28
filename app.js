const estadoEl = document.querySelector("#estado");
const munEl = document.querySelector("#mun");
const praiaEl = document.querySelector("#praia");
const statusEl = document.querySelector("#status");
const outEl = document.querySelector("#out");
const kickerEl = document.querySelector("#kicker");

const params = new URLSearchParams(location.search);
let forecast = null;
let ticket = 0;
let munTicket = 0;
let praiaTicket = 0;
let selectedDate = params.get("dia") || "";

function joinHours(hours) {
  const labels = hours.map((hour) => hour + "h");
  if (labels.length < 2) return labels.join("");
  return labels.slice(0, -1).join(", ") + " e " + labels[labels.length - 1];
}

function tone(score) {
  if (score >= 75) return "good";
  if (score >= 55) return "mid";
  return "low";
}

function setStatus(text, kind) {
  statusEl.textContent = text || "";
  if (kind) statusEl.dataset.kind = kind;
  else delete statusEl.dataset.kind;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
  }
  for (const child of children || []) {
    if (child) node.append(child);
  }
  return node;
}

function remember() {
  const dia = selectedDate || "";
  const estado = estadoEl.value;
  const next = new URLSearchParams();
  if (estado) next.set("estado", estado);
  if (munEl.value) next.set("municipio", munEl.value);
  if (praiaEl.value) next.set("praia", praiaEl.value);
  if (dia) next.set("dia", dia);
  history.replaceState(null, "", "?" + next.toString());
  if (!estado) return;
  localStorage.setItem("pesca-estado", estado);
  localStorage.setItem("pesca-mun-" + estado, munEl.value);
  localStorage.setItem("pesca-praia-" + estado, praiaEl.value);
}

async function getJSON(url) {
  const res = await fetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Não consegui ler o SurfGuru.");
  return data;
}

function fillSelect(select, items, preferred) {
  select.replaceChildren();
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.slug;
    opt.textContent = item.nome;
    select.append(opt);
  }
  const slugs = items.map((item) => item.slug);
  if (preferred && slugs.includes(preferred)) select.value = preferred;
}

async function loadMunicipios(preferred) {
  const mine = ++munTicket;
  munEl.disabled = true;
  praiaEl.disabled = true;
  const estado = estadoEl.value;
  if (!estado) return;
  const items = await getJSON("/api/municipios?estado=" + encodeURIComponent(estado));
  if (mine !== munTicket) return;
  fillSelect(munEl, items, preferred);
  munEl.disabled = false;
}

async function loadPraias(preferred) {
  const mine = ++praiaTicket;
  praiaEl.disabled = true;
  const estado = estadoEl.value;
  const mun = munEl.value;
  if (!estado || !mun) return;
  const items = await getJSON("/api/praias?estado=" + encodeURIComponent(estado) + "&municipio=" + encodeURIComponent(mun));
  if (mine !== praiaTicket) return;
  if (!items.length) {
    praiaEl.replaceChildren();
    setStatus("Nenhuma praia cadastrada nesta cidade.", "error");
    outEl.replaceChildren();
    return;
  }
  fillSelect(praiaEl, items, preferred);
  praiaEl.disabled = false;
}

async function loadForecast(fresh) {
  const mine = ++ticket;
  const estado = estadoEl.value;
  const mun = munEl.value;
  const praia = praiaEl.value;
  if (!estado || !mun || !praia) return;
  setStatus("Lendo vento, maré e lua no SurfGuru…");
  outEl.replaceChildren();
  try {
    const url = "/api/previsao?estado=" + encodeURIComponent(estado) + "&municipio=" + encodeURIComponent(mun) + "&praia=" + encodeURIComponent(praia) + (fresh ? "&fresh=1" : "");
    const data = await getJSON(url);
    if (mine !== ticket) return;
    forecast = data;
    const day = data.days.find((item) => item.date === selectedDate) || data.days.find((item) => item.date === data.today) || data.days[0];
    setStatus("");
    render(day.date);
    remember();
  } catch (err) {
    if (mine !== ticket) return;
    forecast = null;
    setStatus(err.message, "error");
  }
}

function render(date) {
  const data = forecast;
  const day = data.days.find((item) => item.date === date) || data.days[0];
  selectedDate = day.date;
  const days = el("div", { class: "days", role: "listbox", "aria-label": "Dias da previsão" });
  for (const item of data.days) {
    const pressed = item.date === day.date;
    const button = el("button", {
      type: "button",
      class: "day",
      role: "option",
      "aria-pressed": pressed ? "true" : "false",
      "data-date": item.date,
    }, [
      el("small", { text: item.weekdayShort + " " + item.day }),
      el("b", { text: String(item.score) }),
    ]);
    button.addEventListener("click", () => {
      render(item.date);
      remember();
    });
    days.append(button);
  }

  const best = joinHours(day.bestHours);
  const hero = el("section", { class: "hero" }, [
    el("p", { class: "hero-score " + tone(day.score), text: String(day.score) }),
    el("div", { class: "hero-copy" }, [
      el("p", { class: "verdict", text: day.verdict.charAt(0).toUpperCase() + day.verdict.slice(1) }),
      el("p", { class: "window", text: "Melhor sequência " + best + ". Média de 5h às 18h: " + day.daylightMean }),
    ]),
  ]);

  const jump = el("button", { type: "button", class: "ghost", text: "Ir para a melhor hora" });
  jump.addEventListener("click", () => {
    document.getElementById("h-" + day.bestHours[0])?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  const refresh = el("button", { type: "button", class: "ghost", text: "Atualizar" });
  refresh.addEventListener("click", () => loadForecast(true));
  const source = el("a", { class: "ghost", href: data.sourceUrl, target: "_blank", rel: "noopener", text: "Ver no SurfGuru" });
  const actions = el("div", { class: "actions" }, [jump, refresh, source]);

  const prose = el("div", { class: "prose" });
  for (const paragraph of day.paragraphs) prose.append(el("p", { text: paragraph }));

  const bars = el("section", { class: "bars" }, [
    el("h2", { text: "Na melhor sequência" }),
  ]);
  for (const part of day.breakdown) {
    const pct = Math.max(0, Math.min(100, (part.score / part.max) * 100));
    const fill = el("span", { class: "fill" });
    fill.style.width = pct + "%";
    bars.append(el("div", { class: "bar-row" }, [
      el("span", { text: part.label }),
      el("div", { class: "track" }, [fill]),
      el("b", { text: Math.round(part.score) + "/" + part.max }),
    ]));
  }

  const hours = el("section", { class: "hours" }, [
    el("h2", { text: "Hora a hora · " + (data.clockLabel || "horário de Brasília") }),
  ]);
  for (const hour of day.hours) {
    const label = String(hour.hour).padStart(2, "0") + "h";
    const isNow = day.date === data.today && hour.hour === data.nowHour;
    const row = el("article", {
      class: "hour" + (hour.best ? " best" : "") + (isNow ? " now" : ""),
      id: "h-" + hour.hour,
    }, [
      el("div", { class: "when" }, [
        document.createTextNode(label),
        isNow ? el("span", { text: "agora" }) : null,
      ]),
      el("div", {}, [
        el("div", { class: "score-line" }, [
          el("strong", { class: tone(hour.score), text: String(hour.score) }),
          el("span", { text: hour.note }),
        ]),
        el("p", { text: hour.text }),
        hour.meta ? el("p", { class: "meta", text: hour.meta }) : null,
      ]),
    ]);
    hours.append(row);
  }

  const fetched = new Date(data.fetchedAt);
  const when = fetched.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
  const place = [data.place.praia, data.place.cidade].filter(Boolean).join(", ");
  const where = place + (data.place.estado ? " · " + data.place.estado : "");
  if (data.place.estado) kickerEl.textContent = data.place.estado;
  outEl.replaceChildren(
    days,
    el("p", { class: "meta", text: where + (data.place.estacao ? " · maré de " + data.place.estacao : "") }),
    hero,
    actions,
    prose,
    bars,
    hours,
    el("p", { class: "meta", text: "Lido do SurfGuru às " + when + ", horário de Brasília." })
  );
  document.title = day.score + " · " + place + " — Dia de pesca";
  days.querySelector("[aria-pressed='true']")?.scrollIntoView({ inline: "center", block: "nearest" });
}

async function boot() {
  try {
    const estados = await getJSON("/api/estados");
    const wantedEstado = params.get("estado") || localStorage.getItem("pesca-estado") || "espirito-santo";
    fillSelect(estadoEl, estados, wantedEstado);
    const estado = estadoEl.value || "espirito-santo";
    const wantedMun = params.get("municipio") || localStorage.getItem("pesca-mun-" + estado) || localStorage.getItem("pesca-mun") || "vitoria";
    await loadMunicipios(wantedMun);
    const wantedPraia = params.get("praia") || localStorage.getItem("pesca-praia-" + estado) || localStorage.getItem("pesca-praia") || "praia-camburi";
    await loadPraias(wantedPraia);
    await loadForecast(false);
  } catch (err) {
    setStatus(err.message, "error");
  }
}

estadoEl.addEventListener("change", async () => {
  setStatus("Carregando cidades…");
  kickerEl.textContent = estadoEl.selectedOptions[0] ? estadoEl.selectedOptions[0].textContent : "Costa do Brasil";
  try {
    const estado = estadoEl.value;
    const savedMun = localStorage.getItem("pesca-mun-" + estado) || "";
    await loadMunicipios(savedMun);
    const savedPraia = localStorage.getItem("pesca-praia-" + estado) || "";
    await loadPraias(savedPraia);
    await loadForecast(false);
  } catch (err) {
    setStatus(err.message, "error");
  }
});

munEl.addEventListener("change", async () => {
  setStatus("Carregando praias…");
  try {
    await loadPraias("");
    await loadForecast(false);
  } catch (err) {
    setStatus(err.message, "error");
  }
});

praiaEl.addEventListener("change", () => loadForecast(false));

boot();
