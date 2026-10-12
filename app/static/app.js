"use strict";
// JurisBusca SP — interface. Todo conteúdo vindo de documentos externos é inserido como TEXTO
// (textContent), nunca como HTML.

const NL = "Não localizado na fonte consultada";
const state = { token: null, sources: [], run: null, term: 0, page: 1, sort: "relevancia", srcFilter: "",
                poll: null, docPage: 1 };

// ---------------------------------------------------------------- utilidades
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $ = (s) => document.querySelector(s);
const fmtNum = (n) => (n ?? 0).toLocaleString("pt-BR");
function fmtDate(s) {
  if (!s) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(s);
  if (!m) return s;
  return m[4] ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]} UTC` : `${m[3]}/${m[2]}/${m[1]}`;
}
function safeHref(u) {
  try { const x = new URL(u); return (x.protocol === "https:" || x.protocol === "http:") ? x.href : null; }
  catch { return null; }
}

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (state.token) headers["X-App-Token"] = state.token;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, { method: opts.method || "GET", headers, body: opts.body });
  if (r.status === 401) { askToken(); throw new Error("Não autorizado"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    let msg = data && data.detail !== undefined ? data.detail : data;
    if (msg && typeof msg === "object") msg = msg.erro || (Array.isArray(msg) ? msg.map((e) => e.msg).join("; ") : JSON.stringify(msg));
    throw new Error(msg || `Erro HTTP ${r.status}`);
  }
  return data;
}
function askToken() {
  const t = prompt("Informe o token de acesso (JURIS_APP_TOKEN):");
  if (t) { state.token = t; try { sessionStorage.setItem("juris-token", t); } catch {} location.reload(); }
}
function withToken(url) {
  if (!state.token) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(state.token);
}
function openDialog(title, body, tools) {
  $("#dlg-title").textContent = title;
  $("#dlg-body").replaceChildren(body);
  $("#dlg-tools").replaceChildren(...(tools || []));
  const d = $("#dlg");
  if (!d.open) d.showModal();
}
function badge(text, kind) { return h("span", { class: "badge " + (kind || "") }, text); }

const SRC_STATUS = {
  ativa: ["Ativa", "ok"], nao_configurada: ["Não configurada", ""], falha_acesso: ["Falha de acesso", "err"],
  desativada: ["Desativada", ""], nao_integravel: ["Não integrável", "warn"],
  nao_validada: ["Implementada — não validada", "warn"],
};
const RUN_STATUS = { sucesso: ["Sucesso", "ok"], parcial: ["Parcial", "warn"], falha: ["Falha", "err"],
  em_andamento: ["Em andamento", "info"], cancelada: ["Cancelada", ""] };

// ---------------------------------------------------------------- abas
function showTab(name) {
  document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll("main > section").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + name));
  if (name === "fontes") loadSources();
  if (name === "coletas") loadCollections();
  if (name === "acervo") loadDocuments();
  if (name === "auditoria") loadAudit();
  try { localStorage.setItem("juris-tab", name); } catch {}
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  const s = await api("/api/status");
  if (s.token_exigido && !s.autenticado) { askToken(); throw new Error("Token de acesso necessário"); }
  $("#status-line").textContent = `${fmtNum(s.documentos)} documentos no acervo (${fmtNum(s.documentos_unicos)} únicos) · ` +
    `OCR ${s.ocr_disponivel ? "disponível" : "indisponível"} · Resumo por IA ${s.ia_disponivel ? "disponível" : "não configurado"}` +
    (s.proxy ? ` · Saída pelas fontes via proxy ${s.proxy}` : "");
  state.status = s;
  const banner = $("#mode-banner");
  banner.replaceChildren();
  if (s.token_ausente) {
    banner.append(h("div", { class: "notice err" }, "Configuração pendente: defina a variável JURIS_APP_TOKEN no painel da " +
      "Vercel (Settings → Environment Variables) com uma senha longa e faça um novo deploy. Até lá o sistema fica bloqueado."));
  }
  if (s.modo_temporario) {
    banner.append(h("div", { class: "notice" }, h("b", {}, "Modo temporário (Vercel): "),
      "o acervo fica em disco temporário e é apagado com frequência (a cada reinício do servidor, e cada instância tem o seu); " +
      "coletas são limitadas a poucas páginas, não há coletas agendadas nem OCR, e o DJEN pode ou não aceitar o acesso " +
      "(a função roda na região de São Paulo; use Fontes → Testar conexão). " +
      "Use para conhecer a interface. Para uso real, veja docs/HOSPEDAGEM_GRATIS.md."));
  }
}

// ---------------------------------------------------------------- conversa
let parseTimer = null;
function askBody() {
  return {
    message: $("#ask").value, online: $("#opt-online").checked, fold_accents: $("#opt-fold").checked,
    online_max_pages: Number($("#opt-pages").value) || null,
    date_from: $("#f-from").value || null, date_to: $("#f-to").value || null,
  };
}

function termChips(terms) {
  return terms.map((t) => h("span", { class: "chip " + (t.mode === "frase_exata" ? "exact" : "") }, `Pesquisa ${t.index + 1}: `,
    h("b", {}, t.mode === "frase_exata" ? `"${t.raw}"` : t.raw), ` — ${t.mode_label}`));
}

function previewParse() {
  clearTimeout(parseTimer);
  parseTimer = setTimeout(async () => {
    const box = $("#parse-preview"), err = $("#parse-error");
    err.textContent = "";
    if (!$("#ask").value.trim()) { box.replaceChildren(); return; }
    try {
      const it = await api("/api/interpret", { method: "POST", json: askBody() });
      box.replaceChildren(...termChips(it.terms),
        h("span", { class: "chip city" }, it.cidade ? `Cidade: ${it.cidade}` : "Todo o Estado de SP"),
        h("span", { class: "chip city" }, periodo(it)),
        h("span", { class: "chip exact" }, "Só Despacho / Decisão do juiz"));
    } catch (e) { box.replaceChildren(); err.textContent = e.message; }
  }, 300);
}

function addMsg(role, ...children) {
  const bubble = h("div", { class: "bubble" }, ...children);
  const row = h("div", { class: "msg " + role },
    role === "ai" ? h("img", { class: "avatar", src: "/static/logo.png", alt: "" }) : null, bubble);
  $("#chat").append(row);
  document.body.classList.add("chatting");
  row.scrollIntoView({ behavior: "smooth", block: "start" });
  return bubble;
}

function fmtBr(d) { return d ? d.split("-").reverse().join("/") : ""; }
function periodo(it) {
  const a = $("#f-from").value || it.data_inicial, b = $("#f-to").value || it.data_final;
  if (a && b) return a === b ? `Data: ${fmtBr(a)}` : `Período: ${fmtBr(a)} a ${fmtBr(b)}`;
  if (a) return `Desde ${fmtBr(a)}`;
  if (b) return `Até ${fmtBr(b)}`;
  return "Período: últimos 30 dias";
}

function interpretationEl(it) {
  return h("div", { class: "interp" },
    h("p", {}, "Entendi assim:"),
    h("div", { class: "chips" }, termChips(it.terms)),
    h("p", { class: "small" }, it.cidade
      ? ["Local: somente ", h("b", {}, it.cidade), " (comarca correspondente no TJSP; varas com o nome da cidade no DJEN)."]
      : "Local: todo o Estado de São Paulo."),
    h("p", { class: "small" }, periodo(it) + " · somente DESPACHO e DECISÃO do juiz."),
    it.ignoradas.length ? h("p", { class: "small muted" }, "Usei como instrução, sem pesquisar: " + it.ignoradas.join(" · ")) : null,
    ...(it.warnings || []).map((w) => h("p", { class: "small muted" }, "Aviso: " + w)));
}

function summaryEl(run) {
  if (run.status === "erro") return h("div", { class: "notice err" }, "Não consegui concluir a pesquisa: " + run.error);
  const st = run.stats;
  const city = run.params && run.params.city;
  const where = city ? ` em ${city}` : " no Estado de São Paulo";
  const cov = run.coverage || [];
  const searched = cov.filter((c) => c.pesquisada_no_acervo).map((c) => c.name);
  const failed = cov.filter((c) => (c.consultada_online && c.consultada_online.status === "falha") ||
    c.avisos.some((a) => a.includes("FALHOU"))).map((c) => c.name);
  const box = h("div", { class: "answer" });
  if (st.total_occurrences) {
    box.append(h("p", {}, "Encontrei ", h("b", {}, `${fmtNum(st.total_occurrences)} ocorrência(s)`), " em ",
      h("b", {}, `${fmtNum(st.unique_documents)} despacho(s)/decisão(ões)`), `${where}.`));
    box.append(h("ul", { class: "small" }, st.per_term.map((t) => h("li", {},
      `${t.mode === "frase_exata" ? `"${t.term}"` : t.term} (${t.mode_label.toLowerCase()}): ${fmtNum(t.occurrences)} ocorrência(s) em ${fmtNum(t.documents)} documento(s)`))));
  } else {
    box.append(h("p", {}, `Não encontrei nenhuma ocorrência confirmada em despachos ou decisões${where}.`));
  }
  box.append(h("p", { class: "small muted" }, searched.length
    ? `Pesquisei no texto integral de: ${searched.join("; ")}.`
    : "Nenhuma fonte tinha despachos ou decisões no acervo para esta pesquisa."));
  if (failed.length) box.append(h("div", { class: "notice err small" }, `Não consegui consultar agora: ${failed.join("; ")}. ` +
    "Ausência de resultados nessas fontes não comprova que não existam publicações (detalhes no relatório de cobertura)."));
  return box;
}

function freezeActive() {
  clearInterval(state.poll);
  const area = state.runArea, run = state.run;
  if (!area || !run || !area.isConnected) return;
  const id = run.id;
  area.replaceChildren(summaryEl(run),
    h("button", { class: "btn small", onclick: () => activate(area, id) }, "Reabrir resultados"));
  state.runArea = null;
}

async function activate(area, id) {
  freezeActive();
  state.runArea = area;
  state.term = 0; state.page = 1; state.srcFilter = "";
  await openRun(id);
  area.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitAsk(ev) {
  if (ev) ev.preventDefault();
  const body = askBody();
  if (!body.message.trim()) return;
  freezeActive();
  document.querySelector("#ask-form details.filters").open = false;
  addMsg("user", body.message);
  $("#ask").value = "";
  $("#parse-preview").replaceChildren();
  $("#parse-error").textContent = "";
  const bubble = addMsg("ai", h("p", {}, h("span", { class: "spin" }), " Lendo sua pergunta…"));
  $("#btn-ask").disabled = true;
  try {
    const it = await api("/api/interpret", { method: "POST", json: body });
    const area = h("div", { class: "run-area" },
      h("p", {}, h("span", { class: "spin" }), body.online
        ? " Consultando as fontes jurídicas de SP e conferindo cada ocorrência no texto integral… (pode levar alguns minutos)"
        : " Procurando no acervo…"));
    bubble.replaceChildren(interpretationEl(it), area);
    state.runArea = area;
    const r = await api("/api/ask", { method: "POST", json: body });
    state.term = 0; state.page = 1; state.srcFilter = "";
    await openRun(r.run_id);
    loadHistory();
  } catch (e) {
    bubble.replaceChildren(h("p", {}, e.message));
  } finally { $("#btn-ask").disabled = false; }
}

async function openRun(id) {
  clearInterval(state.poll);
  const run = await api(`/api/searches/${id}`);
  state.run = run;
  renderRun();
  if (run.status === "em_andamento") {
    state.poll = setInterval(async () => {
      const r = await api(`/api/searches/${id}`);
      state.run = r;
      if (r.status !== "em_andamento") { clearInterval(state.poll); renderRun(); loadStatus(); loadHistory(); }
      else renderProgress();
    }, 1500);
  }
}

function renderProgress() {
  const box = $("#run-progress");
  if (!box) return;
  const pr = state.run.progress;
  const fontes = pr && pr.fontes ? Object.values(pr.fontes) : [];
  const fase = { consulta_online: "Consultando portais…", busca_local: "Buscando no acervo…", concluida: "Concluída" };
  box.replaceChildren(
    h("p", {}, h("span", { class: "spin" }), " ", pr ? (fase[pr.fase] || pr.fase) : "Iniciando…"),
    h("ul", { class: "progress-list" }, fontes.map((f) => h("li", {},
      h("b", {}, f.nome), " — ", f.status.replace("_", " "), ` · ${f.paginas_examinadas} página(s) examinada(s), `,
      `${f.documentos_recuperados} documento(s) recuperado(s), ${f.documentos_novos} novo(s)`,
      f.mensagens.length ? h("div", { class: "muted small" }, f.mensagens.slice(-2).join(" | ")) : null))));
}

function renderRun() {
  const run = state.run;
  const area = state.runArea;
  if (!area) return;
  const panel = h("div", { class: "run" });
  area.replaceChildren(panel);
  if (run.status === "em_andamento") { panel.append(h("div", { id: "run-progress" })); renderProgress(); return; }
  if (run.status === "erro") { panel.append(summaryEl(run)); return; }
  panel.append(summaryEl(run));
  const st = run.stats;
  panel.append(h("div", { class: "stats" },
    h("div", { class: "stat" }, h("div", { class: "v" }, fmtNum(st.total_occurrences)), h("div", { class: "l" }, "ocorrências (todos os termos)")),
    h("div", { class: "stat" }, h("div", { class: "v" }, fmtNum(st.total_results)), h("div", { class: "l" }, "resultados (documento × termo)")),
    h("div", { class: "stat" }, h("div", { class: "v" }, fmtNum(st.unique_documents)), h("div", { class: "l" }, "documentos únicos")),
    h("div", { class: "stat" }, h("div", { class: "v" }, fmtNum(st.sources_with_results)), h("div", { class: "l" }, "fontes com resultado"))));
  const exp = (fmt, all) => {
    const q = all ? "" : `?term=${state.term}` + (state.srcFilter ? `&source_id=${state.srcFilter}` : "");
    return h("a", { class: "btn small", href: withToken(`/api/searches/${run.id}/export.${fmt}${q}`) },
      `${fmt.toUpperCase()} ${all ? "(todos os resultados)" : "(seleção atual)"}`);
  };
  panel.append(h("div", { class: "toolbar" }, h("span", { class: "muted" }, "Exportar:"),
    exp("csv", true), exp("csv", false), exp("xlsx", true), exp("xlsx", false)));
  panel.append(h("div", { class: "term-tabs", role: "tablist" }, st.per_term.map((t) =>
    h("button", { role: "tab", "aria-selected": String(t.term_index === state.term),
      onclick: () => { state.term = t.term_index; state.page = 1; renderRun(); } },
      `${t.mode === "frase_exata" ? `"${t.term}"` : t.term} · ${fmtNum(t.occurrences)} ocorr. em ${fmtNum(t.documents)} doc.`))));
  const cur = st.per_term.find((t) => t.term_index === state.term);
  if (cur) panel.append(h("p", { class: "small muted" }, `Modo: ${cur.mode_label}.`));
  const srcSel = h("select", { onchange: (e) => { state.srcFilter = e.target.value; state.page = 1; loadResults(); } },
    h("option", { value: "" }, "Todas as fontes"),
    ...state.sources.map((s) => h("option", { value: s.id, selected: String(s.id) === String(state.srcFilter) }, s.name)));
  const sortSel = h("select", { onchange: (e) => { state.sort = e.target.value; state.page = 1; loadResults(); } },
    ...[["relevancia", "Mais ocorrências"], ["data", "Data (mais recente)"], ["data_asc", "Data (mais antiga)"], ["fonte", "Fonte"]]
      .map(([v, l]) => h("option", { value: v, selected: v === state.sort }, l)));
  panel.append(h("div", { class: "toolbar" }, h("label", {}, "Ordenar ", sortSel), h("label", {}, "Fonte ", srcSel)));
  panel.append(h("div", { id: "results" }), h("div", { class: "pager", id: "pager" }));
  panel.append(renderCoverage(run));
  loadResults();
}

function renderCoverage(run) {
  const p = h("details", { class: "coverage" }, h("summary", {}, "Relatório de cobertura e limitações"));
  p.append(h("p", { class: "help" }, "A pesquisa é feita no texto efetivamente armazenado. Uma fonte sem resultado só " +
    "comprova ausência dentro do acervo coletado e dentro dos documentos com texto extraído."));
  const rows = (run.coverage || []).map((c) => {
    const [label, kind] = SRC_STATUS[c.status] || [c.status, ""];
    const online = c.consultada_online;
    return h("tr", {},
      h("td", {}, h("b", {}, c.name), h("div", {}, badge(label, kind))),
      h("td", {}, c.pesquisada_no_acervo ? "Sim" : "Não"),
      h("td", {}, online ? `${online.status} · ${online.paginas_examinadas} pág. · ${online.documentos_recuperados} doc.` : "—"),
      h("td", {}, `${fmtNum(c.documentos_no_acervo)} (${fmtNum(c.documentos_integrais)} integrais, ${fmtNum(c.documentos_parciais)} parciais, ${fmtNum(c.documentos_sem_texto)} sem texto)`),
      h("td", {}, fmtNum(c.documentos_com_correspondencia)),
      h("td", {}, c.mensagem, ...c.avisos.map((a) => h("div", { class: "notice err small" }, a)),
        online && online.mensagens && online.mensagens.length ? h("div", { class: "muted small" }, online.mensagens.join(" | ")) : null));
  });
  p.append(h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, ...["Fonte", "Pesquisada no acervo", "Consulta online", "Documentos no acervo", "Com correspondência", "Situação"].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows))));
  return p;
}

function metaRow(label, value, isDate, missing = NL) {
  const v = isDate ? fmtDate(value) : value;
  return [h("dt", {}, label), v ? h("dd", {}, v) : h("dd", { class: "missing" }, missing)];
}
const srow = (label, value, isDate) => metaRow(label, value, isDate, "—");
function linkOrMissing(url, label) {
  const u = url && safeHref(url);
  return u ? h("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, label) : h("span", { class: "missing" }, NL);
}

function snippetEl(o) {
  return h("div", { class: "snippet" },
    h("span", { class: "where" }, `Ocorrência ${o.ordinal}` + (o.page ? ` · página ${o.page} do PDF` : "") + ` · posição ${o.start_pos}–${o.end_pos}`),
    "…" + o.snippet_before, h("mark", {}, o.match_text), o.snippet_after + "…");
}

async function loadResults() {
  const run = state.run;
  const box = $("#results");
  if (!box) return;
  box.replaceChildren(h("p", {}, h("span", { class: "spin" }), " Carregando resultados…"));
  const q = new URLSearchParams({ term: state.term, page: state.page, page_size: 20, sort: state.sort });
  if (state.srcFilter) q.set("source_id", state.srcFilter);
  const data = await api(`/api/searches/${run.id}/results?${q}`);
  if (!data.total) {
    box.replaceChildren(h("div", { class: "notice info" }, "Nenhuma ocorrência confirmada para este termo no acervo pesquisado. " +
      "Consulte o relatório de cobertura abaixo para ver quais fontes foram efetivamente pesquisadas."));
    $("#pager").replaceChildren();
    return;
  }
  box.replaceChildren(...data.items.map((d) => resultCard(d)));
  const pager = $("#pager");
  pager.replaceChildren(
    h("button", { class: "btn small", disabled: data.page <= 1, onclick: () => { state.page--; loadResults(); } }, "‹ Anterior"),
    h("span", {}, `Página ${data.page} de ${data.pages} · ${fmtNum(data.total)} documento(s)`),
    h("button", { class: "btn small", disabled: data.page >= data.pages, onclick: () => { state.page++; loadResults(); } }, "Próxima ›"));
}

function resultCard(d) {
  const verified = d.verification_status === "Integralmente verificado";
  const card = h("article", { class: "result" });
  card.append(h("h4", {}, d.title || NL),
    h("div", { class: "badges" },
      badge(d.source_name, "info"),
      d.act_kind ? badge({ despacho: "Despacho", decisao: "Decisão", sentenca: "Sentença", acordao: "Acórdão", outro: "Outro ato" }[d.act_kind] || d.act_kind, "ok") : null,
      badge(`${d.occurrence_count} ocorrência(s)`, ""),
      badge(`Extração: ${d.extraction_status} (${d.extraction_method})`, d.extraction_status === "completo" ? "ok" : "warn"),
      badge(d.verification_status, verified ? "ok" : "warn"),
      d.dup_of ? badge(`Duplicata do documento ${d.dup_of}`, "warn") : null,
      d.version_changed_since_search ? badge("Documento atualizado após a pesquisa", "warn") : null));
  if (!verified) card.append(h("div", { class: "notice small" },
    "Documento parcialmente processado: " + (d.extraction_notes || "texto integral não disponível na fonte") + ". Pode haver ocorrências não localizadas."));
  card.append(h("dl", { class: "meta" },
    ...metaRow("Tribunal", d.court), ...metaRow("Órgão julgador / vara", d.organ),
    ...metaRow("Tipo de ato (fonte)", d.act_type), ...metaRow("Processo", d.process_number),
    ...metaRow("Magistrado / relator", d.judge), ...metaRow("Data de disponibilização", d.date_available, true),
    ...metaRow("Data de publicação", d.date_publication, true), ...metaRow("Data da decisão", d.date_decision, true),
    ...metaRow("Última coleta", d.last_collected_at, true),
    h("dt", {}, "Publicação"), h("dd", {}, linkOrMissing(d.url_publication, "Abrir publicação")),
    h("dt", {}, "Documento original"), h("dd", {}, linkOrMissing(d.url_original, "Abrir original"))));
  const occBox = h("div", {});
  const showN = 3;
  occBox.append(...d.occurrences.slice(0, showN).map(snippetEl));
  card.append(occBox);
  const actions = h("div", { class: "actions" });
  if (d.occurrences.length > showN) {
    actions.append(h("button", { class: "btn small", onclick: (e) => {
      occBox.replaceChildren(...d.occurrences.map(snippetEl)); e.target.remove(); } }, `Mostrar as ${d.occurrence_count} ocorrências`));
  }
  actions.append(h("button", { class: "btn small", onclick: () => openFullText(d.id, state.run.id, state.term) }, "Texto integral com destaques"));
  if (state.status && state.status.ia_disponivel) actions.append(h("button", { class: "btn small", onclick: () => openSummary(d.id, d.title) }, "Resumo por IA"));
  card.append(actions);
  return card;
}

async function openFullText(docId, runId, term) {
  const [doc, occ] = await Promise.all([
    api(`/api/documents/${docId}`),
    runId ? api(`/api/searches/${runId}/documents/${docId}/occurrences?term=${term}`) : Promise.resolve([]),
  ]);
  const text = doc.text;
  const body = h("div", { class: "fulltext" });
  let pos = 0;
  const marks = [];
  for (const o of occ) {
    if (o.start_pos < pos) continue;
    body.append(text.slice(pos, o.start_pos));
    const m = h("mark", { id: `occ-${o.ordinal}` }, text.slice(o.start_pos, o.end_pos));
    marks.push(m); body.append(m);
    pos = o.end_pos;
  }
  body.append(text.slice(pos));
  let idx = -1;
  const counter = h("span", { class: "small muted" }, occ.length ? `${occ.length} ocorrência(s)` : "");
  const go = (delta) => {
    if (!marks.length) return;
    if (idx >= 0) marks[idx].classList.remove("active");
    idx = (idx + delta + marks.length) % marks.length;
    marks[idx].classList.add("active");
    marks[idx].scrollIntoView({ block: "center" });
    counter.textContent = `Ocorrência ${idx + 1} de ${marks.length}`;
  };
  const tools = [counter,
    h("button", { class: "btn small", onclick: () => go(-1), disabled: !marks.length }, "‹ Anterior"),
    h("button", { class: "btn small", onclick: () => go(1), disabled: !marks.length }, "Próxima ›")];
  if (doc.has_original) tools.push(h("a", { class: "btn small", href: withToken(`/api/documents/${docId}/original`) }, "Baixar original armazenado"));
  const info = h("div", { class: "small muted" },
    `Fonte: ${doc.source_name} · versão ${doc.version} · extração ${doc.extraction_method} (${doc.extraction_status}) · ${doc.verification_status}`,
    doc.duplicates.length ? ` · conteúdo idêntico em: ${doc.duplicates.map((x) => `#${x.id} (${x.source_name})`).join(", ")}` : "");
  openDialog(doc.title || `Documento ${docId}`, h("div", {}, info, h("hr", {}), body), tools);
  if (marks.length) setTimeout(() => go(1), 50);
}

async function openSummary(docId, title) {
  openDialog("Resumo por IA — " + (title || docId), h("p", {}, h("span", { class: "spin" }), " Gerando resumo…"));
  try {
    const r = await api(`/api/documents/${docId}/summary`, { method: "POST" });
    if (!r.ok) { $("#dlg-body").replaceChildren(h("div", { class: "notice err" }, r.erro)); return; }
    $("#dlg-body").replaceChildren(h("div", { class: "notice" }, r.aviso), h("div", { class: "fulltext" }, r.resumo),
      h("p", { class: "small muted" }, "Modelo: " + r.modelo));
  } catch (e) { $("#dlg-body").replaceChildren(h("div", { class: "notice err" }, e.message)); }
}

async function loadHistory() {
  const list = await api("/api/searches?limit=8");
  $("#history").replaceChildren(...list.map((r) => h("button", { type: "button", class: "chip recent-chip", title: fmtDate(r.created_at),
    onclick: () => {
      freezeActive();
      addMsg("user", r.command);
      const area = h("div", { class: "run-area" });
      addMsg("ai", area);
      activate(area, r.id);
    } }, r.command.length > 48 ? r.command.slice(0, 47) + "…" : r.command)));
  $("#recent").classList.toggle("hidden", !list.length);
}

// ---------------------------------------------------------------- fontes
async function loadSources() {
  state.sources = await api("/api/sources");
  const list = $("#sources-list");
  if (!list) return;
  list.replaceChildren(...state.sources.map(sourceCard));
  renderSourceForm();
}

function collectForm(s) {
  const f = h("form", { class: "grid-form" });
  const add = (name, label, type = "text", value = "") => f.append(h("label", {}, label, h("input", { name, type, value })));
  if (s.connector === "tjsp_cjpg" || s.connector === "tjsp_dje") {
    add("query", "Termo para o portal (aspas = frase)");
    add("data_inicio", "Data inicial", "date"); add("data_fim", "Data final", "date");
    add("max_paginas", "Máx. páginas de resultados", "number", s.config.max_paginas || 5);
  } else if (s.connector === "djen") {
    add("data_inicio", "Disponibilização de", "date"); add("data_fim", "até", "date");
    add("numero_processo", "Número do processo (opcional)"); add("numero_oab", "OAB (opcional)"); add("uf_oab", "UF da OAB");
    add("max_paginas", "Máx. páginas (100 itens cada)", "number", s.config.max_paginas || 50);
  } else if (s.connector === "generic_web") {
    f.append(h("label", {}, "URLs (uma por linha; vazio = usar as cadastradas)", h("textarea", { name: "urls" })));
    add("max_paginas", "Máx. páginas", "number", s.config.max_paginas || 20);
  }
  f.append(h("label", {}, " ", h("button", { class: "btn primary", type: "submit" }, "Iniciar coleta")));
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const params = {};
    for (const [k, v] of new FormData(f).entries()) {
      if (v === "") continue;
      params[k] = k === "max_paginas" ? Number(v) : (k === "urls" ? v.split(/\n+/).map((x) => x.trim()).filter(Boolean) : v);
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = state.status && state.status.modo_temporario ? "Coletando (pode levar alguns minutos)…" : "Iniciando…";
    try { await api(`/api/sources/${s.id}/collect`, { method: "POST", json: { params } }); $("#dlg").close(); showTab("coletas"); }
    catch (err) { alert(err.message); btn.disabled = false; btn.textContent = "Iniciar coleta"; }
  });
  return f;
}

function sourceCard(s) {
  const [label, kind] = SRC_STATUS[s.status] || [s.status, ""];
  const card = h("div", { class: "src-card" });
  const last = s.ultima_coleta;
  card.append(h("h3", {}, s.name), h("div", { class: "badges" }, badge(label, kind), s.court ? badge(s.court, "info") : null,
    badge(`${fmtNum(s.documentos)} documento(s)`), s.running_collection ? badge("Coleta em andamento", "info") : null,
    s.pesquisa_por_termo ? badge("Pesquisa por termo no portal") : null, s.coleta_por_periodo ? badge("Coleta por período") : null));
  card.append(h("p", { class: "small" }, s.status_message || s.last_error || ""),
    s.known_restrictions ? h("p", { class: "small muted" }, "Restrições: " + s.known_restrictions) : null,
    h("details", {}, h("summary", { class: "small muted" }, "Detalhes, método de acesso e restrições"), h("dl", { class: "meta" },
    ...srow("URL principal", s.base_url), ...srow("Categoria", s.category), ...srow("Conector", s.connector),
    ...srow("Método de acesso", s.access_method), ...srow("Mecanismo de pesquisa", s.search_mechanism),
    ...srow("Paginação", s.pagination), ...srow("Obtenção dos documentos", s.document_method),
    ...srow("Formatos", s.formats), ...srow("Política de atualização", s.update_policy),
    ...srow("Coleta agendada", s.schedule_minutes ? `a cada ${s.schedule_minutes} min` : "não"),
    ...srow("Última coleta bem-sucedida", s.last_success_at, true),
    ...srow("Última coleta", last ? `${(RUN_STATUS[last.status] || [last.status])[0]} em ${fmtDate(last.started_at)}` : null),
    ...srow("Mensagem de erro", s.last_error), ...srow("Situação", s.status_message),
    ...srow("Restrições conhecidas", s.known_restrictions))));
  const actions = h("div", { class: "actions" });
  if (["tjsp_cjpg", "tjsp_dje", "djen", "generic_web"].includes(s.connector)) {
    actions.append(
      h("button", { class: "btn small", disabled: !s.enabled || !!s.running_collection, onclick: () => openDialog(`Coletar — ${s.name}`, collectForm(s)) }, "Coletar"),
      h("button", { class: "btn small", onclick: async (e) => {
        e.target.disabled = true; e.target.textContent = "Testando…";
        try { const r = await api(`/api/sources/${s.id}/test`, { method: "POST" });
          openDialog(`Teste de conexão — ${s.name}`, h("pre", { class: "json" }, JSON.stringify(r, null, 2))); }
        catch (err) { alert(err.message); } finally { e.target.disabled = false; e.target.textContent = "Testar conexão"; } } }, "Testar conexão"));
    if (s.running_collection) actions.append(h("button", { class: "btn small danger", onclick: async () => { await api(`/api/sources/${s.id}/cancel`, { method: "POST" }); loadSources(); } }, "Cancelar coleta"));
  }
  actions.append(
    h("button", { class: "btn small", onclick: () => renderSourceForm(s) }, "Editar"),
    h("button", { class: "btn small", onclick: async () => { await saveSource({ ...s, enabled: !s.enabled }, s.id); } }, s.enabled ? "Desativar" : "Ativar"),
    h("button", { class: "btn small danger", onclick: async () => {
      if (!confirm(`Remover a fonte "${s.name}" e TODOS os seus ${s.documentos} documento(s)?`)) return;
      try { await api(`/api/sources/${s.id}`, { method: "DELETE" }); loadSources(); loadStatus(); } catch (err) { alert(err.message); } } }, "Remover"));
  card.append(actions);
  return card;
}

const SRC_FIELDS = [["slug", "Identificador (a-z, 0-9, -)"], ["name", "Nome"], ["court", "Tribunal (TJSP/TRT15)"],
  ["base_url", "URL principal"], ["category", "Categoria"], ["connector", "Conector"], ["access_method", "Método de acesso"],
  ["search_mechanism", "Mecanismo de pesquisa"], ["pagination", "Navegação entre resultados"], ["document_method", "Obtenção dos documentos"],
  ["formats", "Formatos"], ["update_policy", "Política de atualização"], ["schedule_minutes", "Coleta agendada (min; 0 = não)"],
  ["retry_minutes", "Nova tentativa após falha (min)"], ["max_retries", "Tentativas por requisição"],
  ["known_restrictions", "Restrições conhecidas"]];

function renderSourceForm(s) {
  const f = $("#source-form");
  const editing = !!s;
  const v = s || { connector: "generic_web", category: "arquivos", schedule_minutes: 0, retry_minutes: 60, max_retries: 3, config: {}, enabled: true };
  f.replaceChildren();
  for (const [k, label] of SRC_FIELDS) {
    if (k === "connector") {
      f.append(h("label", {}, label, h("select", { name: k }, ...["tjsp_cjpg", "tjsp_dje", "djen", "generic_web", "upload", "nenhum"]
        .map((c) => h("option", { value: c, selected: c === v.connector }, c)))));
    } else {
      const num = ["schedule_minutes", "retry_minutes", "max_retries"].includes(k);
      f.append(h("label", {}, label, h("input", { name: k, type: num ? "number" : "text", value: v[k] ?? "" })));
    }
  }
  f.append(h("label", {}, "Configuração (JSON)", h("textarea", { name: "config" }, JSON.stringify(v.config || {}, null, 2))));
  f.append(h("label", {}, " ", h("span", {},
    h("button", { class: "btn primary", type: "submit" }, editing ? "Salvar alterações" : "Cadastrar fonte"),
    editing ? h("button", { class: "btn", type: "button", onclick: () => renderSourceForm() }, "Cancelar") : null)));
  f.onsubmit = async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(f).entries());
    let config;
    try { config = JSON.parse(fd.config || "{}"); } catch { alert("Configuração não é um JSON válido."); return; }
    const body = { ...fd, config, enabled: v.enabled, status: editing ? v.status : null };
    for (const k of ["schedule_minutes", "retry_minutes", "max_retries"]) body[k] = Number(body[k] || 0);
    for (const k of Object.keys(body)) if (body[k] === "") body[k] = null;
    await saveSource(body, editing ? s.id : null);
  };
  if (editing) f.scrollIntoView({ behavior: "smooth" });
}

async function saveSource(body, id) {
  const payload = { ...body };
  delete payload.id; delete payload.documentos; delete payload.ultima_coleta; delete payload.running_collection;
  delete payload.pesquisa_por_termo; delete payload.coleta_por_periodo;
  for (const k of ["created_at", "updated_at", "last_success_at", "last_attempt_at", "last_error", "status_message"]) delete payload[k];
  try {
    await api(id ? `/api/sources/${id}` : "/api/sources", { method: id ? "PUT" : "POST", json: payload });
    loadSources();
  } catch (e) { alert(e.message); }
}

// ---------------------------------------------------------------- coletas
async function loadCollections() {
  const rows = await api("/api/collections?limit=100");
  const t = $("#collections");
  t.replaceChildren(h("thead", {}, h("tr", {}, ...["#", "Fonte", "Origem", "Estado", "Início / fim", "Páginas", "Docs (novos/atual./inalt.)", "Parciais/falhas", "Total na fonte", "Mensagem", ""].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => {
      const [label, kind] = RUN_STATUS[r.status] || [r.status, ""];
      return h("tr", {}, h("td", {}, r.id), h("td", {}, r.source_name), h("td", {}, r.trigger), h("td", {}, badge(label, kind)),
        h("td", {}, fmtDate(r.started_at), h("br", {}), fmtDate(r.finished_at) || "—"), h("td", {}, r.pages_examined),
        h("td", {}, `${r.docs_found} (${r.docs_new}/${r.docs_updated}/${r.docs_unchanged})`),
        h("td", {}, `${r.docs_partial}/${r.docs_failed}`), h("td", {}, r.total_reported ?? "—"),
        h("td", {}, r.error || r.message || ""),
        h("td", {}, h("button", { class: "btn small", onclick: () => showLogs(r.id) }, "Logs"),
          (r.status === "parcial" || r.status === "falha" || r.status === "cancelada") && r.checkpoint_json
            ? h("button", { class: "btn small", onclick: async () => { try { await api(`/api/collections/${r.id}/resume`, { method: "POST" }); setTimeout(loadCollections, 500); } catch (e) { alert(e.message); } } }, "Retomar") : null));
    })));
  if (rows.some((r) => r.status === "em_andamento")) setTimeout(() => { if (!$("#tab-coletas").classList.contains("hidden")) loadCollections(); }, 2500);
}

async function showLogs(id) {
  const c = await api(`/api/collections/${id}`);
  openDialog(`Coleta #${id} — ${c.source_name}`, h("div", {},
    h("p", { class: "small" }, `Parâmetros: ${c.params_json} · Checkpoint: ${c.checkpoint_json || "—"}`),
    h("div", { class: "table-wrap" }, h("table", { class: "data" }, h("tbody", {}, c.logs.map((l) =>
      h("tr", {}, h("td", {}, fmtDate(l.at)), h("td", {}, badge(l.level, l.level === "erro" ? "err" : l.level === "aviso" ? "warn" : "")),
        h("td", {}, l.message, l.url ? h("div", { class: "muted small" }, l.url) : null))))))));
}

