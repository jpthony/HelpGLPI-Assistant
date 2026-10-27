/* Assistant de saisie GLPI — interface utilisateur (sans dépendance, sans build). */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, ...children) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(c));
    return n;
  };
  const icon = (id) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", `#${id}`); s.append(u); s.setAttribute("aria-hidden", "true");
    return s;
  };

  const URGENCY = [
    { v: 2, label: "Ça peut attendre" },
    { v: 3, label: "Ça me gêne" },
    { v: 4, label: "Je suis bloqué(e)" },
    { v: 5, label: "Plusieurs personnes bloquées" },
  ];
  const urgencyBucket = (u) => (u <= 2 ? 2 : Math.min(5, u));
  const urgencyLabel = (u) => URGENCY.find((x) => x.v === urgencyBucket(u))?.label || "";
  const UNKNOWN = "Je ne sais pas";
  const PH_START = () => (matchMedia("(max-width: 520px)").matches ? "Décrivez votre problème…" : "Par exemple : « Mon imprimante affiche une erreur depuis ce matin »");

  const S = {
    me: null, ui: null, cats: [],
    phase: "describe",          // describe | thinking | clarify | review | sending | sent
    description: "", exchanges: [], rounds: 0,
    files: [], draft: null, ticket: null, qcard: null,
  };

  const thread = $("#thread"), input = $("#input"), sendBtn = $("#sendBtn"), tray = $("#tray");
  const slip = $("#slip"), slipBody = $("#slipBody"), slipCol = $("#slipCol"), scrim = $("#scrim");

  // ------------------------------------------------------------------ API
  async function api(path, { method = "GET", json, form } = {}) {
    const opt = { method, credentials: "same-origin", headers: { "X-Requested-With": "glpi-assistant" } };
    if (json !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(json); }
    if (form) opt.body = form;
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch { /* corps vide */ }
    if (!r.ok) {
      const err = new Error(data?.detail?.message || `Erreur ${r.status}`);
      err.status = r.status; err.detail = data?.detail || {};
      throw err;
    }
    return data;
  }

  // ----------------------------------------------------------------- Init
  async function init() {
    try {
      S.me = await api("/api/me");
    } catch (e) {
      if (e.status === 401 && e.detail?.sso) {
        const last = Number(sessionStorage.getItem("sso_try") || 0);
        if (Date.now() - last > 20000) {
          sessionStorage.setItem("sso_try", String(Date.now()));
          location.href = `${e.detail.sso}?next=${encodeURIComponent(location.pathname)}`;
          return;
        }
      }
      showNotice("Impossible de vous identifier automatiquement. Ouvrez cette page depuis un poste de l’entreprise, puis rechargez-la.");
      lockComposer(true);
      return;
    }
    sessionStorage.removeItem("sso_try");
    S.ui = S.me.ui;
    applyBranding();
    if (S.me.warning === "not_in_glpi") {
      showNotice("Votre compte n’est pas encore connu de l’outil de tickets. Vous pouvez décrire votre problème : le support sera prévenu.");
    }
    input.placeholder = PH_START();
    renderSlip();
    api("/api/categories").then((c) => { S.cats = c || []; if (S.phase === "review") renderSlip(); }).catch(() => {});
    input.focus();
  }

  function applyBranding() {
    const ui = S.ui;
    document.title = ui.app_name || document.title;
    $("#appName").textContent = ui.app_name || "";
    $("#company").textContent = ui.company || "";
    if (/^#[0-9a-f]{6}$/i.test(ui.accent || "")) document.documentElement.style.setProperty("--accent", ui.accent);
    if (ui.logo_url) $("#brandMark").replaceChildren(el("img", { src: ui.logo_url, alt: "" }));
    const first = S.me.firstname || "";
    $("#wTitle").textContent = (ui.welcome_title || "Bonjour,").replace("{prenom}", first).replace(/\s+,/, ",");
    $("#wSub").textContent = ui.welcome_subtitle || "";
    $("#wText").textContent = ui.welcome_text || "";
    const topics = $("#topics");
    topics.replaceChildren(...(ui.quick_topics || []).map((t) => el("li", {},
      el("button", { class: "topic", type: "button", onclick: () => { input.value = t.prompt || `${t.label} : `; autosize(); input.focus(); input.setSelectionRange(input.value.length, input.value.length); updateSend(); } },
        el("span", { class: "emoji", "aria-hidden": "true", text: t.emoji || "" }), t.label))));
    $("#userChip").hidden = false;
    $("#userName").textContent = S.me.display_name;
    $("#avatar").textContent = initials(S.me.display_name);
    $("#adminLink").hidden = !S.me.is_admin;
    const att = ui.attachments || {};
    if (att.extensions?.length) $("#fileInput").accept = att.extensions.map((x) => `.${x}`).join(",");
  }

  const initials = (n) => (n || "?").split(/[\s.\-_]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");

  function showNotice(text) { const n = $("#notice"); n.textContent = text; n.hidden = false; }
  function lockComposer(lock) { input.disabled = lock; $("#attachBtn").disabled = lock; updateSend(); }

  // ------------------------------------------------------------ Composer
  function autosize() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`; }
  function updateSend() { sendBtn.disabled = input.disabled || !input.value.trim() || ["thinking", "sending"].includes(S.phase); }

  input.addEventListener("input", () => { autosize(); updateSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); onSend(); });
  $("#attachBtn").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", (e) => { addFiles([...e.target.files]); e.target.value = ""; });

  input.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "h").replace(/h(\d\d)$/, "m$1");
    addFiles(files.map((f, i) => f.type.startsWith("image/") && /^image\.\w+$/i.test(f.name)
      ? new File([f], `capture-${stamp}${i ? `-${i}` : ""}.${f.type.split("/")[1] || "png"}`, { type: f.type }) : f));
  });

  const composer = $("#composer"), convo = $(".conversation");
  let dragDepth = 0;
  convo.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { dragDepth++; composer.classList.add("dragging"); } });
  convo.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; composer.classList.remove("dragging"); } });
  convo.addEventListener("dragover", (e) => e.preventDefault());
  convo.addEventListener("drop", (e) => {
    e.preventDefault(); dragDepth = 0; composer.classList.remove("dragging");
    addFiles([...(e.dataTransfer?.files || [])]);
  });

  function fmtSize(b) { return b < 1024 ? `${b} o` : b < 1048576 ? `${Math.round(b / 1024)} Ko` : `${(b / 1048576).toFixed(1).replace(".", ",")} Mo`; }

  function addFiles(list) {
    if (S.phase === "sent" || S.phase === "sending") return;
    const att = S.ui?.attachments || {};
    const max = att.max_files || 6, maxB = (att.max_mb || 10) * 1048576;
    const exts = (att.extensions || []).map((x) => x.toLowerCase());
    const refused = [];
    for (const f of list) {
      const ext = (f.name.split(".").pop() || "").toLowerCase();
      if (S.files.length >= max) { refused.push(`${f.name} (${max} fichiers maximum)`); continue; }
      if (exts.length && !exts.includes(ext)) { refused.push(`${f.name} (type non accepté)`); continue; }
      if (f.size > maxB) { refused.push(`${f.name} (plus de ${att.max_mb || 10} Mo)`); continue; }
      S.files.push({ id: crypto.randomUUID?.() || String(Math.random()), file: f, url: f.type.startsWith("image/") ? URL.createObjectURL(f) : null });
    }
    if (refused.length) agentSay([`Je n’ai pas pu ajouter : ${refused.join(", ")}.`]);
    renderTray(); renderSlip();
  }

  function renderTray() {
    tray.replaceChildren(...S.files.slice(sentFileCount).map((f) => el("span", { class: "file-pill" },
      f.url ? el("img", { class: "thumb", src: f.url, alt: "" }) : el("span", { class: "thumb", text: (f.file.name.split(".").pop() || "").slice(0, 4).toUpperCase() }),
      el("span", { class: "meta" }, el("span", { class: "fname", text: f.file.name, title: f.file.name }), el("span", { class: "fsize", text: fmtSize(f.file.size) })),
      el("button", { type: "button", "aria-label": `Retirer ${f.file.name}`, onclick: () => removeFile(f.id) }, icon("i-x")))));
  }
  function removeFile(id) {
    const idx = S.files.findIndex((x) => x.id === id);
    if (idx < 0) return;
    if (S.files[idx].url) URL.revokeObjectURL(S.files[idx].url);
    if (idx < sentFileCount) sentFileCount -= 1;
    S.files.splice(idx, 1);
    renderTray(); renderSlip();
  }

  // -------------------------------------------------------------- Thread
  function scrollDown() { requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })); }

  function agentMark() { const m = el("span", { class: "who" }); m.append(icon("i-mark")); m.firstChild.setAttribute("width", "32"); m.firstChild.setAttribute("height", "32"); return m; }

  function agentSay(paragraphs, cls = "agent") {
    const body = el("div", { class: "msg-body" }, ...paragraphs.filter(Boolean).map((p) => el("p", { text: p })));
    const n = el("div", { class: `msg ${cls}` }, agentMark(), body);
    thread.append(n); scrollDown(); return n;
  }

  function userSay(content, fileNames = []) {
    const body = el("div", { class: "msg-body" });
    if (typeof content === "string") body.append(content); else body.append(content);
    if (fileNames.length) body.append(el("div", { class: "files" }, ...fileNames.map((n) => el("span", { text: `📎 ${n}` }))));
    thread.append(el("div", { class: "msg user" }, body)); scrollDown();
  }

  let typingNode = null;
  function typing(on, label = "Je lis votre message…") {
    typingNode?.remove(); typingNode = null;
    if (!on) return;
    typingNode = el("div", { class: "msg agent", "aria-label": label }, agentMark(),
      el("div", { class: "msg-body typing" }, el("span", { class: "dots", "aria-hidden": "true" }, el("i"), el("i"), el("i")), el("span", { text: label })));
    thread.append(typingNode); scrollDown();
  }

  let sentFileCount = 0;
  function newFileNames() {
    const names = S.files.slice(sentFileCount).map((f) => f.file.name);
    sentFileCount = S.files.length;
    queueMicrotask(renderTray);
    return names;
  }

  function onSend() {
    const text = input.value.trim();
    if (!text || ["thinking", "sending", "sent"].includes(S.phase)) return;
    input.value = ""; autosize(); updateSend();
    $("#welcome")?.remove();
    userSay(text, newFileNames());
    if (!S.description) {
      S.description = text;
    } else {
      // Précision libre pendant les questions ou la vérification.
      closeQuestions();
      S.exchanges.push({ q: "", a: text });
      S.rounds += 1;
    }
    runTurn();
  }

  async function runTurn(force = false) {
    setPhase("thinking");
    typing(true, S.rounds ? "Je mets le ticket à jour…" : "Je lis votre message…");
    try {
      const res = await api("/api/agent/turn", { method: "POST", json: {
        description: S.description, exchanges: S.exchanges, rounds_done: S.rounds, force,
        attachments: S.files.map((f) => ({ name: f.file.name, type: f.file.type })),
      } });
      typing(false);
      const prev = S.draft;
      S.draft = res.ticket;
      if (res.status === "need_info" && res.questions.length) {
        agentSay([res.message || "Merci. J’ai besoin de quelques précisions pour que le support puisse agir vite."]);
        showQuestions(res.questions);
        setPhase("clarify", prev);
      } else {
        agentSay([res.message || "Merci, j’ai tout ce qu’il faut.", "Vérifiez le ticket et modifiez ce qui ne va pas, puis envoyez-le."]);
        setPhase("review", prev);
        openSheet();
      }
    } catch (e) {
      typing(false);
      // Le ticket reste possible même si l’IA est indisponible.
      S.draft = S.draft || { title: S.description.slice(0, 80), summary: S.description, facts: [], type: "incident", urgency: 3, category_id: null };
      agentSay([e.message || "L’assistant ne répond pas pour le moment.", "Complétez la fiche vous-même, elle sera envoyée telle quelle."], "agent error");
      setPhase("review");
      openSheet();
    }
  }

  // ----------------------------------------------------------- Questions
  function showQuestions(questions) {
    const state = questions.map((q) => ({ q, picked: new Set(), free: "" }));
    const blocks = state.map((st, i) => {
      const id = `q${i}`;
      const chips = el("div", { class: "chips", role: "group" });
      const toggle = (opt, btn) => {
        if (opt === UNKNOWN) { st.picked.clear(); st.picked.add(UNKNOWN); }
        else {
          st.picked.delete(UNKNOWN);
          if (st.q.multiple) st.picked.has(opt) ? st.picked.delete(opt) : st.picked.add(opt);
          else { const had = st.picked.has(opt); st.picked.clear(); if (!had) st.picked.add(opt); }
        }
        [...chips.children].forEach((c) => c.setAttribute("aria-pressed", String(st.picked.has(c.dataset.v))));
        btn.focus();
      };
      for (const opt of [...st.q.options, UNKNOWN]) {
        const b = el("button", { type: "button", class: `chip${opt === UNKNOWN ? " unknown" : ""}`, "aria-pressed": "false", "data-v": opt });
        b.textContent = opt;
        b.addEventListener("click", () => toggle(opt, b));
        chips.append(b);
      }
      const free = el("textarea", { class: "q-free", rows: "1", id: `${id}-free`,
        placeholder: st.q.options.length ? "Autre chose ? Précisez ici (facultatif)" : "Votre réponse", "aria-label": `Réponse libre : ${st.q.text}` });
      free.addEventListener("input", () => { st.free = free.value; });
      free.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } });
      return el("fieldset", { class: "q-block" },
        el("legend", {}, st.q.text, st.q.multiple ? el("span", { class: "hint", text: "plusieurs choix possibles" }) : null),
        chips, free);
    });

    const submit = () => {
      const answers = state.map((st) => {
        const parts = [...st.picked];
        if (st.free.trim()) parts.push(st.free.trim());
        return { q: st.q.text, a: parts.join(" ; ") || UNKNOWN };
      });
      closeQuestions();
      S.exchanges.push(...answers);
      S.rounds += 1;
      userSay(el("ul", { class: "answers" }, ...answers.map((a) => el("li", {}, el("span", { class: "q", text: a.q }), a.a))), newFileNames());
      runTurn();
    };
    const skip = () => { closeQuestions(); S.rounds += 1; userSay("Je n’ai pas plus d’informations."); runTurn(true); };

    S.qcard = el("form", { class: "qcard", onsubmit: (e) => { e.preventDefault(); submit(); } },
      ...blocks,
      el("div", { class: "qcard-actions" },
        el("button", { type: "button", class: "btn-quiet", onclick: skip, text: "Passer et rédiger le ticket" }),
        el("button", { type: "submit", class: "btn btn-primary", text: "Répondre" })));
    thread.append(S.qcard);
    scrollDown();
    S.qcard.querySelector(".chip, .q-free")?.focus({ preventScroll: true });
  }
  function closeQuestions() { S.qcard?.remove(); S.qcard = null; }

  // -------------------------------------------------------------- Phases
  function setPhase(phase, prevDraft) {
    S.phase = phase;
    slip.dataset.phase = phase === "thinking" ? (S.draft ? "clarify" : "describe") : phase;
    const order = ["describe", "clarify", "review"];
    const current = phase === "thinking" ? (S.rounds ? "clarify" : "describe") : phase === "sending" ? "review" : phase;
    const idx = phase === "sent" ? 3 : order.indexOf(current);
    $("#steps").querySelectorAll("li").forEach((li, i) => {
      li.classList.toggle("done", i < idx);
      if (i === idx) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    lockComposer(phase === "sent" || phase === "sending");
    $(".composer-wrap").hidden = phase === "sent";
    if (phase === "review") input.placeholder = "Un oubli ? Ajoutez une précision ici";
    else if (phase === "clarify") input.placeholder = "Vous pouvez aussi répondre ici, avec vos mots";
    renderSlip(prevDraft);
    updatePeek();
    updateSend();
  }

  // ------------------------------------------------------------ Fiche
  function field(label, content, { flash = false, cls = "" } = {}) {
    return el("div", { class: `field ${cls}${flash ? " flash" : ""}` }, el("span", { class: "label", text: label }), content);
  }
  const skeleton = () => el("div", { class: "skeleton", "aria-hidden": "true" }, el("i"), el("i"), el("i"));
  const categoryName = (id) => S.cats.find((c) => c.id === id)?.name;

  function renderSlip(prev) {
    const d = S.draft, phase = S.phase;
    const nodes = [];
    const states = {
      describe: "Je remplis cette fiche pendant que nous discutons.",
      thinking: "Je mets la fiche à jour…",
      clarify: "Il manque encore une ou deux informations.",
      review: "Prêt. Relisez, corrigez si besoin, puis envoyez.",
      sending: "Envoi en cours…",
      sent: "Envoyé au support.",
    };
    nodes.push(el("p", { class: "slip-state", role: "status", text: states[phase] || "" }));
    if (phase === "sent" && S.ticket) nodes.push(sentPanel());

    if (!d) {
      nodes.push(field("Sujet", skeleton()), field("Ce qui se passe", skeleton()));
      if (S.files.length) nodes.push(field("Pièces jointes", filesList()));
      slipBody.replaceChildren(...nodes);
      finishSlip();
      return;
    }

    const changed = (k) => prev && JSON.stringify(prev[k]) !== JSON.stringify(d[k]);
    const editable = phase === "review";

    if (editable) {
      const t = el("input", { type: "text", class: "title-input", value: d.title, maxlength: "120", "aria-label": "Sujet du ticket" });
      t.addEventListener("input", () => { d.title = t.value; updatePeek(); });
      const s = el("textarea", { "aria-label": "Description du problème" }); s.value = d.summary;
      s.addEventListener("input", () => { d.summary = s.value; });
      nodes.push(field("Sujet", t, { flash: changed("title") }), field("Ce qui se passe", s, { flash: changed("summary") }));
    } else {
      nodes.push(field("Sujet", el("div", { class: "value title", text: d.title }), { flash: changed("title") }),
        field("Ce qui se passe", el("div", { class: "value", text: d.summary }), { flash: changed("summary") }));
    }

    if (d.facts?.length) {
      const list = el("ul", { class: "facts" }, ...d.facts.map((f, i) => {
        if (!editable) return el("li", {}, el("span", { class: "k", text: f.label }), el("span", { text: f.value }));
        const v = el("input", { type: "text", value: f.value, "aria-label": f.label });
        v.addEventListener("input", () => { f.value = v.value; });
        return el("li", { class: "editable" }, el("span", { class: "k", text: f.label }), v,
          el("button", { type: "button", "aria-label": `Retirer « ${f.label} »`, onclick: () => { d.facts.splice(i, 1); renderSlip(); } }, icon("i-x")));
      }));
      nodes.push(field("Détails", list, { flash: changed("facts") }));
    }

    // Gêne + type
    const meter = el("div", { class: "meter", "data-level": String(urgencyBucket(d.urgency)), role: editable ? "group" : null, "aria-label": "Niveau de gêne" },
      ...URGENCY.map((u) => editable
        ? el("button", { type: "button", "aria-pressed": String(urgencyBucket(d.urgency) === u.v), onclick: () => { d.urgency = u.v; renderSlip(); }, text: u.label })
        : el("span", { class: urgencyBucket(d.urgency) === u.v ? "on" : "", text: u.label })));
    nodes.push(field("Gêne", meter, { flash: changed("urgency") }));

    const typeLabel = d.type === "demande" ? "Une demande" : "Un problème";
    const typeNode = editable
      ? el("div", { class: "seg", role: "group", "aria-label": "Type" },
        ...[["incident", "Un problème"], ["demande", "Une demande"]].map(([v, l]) =>
          el("button", { type: "button", "aria-pressed": String(d.type === v), onclick: () => { d.type = v; renderSlip(); }, text: l })))
      : el("span", { class: "tag", text: typeLabel });

    nodes.push(field("C’est…", typeNode, { flash: changed("type") }));
    if (S.ui?.suggest_category && S.cats.length) {
      if (editable) {
        const sel = el("select", { "aria-label": "Catégorie" }, el("option", { value: "", text: "Laisser le support choisir" }),
          ...S.cats.filter((c) => (d.type === "demande" ? c.is_request : c.is_incident) || c.id === d.category_id)
            .map((c) => el("option", { value: String(c.id), text: c.name, selected: c.id === d.category_id })));
        sel.addEventListener("change", () => { d.category_id = sel.value ? Number(sel.value) : null; });
        nodes.push(field("Catégorie", sel, { flash: changed("category_id") }));
      } else if (d.category_id && categoryName(d.category_id)) {
        nodes.push(field("Catégorie", el("div", { class: "value", text: categoryName(d.category_id) }), { flash: changed("category_id") }));
      }
    }
    if (S.files.length) nodes.push(field("Pièces jointes", filesList()));

    if (editable || phase === "sending") {
      const btn = el("button", { type: "button", class: "btn btn-primary btn-lg", disabled: phase === "sending", onclick: submitTicket },
        phase === "sending" ? "Envoi en cours…" : "Envoyer le ticket");
      nodes.push(el("div", { class: "slip-actions" }, btn,
        el("p", { class: "slip-note", text: "Vous pourrez suivre son avancement dans GLPI." })));
    }

    slipBody.replaceChildren(...nodes);
    finishSlip();
  }

  function sentPanel() {
    const panel = el("div", { class: "sent-panel" },
      el("h3", { text: `Ticket n° ${S.ticket.id} envoyé` }),
      el("p", { text: "Le support a reçu votre ticket. Vous n’avez rien d’autre à faire pour l’instant." }));
    if (S.ticket.warning) panel.append(el("div", { class: "warn-box", text: S.ticket.warning }));
    const acts = el("div", { class: "slip-actions" });
    acts.append(el("button", { type: "button", class: "btn btn-primary", onclick: reset, text: "Signaler un autre problème" }));
    if (S.ticket.url) acts.append(el("a", { class: "btn btn-ghost", href: S.ticket.url, target: "_blank", rel: "noopener", text: "Suivre dans GLPI" }));
    panel.append(acts);
    return panel;
  }

  function finishSlip() {
    const phase = S.phase;
    slip.querySelector(".stamp")?.remove();
    if (phase === "sent" && S.ticket) {
      slip.append(el("div", { class: "stamp", "aria-hidden": "true" }, "Envoyé", el("small", { text: `n° ${S.ticket.id}` })));
    }
    const no = $("#slipNo");
    no.textContent = S.ticket ? `N° ${S.ticket.id}` : "N° à venir";
    no.classList.toggle("on", !!S.ticket);
  }

  function filesList() {
    const editable = S.phase === "review";
    return el("div", { class: "slip-files" }, ...S.files.map((f) => editable
      ? el("span", { class: "sf" }, el("span", { title: f.file.name, text: f.file.name }),
        el("button", { type: "button", "aria-label": `Retirer ${f.file.name}`, onclick: () => removeFile(f.id) }, icon("i-x")))
      : el("span", { title: f.file.name, text: f.file.name })));
  }

  // -------------------------------------------------------------- Envoi
  async function submitTicket() {
    const d = S.draft;
    if (!d.title.trim()) { slipBody.querySelector(".title-input")?.focus(); return; }
    setPhase("sending");
    const form = new FormData();
    form.append("payload", JSON.stringify({ ...d, description: S.description, exchanges: S.exchanges }));
    S.files.forEach((f) => form.append("files", f.file, f.file.name));
    try {
      S.ticket = await api("/api/tickets", { method: "POST", form });
      setPhase("sent");
      agentSay([`C’est envoyé : ticket n° ${S.ticket.id}.`, "Le support va s’en occuper. Bonne journée !"]);
      openSheet();
    } catch (e) {
      setPhase("review");
      agentSay([e.message || "L’envoi a échoué. Réessayez dans un instant."], "agent error");
    }
  }

  function reset() {
    S.files.forEach((f) => f.url && URL.revokeObjectURL(f.url));
    Object.assign(S, { phase: "describe", description: "", exchanges: [], rounds: 0, files: [], draft: null, ticket: null, qcard: null });
    sentFileCount = 0;
    thread.replaceChildren(el("div", { class: "welcome", id: "welcome" },
      el("h1", {}, el("span", { id: "wTitle" }), el("span", { class: "l2", id: "wSub" })), el("p", { id: "wText" }), el("ul", { class: "topics", id: "topics" })));
    applyBranding();
    renderTray(); closeSheet();
    input.placeholder = PH_START();
    setPhase("describe");
    $("#slipPeek").hidden = true;
    window.scrollTo({ top: 0 });
    input.focus();
  }

  // ------------------------------------------------------- Feuille mobile
  const mobile = () => matchMedia("(max-width: 960px)").matches;
  function openSheet() { if (!mobile()) return; slipCol.classList.add("open"); scrim.hidden = false; $("#sheetClose").focus({ preventScroll: true }); }
  function closeSheet() { slipCol.classList.remove("open"); scrim.hidden = true; }
  $("#slipPeek").addEventListener("click", openSheet);
  $("#sheetClose").addEventListener("click", closeSheet);
  scrim.addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && slipCol.classList.contains("open")) closeSheet(); });

  function updatePeek() {
    const p = $("#slipPeek");
    if (!S.draft) { p.hidden = true; return; }
    p.hidden = false;
    const n = (S.draft.facts?.length || 0) + 2;
    $("#peekText").textContent = S.phase === "sent" ? `n° ${S.ticket?.id} envoyé`
      : S.phase === "review" ? "prêt à vérifier" : `${n} informations notées`;
  }

  // Konami code (ou 7 touches sur « ? ») : faux écran bleu puis tampon jusqu’au rechargement. Purement visuel.
  let eggOn = false;
  function easterEgg() {
    if (eggOn) return;
    eggOn = true;
    const pct = el("span", { text: "0" });
    const msg = el("p", {}, "Votre PC a rencontré un problème et doit redémarrer. Nous collectons simplement des informations relatives aux erreurs, puis nous allons redémarrer pour vous.");
    const bsod = el("div", { class: "bsod", role: "alert" }, el("p", { class: "face", text: ":(" }), msg,
      el("p", {}, pct, " % effectués"), el("small", { text: "Code d’arrêt : KONAMI_CODE_DETECTED" }));
    document.body.append(bsod);
    let n = 0, done = false;
    const end = () => {
      if (done) return;
      done = true;
      clearInterval(tick);
      document.removeEventListener("keydown", onEsc);
      bsod.remove();
      if (mobile()) openSheet();
      slip.querySelector(".egg-stamp")?.remove();
      const st = el("div", { class: "egg-stamp", "aria-hidden": "true" }, "Ticket légendaire", el("small", { text: "Approved by IT" }));
      slip.append(st);
      eggOn = false;
    };
    const onEsc = (e) => { if (e.key === "Escape") end(); };
    const tick = setInterval(() => {
      n = Math.min(100, n + 4 + Math.floor(Math.random() * 18));
      pct.textContent = n;
      if (n === 100) {
        clearInterval(tick);
        msg.textContent = "Avez-vous essayé de l’éteindre et de le rallumer ?";
        setTimeout(end, 2200);
      }
    }, 350);
    bsod.addEventListener("click", end);
    document.addEventListener("keydown", onEsc);
  }

  const KONAMI = "arrowup arrowup arrowdown arrowdown arrowleft arrowright arrowleft arrowright b a";
  let keys = [];
  document.addEventListener("keydown", (e) => {
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
    keys = [...keys, e.key.toLowerCase()].slice(-10);
    if (keys.join(" ") === KONAMI) { keys = []; easterEgg(); }
  });
  let taps = 0, lastTap = 0;
  $(".about-btn")?.addEventListener("click", () => {
    const now = Date.now();
    taps = now - lastTap < 1500 ? taps + 1 : 1;
    lastTap = now;
    if (taps === 7) { taps = 0; $("#about").hidePopover?.(); easterEgg(); }
  });

  init();
})();