// ---------------------------------------------------------------- acervo
async function loadDocuments() {
  const sel = $("#doc-source");
  if (!sel.options.length) {
    sel.append(h("option", { value: "" }, "Todas"), ...state.sources.map((s) => h("option", { value: s.id }, s.name)));
    sel.onchange = () => { state.docPage = 1; loadDocuments(); };
    $("#doc-status").onchange = () => { state.docPage = 1; loadDocuments(); };
  }
  const q = new URLSearchParams({ page: state.docPage, page_size: 30 });
  if (sel.value) q.set("source_id", sel.value);
  if ($("#doc-status").value) q.set("status", $("#doc-status").value);
  const data = await api(`/api/documents?${q}`);
  $("#documents").replaceChildren(h("thead", {}, h("tr", {}, ...["#", "Título", "Fonte", "Processo", "Disponibilização", "Extração", "Verificação", "Versão", ""].map((x) => h("th", {}, x)))),
    h("tbody", {}, data.items.map((d) => h("tr", {}, h("td", {}, d.id), h("td", {}, d.title || NL), h("td", {}, d.source_name),
      h("td", {}, d.process_number || "—"), h("td", {}, fmtDate(d.date_available) || "—"),
      h("td", {}, `${d.extraction_method} · ${d.extraction_status}` + (d.ocr_confidence ? ` · OCR ${Math.round(d.ocr_confidence)}%` : "")),
      h("td", {}, d.verification_status), h("td", {}, d.version),
      h("td", {}, h("button", { class: "btn small", onclick: () => openFullText(d.id, null, 0) }, "Ver texto"))))));
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  $("#doc-pager").replaceChildren(
    h("button", { class: "btn small", disabled: state.docPage <= 1, onclick: () => { state.docPage--; loadDocuments(); } }, "‹"),
    h("span", {}, `Página ${state.docPage} de ${pages} · ${fmtNum(data.total)} documento(s)`),
    h("button", { class: "btn small", disabled: state.docPage >= pages, onclick: () => { state.docPage++; loadDocuments(); } }, "›"));
}

async function submitUpload(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const out = $("#upload-result");
  out.replaceChildren(h("p", {}, h("span", { class: "spin" }), " Processando (PDFs digitalizados passam por OCR e podem demorar)…"));
  try {
    const res = await api("/api/uploads", { method: "POST", body: fd });
    out.replaceChildren(h("ul", {}, res.map((r) => h("li", {}, `${r.arquivo}: `,
      r.erro ? h("span", { class: "badge err" }, r.erro) :
        `${r.resultado} · extração ${r.extracao} (${r.estado}) · ${fmtNum(r.caracteres)} caracteres` + (r.observacoes.length ? ` · ${r.observacoes.join("; ")}` : "")))));
    e.target.reset();
    loadDocuments(); loadStatus();
  } catch (err) { out.replaceChildren(h("div", { class: "notice err" }, err.message)); }
}

// ---------------------------------------------------------------- auditoria
async function loadAudit() {
  const rows = await api("/api/audit?limit=300");
  $("#audit").replaceChildren(h("thead", {}, h("tr", {}, ...["Data", "Agente", "Ação", "Detalhe"].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, fmtDate(r.at)), h("td", {}, r.actor), h("td", {}, r.action), h("td", {}, r.detail || "")))));
}

// ---------------------------------------------------------------- início
document.addEventListener("DOMContentLoaded", async () => {
  try { state.token = sessionStorage.getItem("juris-token"); } catch {}
  document.querySelectorAll("nav.tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("#dlg-close").addEventListener("click", () => $("#dlg").close());
  $("#ask").addEventListener("input", previewParse);
  $("#ask").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitAsk(); } });
  $("#opt-fold").addEventListener("change", previewParse);
  $("#ask-form").addEventListener("submit", submitAsk);
  document.querySelectorAll("[data-ex]").forEach((b) => b.addEventListener("click", () => {
    $("#ask").value = b.dataset.ex; $("#ask").focus(); previewParse(); }));
  $("#upload-form").addEventListener("submit", submitUpload);
  try {
    await loadStatus();
    await loadSources();
    await loadHistory();
    let tab = "pesquisa";
    try { tab = localStorage.getItem("juris-tab") || "pesquisa"; } catch {}
    showTab(tab);
  } catch (e) { $("#status-line").textContent = "Erro: " + e.message; }
});
