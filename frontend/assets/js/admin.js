/* Administration de l’assistant GLPI. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, ...children) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v; // uniquement pour des textes d’aide statiques
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(c));
    return n;
  };
  const icon = (id) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); const u = document.createElementNS("http://www.w3.org/2000/svg", "use"); u.setAttribute("href", `#${id}`); s.append(u); return s; };
  const get = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
  const set = (o, path, v) => { const ks = path.split("."); const last = ks.pop(); ks.reduce((a, k) => (a[k] ??= {}), o)[last] = v; };
  const clone = (x) => JSON.parse(JSON.stringify(x));

  async function api(path, { method = "GET", json } = {}) {
    const opt = { method, credentials: "same-origin", headers: { "X-Requested-With": "glpi-assistant" } };
    if (json !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(json); }
    const r = await fetch(path, opt);
    let data = null; try { data = await r.json(); } catch { /* vide */ }
    if (!r.ok) throw new Error(data?.detail?.message || `Erreur ${r.status}`);
    return data;
  }

  let saved = null, W = null, defaults = {}, envLocked = [];
  const ENV_FOR = { "auth.mode": "AUTH_MODE", "glpi.url": "GLPI_URL", "glpi.app_token": "GLPI_APP_TOKEN", "glpi.user_token": "GLPI_USER_TOKEN",
    "llm.base_url": "LLM_BASE_URL", "llm.api_key": "LLM_API_KEY", "llm.model": "LLM_MODEL", "llm.provider": "LLM_PROVIDER" };

  // =============================================================== Schéma
  const SECTIONS = [
    {
      id: "glpi", title: "Connexion à GLPI",
      lead: "Le ticket est créé par un compte de service ; l’utilisateur connecté en SSO est indiqué comme demandeur.",
      groups: [
        { title: "Serveur", fields: [
          { path: "glpi.url", label: "Adresse de GLPI", type: "url", placeholder: "https://support.local.lan", wide: true },
          { path: "glpi.api_mode", label: "API utilisée", type: "select", options: [
            ["auto", "Automatique (recommandé)"], ["v1", "API REST legacy — apirest.php (GLPI 10 et 11)"], ["v2", "API haut niveau v2 — api.php (GLPI 11)"]],
            help: "Automatique : l’API legacy si ses jetons sont renseignés, sinon la v2. La legacy fonctionne sur GLPI 10 et 11 et gère les pièces jointes." },
          { path: "glpi.timeout", label: "Délai d’attente (secondes)", type: "number", min: 3, max: 120 },
          { path: "glpi.verify_tls", label: "Vérifier le certificat HTTPS", type: "toggle", help: "Laissez activé : ajoutez plutôt le certificat racine de votre PKI ci-dessous." },
          { path: "glpi.ca_bundle", label: "Certificat racine de l’entreprise (chemin dans le conteneur)", type: "text", placeholder: "/certs/ca.pem",
            help: "Fichier PEM de l’autorité AD CS, monté dans le conteneur. Sert aussi pour le serveur LLM." },
        ] },
        { title: "API legacy (apirest.php)", lead: "Configuration > Générale > API : activez l’API REST et créez un client API. Le jeton utilisateur se trouve dans la fiche du compte de service, onglet Paramètres. En mode v2, ces jetons servent aussi à envoyer les pièces jointes.",
          fields: [
          { path: "glpi.app_token", label: "Jeton d’application (App-Token)", type: "secret" },
          { path: "glpi.user_token", label: "Jeton API du compte de service (user_token)", type: "secret" },
        ] },
        { title: "API v2 (OAuth)", lead: "Configuration > Clients OAuth : créez un client avec l’autorisation « Password » et le scope « api ».",
          show: (c) => c.glpi.api_mode !== "v1", fields: [
          { path: "glpi.v2_client_id", label: "Client ID", type: "text" },
          { path: "glpi.v2_client_secret", label: "Client secret", type: "secret" },
          { path: "glpi.v2_username", label: "Identifiant du compte de service", type: "text" },
          { path: "glpi.v2_password", label: "Mot de passe du compte de service", type: "secret" },
          { path: "glpi.v2_version", label: "Version d’API (facultatif)", type: "text", placeholder: "v2.2", help: "Vide : dernière version disponible." },
        ] },
        { title: "Contenu des tickets", fields: [
          { path: "glpi.entity_id", label: "Entité (ID)", type: "number", nullable: true, help: "Vide : entité par défaut du demandeur." },
          { path: "glpi.request_type_id", label: "Source de la demande (ID)", type: "number", nullable: true, help: "Ex. l’ID de la source « Helpdesk ». Vide : valeur par défaut de GLPI." },
          { path: "glpi.fallback_requester_id", label: "Demandeur de repli (ID utilisateur)", type: "number", nullable: true,
            help: "Utilisé si l’utilisateur SSO n’existe pas dans GLPI. Vide : la création est refusée avec un message clair." },
          { path: "glpi.title_prefix", label: "Préfixe des titres", type: "text", placeholder: "[Assistant] " },
          { path: "glpi.use_user_location", label: "Reprendre le lieu de l’utilisateur", type: "toggle" },
          { path: "glpi.only_helpdesk_categories", label: "Seulement les catégories visibles dans l’interface simplifiée", type: "toggle" },
          { path: "ui.glpi_ticket_url", label: "Lien vers un ticket", type: "text", wide: true, help: "<code>{id}</code> est remplacé par le numéro du ticket." },
        ] },
      ],
      test: "glpi",
    },
    {
      id: "llm", title: "Modèle IA",
      lead: "Serveur local compatible OpenAI (vLLM, llama.cpp, LM Studio, Ollama /v1…) ou API native d’Ollama.",
      groups: [
        { title: "Serveur", fields: [
          { path: "llm.provider", label: "Type d’API", type: "select", options: [["openai", "Compatible OpenAI (/v1/chat/completions)"], ["ollama", "Ollama natif (/api/chat)"]] },
          { path: "llm.base_url", label: "Adresse", type: "url", placeholder: "https://llm.local.lan/v1",
            help: "Compatible OpenAI : terminez par <code>/v1</code>. Ollama natif : <code>http://hôte:11434</code>." },
          { path: "llm.api_key", label: "Clé d’API (si exigée)", type: "secret" },
          { path: "llm.model", label: "Modèle", type: "model", help: "« Tester » remplit la liste des modèles disponibles." },
          { path: "llm.verify_tls", label: "Vérifier le certificat HTTPS", type: "toggle" },
        ] },
        { title: "Génération", fields: [
          { path: "llm.temperature", label: "Température", type: "range", min: 0, max: 1, step: 0.05, help: "Bas = réponses régulières. 0,1 à 0,3 convient bien." },
          { path: "llm.max_tokens", label: "Longueur maximale de réponse (jetons)", type: "number", min: 200, max: 8000 },
          { path: "llm.timeout", label: "Délai d’attente (secondes)", type: "number", min: 10, max: 600 },
          { path: "llm.json_mode", label: "Forcer une réponse JSON", type: "toggle", help: "Désactivez si votre serveur refuse <code>response_format</code> / <code>format</code>." },
        ] },
      ],
      test: "llm",
    },
    {
      id: "agent", title: "Comportement de l’agent",
      lead: "Ce que l’assistant demande, combien de fois, et sur quel ton. Le format technique de réponse est géré par l’application.",
      groups: [
        { title: "Questions", fields: [
          { path: "agent.max_rounds", label: "Nombre maximal de séries de questions", type: "number", min: 0, max: 5, help: "0 : le ticket est rédigé directement." },
          { path: "agent.max_questions", label: "Questions par série", type: "number", min: 1, max: 5 },
          { path: "agent.system_prompt", label: "Personnalité et mission", type: "textarea", wide: true, tall: true, reset: "system_prompt" },
          { path: "agent.rules", label: "Informations indispensables selon le cas", type: "textarea", wide: true, tall: true, reset: "rules",
            help: "Une ligne par type de problème. L’agent ne pose une question que si l’information manque." },
        ] },
        { title: "Ticket produit", fields: [
          { path: "agent.suggest_category", label: "Proposer une catégorie", type: "toggle" },
          { path: "agent.include_transcript", label: "Joindre le message d’origine et les réponses", type: "toggle", help: "Recommandé : le technicien garde les mots exacts de l’utilisateur." },
          { path: "agent.footer_note", label: "Mention en bas du ticket", type: "text", wide: true },
        ] },
      ],
      custom: ["categories", "sandbox"],
    },
    {
      id: "ui", title: "Page d’accueil",
      lead: "Les textes et raccourcis que voient les utilisateurs.",
      groups: [
        { title: "Identité", fields: [
          { path: "ui.app_name", label: "Nom de l’outil", type: "text" },
          { path: "ui.company", label: "Société", type: "text" },
          { path: "ui.accent", label: "Couleur principale", type: "color" },
          { path: "ui.logo_url", label: "Logo (URL ou /assets/…)", type: "text", placeholder: "/assets/logo.svg" },
          { path: "ui.support_contact", label: "Contact d’urgence", type: "text", placeholder: "Poste 1234", help: "Affiché si la connexion échoue." },
        ] },
        { title: "Accueil", fields: [
          { path: "ui.welcome_title", label: "Titre", type: "text", help: "<code>{prenom}</code> est remplacé par le prénom." },
          { path: "ui.welcome_subtitle", label: "Seconde ligne du titre", type: "text" },
          { path: "ui.welcome_text", label: "Texte d’introduction", type: "textarea", wide: true },
        ] },
      ],
      custom: ["topics"],
    },
    {
      id: "attachments", title: "Pièces jointes",
      lead: "Les captures d’écran collées avec Ctrl + V sont nommées automatiquement.",
      groups: [{ title: "Limites", fields: [
        { path: "attachments.max_files", label: "Nombre maximal de fichiers", type: "number", min: 0, max: 20 },
        { path: "attachments.max_mb", label: "Taille maximale par fichier (Mo)", type: "number", min: 1, max: 100,
          help: "Restez sous la limite d’upload de GLPI (et de PHP : <code>upload_max_filesize</code>)." },
        { path: "attachments.extensions", label: "Extensions acceptées", type: "list", wide: true, help: "Séparées par des virgules, sans point." },
      ] }],
    },
    {
      id: "auth", title: "Accès",
      lead: "Identification des utilisateurs et droits d’administration.",
      groups: [
        { title: "Connexion", fields: [
          { path: "auth.mode", label: "Méthode", type: "select", options: [
            ["kerberos", "Kerberos / SPNEGO (SSO Windows)"], ["proxy_header", "En-tête transmis par un reverse proxy"], ["dev", "Développement (utilisateur fixe)"]] },
          { path: "auth.service_principal", label: "Principal de service", type: "text", placeholder: "HTTP@assistance.local.lan",
            show: (c) => c.auth.mode === "kerberos", help: "Vide : toute clé du keytab est acceptée." },
          { path: "auth.proxy_header", label: "Nom de l’en-tête", type: "text", show: (c) => c.auth.mode === "proxy_header" },
          { path: "auth.dev_user", label: "Utilisateur simulé", type: "text", show: (c) => c.auth.mode === "dev" },
          { path: "auth.strip_realm", label: "Retirer le domaine (jdoe@LOCAL.LAN → jdoe)", type: "toggle" },
          { path: "auth.lowercase", label: "Convertir le login en minuscules", type: "toggle" },
          { path: "auth.session_hours", label: "Durée de session (heures)", type: "number", min: 1, max: 168 },
        ] },
        { title: "Administrateurs", fields: [
          { path: "auth.admin_users", label: "Logins autorisés à ouvrir cette page", type: "list", wide: true, help: "Séparés par des virgules. Votre propre login est toujours conservé." },
        ] },
      ],
    },
    { id: "events", title: "Journal", lead: "Les 300 derniers événements depuis le démarrage du service.", groups: [], custom: ["events"] },
  ];

  // ============================================================== Rendu
  const nav = $("#nav"), main = $("#main");
  const panels = {};

  function renderAll() {
    nav.replaceChildren(...SECTIONS.map((s) => el("li", {}, el("a", { href: `#${s.id}`, "data-id": s.id }, s.title,
      s.test ? el("span", { class: "pill", id: `pill-${s.test}`, "aria-hidden": "true" }) : null))));
    main.replaceChildren();
    for (const s of SECTIONS) {
      const p = el("section", { class: "panel", id: `panel-${s.id}`, "aria-labelledby": `h-${s.id}` },
        el("h1", { id: `h-${s.id}`, text: s.title }), el("p", { class: "lead", text: s.lead }));
      if (s.id === "auth" && W.auth.mode === "dev") p.append(el("div", { class: "callout danger", text: "Mode développement actif : tout visiteur est connecté sous l’utilisateur simulé. Ne l’utilisez jamais en production." }));
      for (const g of s.groups) {
        if (g.show && !g.show(W)) continue;
        const box = el("div", { class: "group" }, el("h2", { text: g.title }), g.lead ? el("p", { class: "lead", text: g.lead }) : null);
        const fields = el("div", { class: "fields" });
        for (const f of g.fields) if (!f.show || f.show(W)) fields.append(renderField(f));
        box.append(fields);
        p.append(box);
      }
      if (s.test === "glpi") p.append(testGlpiBox());
      if (s.test === "llm") p.append(testLlmBox());
      for (const c of s.custom || []) p.append(CUSTOM[c]());
      main.append(p);
      panels[s.id] = p;
    }
    route();
  }

  function renderField(f) {
    const id = `f-${f.path.replace(/\./g, "-")}`;
    const val = get(W, f.path);
    const envKey = ENV_FOR[f.path];
    const locked = envKey && envLocked.includes(envKey);
    const wrap = el("div", { class: `f${f.wide ? " wide" : ""}` });
    const help = f.help ? el("span", { class: "help", html: f.help }) : null;
    const onVal = (v) => { set(W, f.path, v); dirty(); if (f.path.endsWith(".mode") || f.path.endsWith("api_mode")) rerender(); };
    let input;

    switch (f.type) {
      case "toggle": {
        input = el("input", { type: "checkbox", id, role: "switch", checked: !!val, disabled: locked });
        input.addEventListener("change", () => onVal(input.checked));
        wrap.append(el("label", { class: "switch", for: id }, input, el("span", { class: "txt" }, el("b", { text: f.label }), help)));
        return wrap;
      }
      case "select":
        input = el("select", { id, disabled: locked }, ...f.options.map(([v, l]) => el("option", { value: v, text: l, selected: v === val })));
        input.addEventListener("change", () => onVal(input.value));
        break;
      case "textarea":
        input = el("textarea", { id, class: f.tall ? "tall" : null, disabled: locked }); input.value = val ?? "";
        input.addEventListener("input", () => onVal(input.value));
        break;
      case "number":
        input = el("input", { type: "number", id, value: val ?? "", min: f.min, max: f.max, disabled: locked });
        input.addEventListener("input", () => onVal(input.value === "" ? (f.nullable ? null : 0) : Number(input.value)));
        break;
      case "range": {
        const out = el("output", { for: id, text: String(val) });
        input = el("input", { type: "range", id, value: val, min: f.min, max: f.max, step: f.step });
        input.addEventListener("input", () => { out.textContent = input.value; onVal(Number(input.value)); });
        wrap.append(el("label", { for: id, text: f.label }), el("div", { class: "range-row" }, input, out));
        if (help) wrap.append(help);
        return wrap;
      }
      case "color":
        input = el("input", { type: "color", id, value: val || "#1E5E58" });
        input.addEventListener("input", () => onVal(input.value));
        break;
      case "list":
        input = el("input", { type: "text", id, value: (val || []).join(", "), disabled: locked });
        input.addEventListener("input", () => onVal(input.value.split(",").map((x) => x.trim()).filter(Boolean)));
        break;
      case "secret":
        input = el("input", { type: "password", id, value: val ?? "", autocomplete: "new-password", disabled: locked,
          placeholder: val ? "" : "Non renseigné" });
        input.addEventListener("focus", () => { if (input.value === "••••••••") input.select(); });
        input.addEventListener("input", () => onVal(input.value));
        break;
      case "model": {
        input = el("input", { type: "text", id, value: val ?? "", list: "modelList", disabled: locked, placeholder: "ex. qwen3:32b" });
        input.addEventListener("input", () => onVal(input.value));
        wrap.append(el("label", { for: id, text: f.label }), input, el("datalist", { id: "modelList" }));
        if (help) wrap.append(help);
        if (locked) wrap.append(el("span", { class: "locked", text: `Défini par la variable d’environnement ${envKey}.` }));
        return wrap;
      }
      default:
        input = el("input", { type: f.type === "url" ? "url" : "text", id, value: val ?? "", placeholder: f.placeholder, disabled: locked });
        input.addEventListener("input", () => onVal(input.value));
    }
    if (f.placeholder && !input.placeholder) input.placeholder = f.placeholder;
    const head = el("div", { class: "f-head" }, el("label", { for: id, text: f.label }));
    if (f.reset) head.append(el("button", { type: "button", class: "btn-quiet", text: "Texte par défaut",
      onclick: () => { input.value = defaults[f.reset]; onVal(input.value); } }));
    wrap.append(head, input);
    if (help) wrap.append(help);
    if (locked) wrap.append(el("span", { class: "locked", text: `Défini par la variable d’environnement ${envKey}.` }));
    return wrap;
  }

  function rerender() { const y = window.scrollY; renderAll(); window.scrollTo(0, y); }

  // ======================================================= Tests GLPI / LLM
  function testGlpiBox() {
    const probe = el("input", { type: "text", placeholder: "Login à rechercher (par défaut : le vôtre)", "aria-label": "Login à rechercher" });
    const res = el("div", { "aria-live": "polite" });
    const btn = el("button", { type: "button", class: "btn btn-primary", text: "Tester la connexion" });
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "Test en cours…"; res.replaceChildren();
      try {
        const r = await api("/api/admin/test/glpi", { method: "POST", json: { glpi: W.glpi, probe_login: probe.value.trim() || undefined } });
        pill("glpi", r.ok);
        if (!r.ok) { res.replaceChildren(el("div", { class: "result ko", text: r.error })); return; }
        const i = r.info, pr = i.probe || {};
        res.replaceChildren(el("div", { class: "result ok" }, el("h3", { text: "Connexion réussie" }), dl([
          ["API", i.api], ["Version GLPI", i.version], ["Compte de service", i.service_account], ["Profil actif", i.profile],
          ["Catégories lues", i.categories], ["Pièces jointes", i.attachments],
          ["Utilisateur « " + pr.login + " »", pr.found ? `trouvé : ${pr.name} (ID ${pr.id})${pr.location ? `, ${pr.location}` : ""}` : "introuvable dans GLPI"],
        ])));
      } catch (e) { res.replaceChildren(el("div", { class: "result ko", text: e.message })); }
      finally { btn.disabled = false; btn.textContent = "Tester la connexion"; }
    });
    return el("div", { class: "group" }, el("h2", { text: "Vérifier" }),
      el("p", { class: "lead", text: "Le test utilise les valeurs affichées, même non enregistrées." }),
      el("div", { class: "actions-row" }, probe, btn), res);
  }

  function testLlmBox() {
    const res = el("div", { "aria-live": "polite" });
    const btn = el("button", { type: "button", class: "btn btn-primary", text: "Tester le modèle" });
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "Test en cours…"; res.replaceChildren();
      try {
        const r = await api("/api/admin/test/llm", { method: "POST", json: { llm: W.llm } });
        pill("llm", r.ok);
        const dlist = $("#modelList");
        if (dlist && r.models) dlist.replaceChildren(...r.models.map((m) => el("option", { value: m })));
        const rows = [["Modèles disponibles", r.models ? `${r.models.length} : ${r.models.slice(0, 8).join(", ")}${r.models.length > 8 ? "…" : ""}` : r.models_error]];
        if (r.latency_ms != null) rows.push(["Temps de réponse", `${(r.latency_ms / 1000).toFixed(1).replace(".", ",")} s`], ["JSON valide", r.json_ok ? "oui" : "non — désactivez peut-être le mode JSON ou changez de modèle"]);
        const box = el("div", { class: `result ${r.ok ? "ok" : "ko"}` }, el("h3", { text: r.ok ? "Le modèle répond" : "Échec" }), dl(rows));
        if (r.error) box.append(el("pre", { text: r.error }));
        if (r.sample) box.append(el("pre", { text: r.sample }));
        res.replaceChildren(box);
      } catch (e) { res.replaceChildren(el("div", { class: "result ko", text: e.message })); }
      finally { btn.disabled = false; btn.textContent = "Tester le modèle"; }
    });
    return el("div", { class: "group" }, el("h2", { text: "Vérifier" }),
      el("p", { class: "lead", text: "Liste les modèles et envoie une requête courte." }), el("div", { class: "actions-row" }, btn), res);
  }

  const dl = (rows) => el("dl", {}, ...rows.filter(([, v]) => v != null && v !== "").flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]));
  function pill(id, ok) { const p = $(`#pill-${id}`); if (p) p.className = `pill ${ok ? "ok" : "ko"}`; }

  // ======================================================= Blocs spéciaux
  const CUSTOM = {
    categories() {
      const list = el("div", { class: "cat-list" }, el("p", { class: "empty", text: "Chargez les catégories depuis GLPI." }));
      const count = el("span", { class: "cat-count" });
      const search = el("input", { type: "search", placeholder: "Filtrer…", "aria-label": "Filtrer les catégories" });
      let cats = [];
      const sel = () => new Set(W.agent.allowed_category_ids || []);
      const updateCount = () => { const n = sel().size; count.textContent = n ? `${n} catégorie(s) proposée(s)` : "Toutes les catégories sont proposées"; };
      const draw = () => {
        const q = search.value.toLowerCase(), s = sel();
        const rows = cats.filter((c) => !q || c.name.toLowerCase().includes(q)).map((c) => {
          const cb = el("input", { type: "checkbox", checked: s.has(c.id) });
          cb.addEventListener("change", () => { const cur = sel(); cb.checked ? cur.add(c.id) : cur.delete(c.id); W.agent.allowed_category_ids = [...cur]; dirty(); updateCount(); });
          const usage = [c.is_incident && "incident", c.is_request && "demande"].filter(Boolean).join(" / ") + (c.helpdesk ? "" : " · masquée");
          return el("label", {}, cb, el("span", { text: c.name }), el("span", { class: "usage", text: usage }));
        });
        list.replaceChildren(...(rows.length ? rows : [el("p", { class: "empty", text: "Aucune catégorie." })]));
      };
      search.addEventListener("input", draw);
      const load = el("button", { type: "button", class: "btn btn-ghost", text: "Charger depuis GLPI" });
      load.addEventListener("click", async () => {
        load.disabled = true;
        try {
          const r = await api("/api/admin/glpi/categories", { method: "POST", json: { glpi: W.glpi } });
          if (!r.ok) throw new Error(r.error);
          cats = r.categories; draw();
        } catch (e) { list.replaceChildren(el("p", { class: "empty", text: e.message })); }
        finally { load.disabled = false; }
      });
      const none = el("button", { type: "button", class: "btn-quiet", text: "Tout décocher", onclick: () => { W.agent.allowed_category_ids = []; dirty(); updateCount(); draw(); } });
      updateCount();
      return el("div", { class: "group" }, el("h2", { text: "Catégories proposées à l’agent" }),
        el("p", { class: "lead", text: "Aucune case cochée : toutes les catégories visibles sont proposées. Moins de catégories = suggestions plus justes." }),
        el("div", { class: "cat-tools" }, search, load, none, count), list);
    },

    sandbox() {
      const ta = el("textarea", { "aria-label": "Message d’exemple", placeholder: "ex. mon imprimante marche pas" });
      ta.value = "l’imprimante du 2e fait n’importe quoi depuis ce matin";
      const res = el("div", { "aria-live": "polite" });
      const btn = el("button", { type: "button", class: "btn btn-primary", text: "Essayer" });
      btn.addEventListener("click", async () => {
        if (isDirty()) { toast("Enregistrez d’abord : l’essai utilise la configuration enregistrée.", false); return; }
        btn.disabled = true; btn.textContent = "L’agent réfléchit…"; res.replaceChildren();
        try {
          const r = await api("/api/admin/test/agent", { method: "POST", json: { description: ta.value } });
          if (!r.ok) throw new Error(r.error);
          const x = r.result, t = x.ticket;
          const box = el("div", { class: "result ok" },
            el("h3", { text: x.status === "need_info" ? "L’agent pose des questions" : "L’agent rédige directement le ticket" }),
            dl([["Message", x.message], ["Temps", `${(r.latency_ms / 1000).toFixed(1).replace(".", ",")} s`], ["Titre", t.title], ["Type", t.type], ["Urgence", t.urgency], ["Catégorie", t.category_id]]));
          if (x.questions.length) box.append(el("ol", { class: "preview-q" }, ...x.questions.map((q) => el("li", {}, q.text,
            q.options.length ? el("div", { class: "opts" }, ...q.options.map((o) => el("span", { text: o }))) : null))));
          box.append(el("pre", { text: JSON.stringify(x, null, 2) }));
          res.replaceChildren(box);
        } catch (e) { res.replaceChildren(el("div", { class: "result ko", text: e.message })); }
        finally { btn.disabled = false; btn.textContent = "Essayer"; }
      });
      return el("div", { class: "group" }, el("h2", { text: "Essayer l’agent" }),
        el("p", { class: "lead", text: "Écrivez comme un utilisateur pressé, et regardez ce que l’agent en fait." }),
        el("div", { class: "f wide" }, ta), el("div", { class: "actions-row" }, btn), res);
    },

    topics() {
      const rows = el("div", { class: "topic-rows" });
      const draw = () => {
        const T = W.ui.quick_topics;
        rows.replaceChildren(...T.map((t, i) => {
          const mk = (k, cls, ph) => { const inp = el("input", { type: "text", class: cls, value: t[k] || "", placeholder: ph, "aria-label": ph }); inp.addEventListener("input", () => { t[k] = inp.value; dirty(); }); return inp; };
          const move = (d) => { const j = i + d; if (j < 0 || j >= T.length) return; [T[i], T[j]] = [T[j], T[i]]; dirty(); draw(); };
          const up = el("button", { type: "button", class: "icon-btn", "aria-label": "Monter", onclick: () => move(-1) }, icon("i-up"));
          const rm = el("button", { type: "button", class: "icon-btn", "aria-label": "Supprimer", onclick: () => { T.splice(i, 1); dirty(); draw(); } }, icon("i-x"));
          return el("div", { class: "topic-row" }, mk("emoji", "emoji", "Emoji"), mk("label", "", "Libellé du bouton"), mk("prompt", "prompt", "Début de phrase inséré"), el("div", { class: "row-btns" }, up, rm));
        }));
      };
      draw();
      const add = el("button", { type: "button", class: "btn btn-ghost", text: "Ajouter un raccourci",
        onclick: () => { W.ui.quick_topics.push({ emoji: "💡", label: "", prompt: "" }); dirty(); draw(); rows.lastElementChild?.querySelector("input:nth-child(2)")?.focus(); } });
      return el("div", { class: "group" }, el("h2", { text: "Raccourcis d’accueil" }),
        el("p", { class: "lead", text: "Un clic insère le début de phrase dans la zone de saisie. Gardez-en 4 à 8." }),
        rows, el("div", { class: "actions-row" }, add));
    },

    events() {
      const body = el("tbody");
      const fmt = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "medium" });
      const KIND = { sso: "Connexion", agent: "Agent", ticket: "Ticket", llm: "Modèle IA", glpi: "GLPI", admin: "Administration" };
      const load = async () => {
        try {
          const ev = await api("/api/admin/events");
          body.replaceChildren(...(ev.length ? ev.map((e) => el("tr", {},
            el("td", { class: "when", text: fmt.format(new Date(e.ts * 1000)) }),
            el("td", {}, el("span", { class: `lvl ${e.level}`, "aria-hidden": "true" }), KIND[e.kind] || e.kind),
            el("td", { text: e.user || "—" }), el("td", { text: e.detail })))
            : [el("tr", {}, el("td", { colspan: "4", class: "empty", text: "Aucun événement pour l’instant." }))]));
        } catch (e) { body.replaceChildren(el("tr", {}, el("td", { colspan: "4", class: "empty", text: e.message }))); }
      };
      load();
      return el("div", { class: "group" },
        el("div", { class: "actions-row", style: "margin:0 0 12px" }, el("button", { type: "button", class: "btn btn-ghost", text: "Actualiser", onclick: load })),
        el("div", { class: "events-wrap" }, el("table", { class: "events" },
          el("thead", {}, el("tr", {}, el("th", { text: "Quand" }), el("th", { text: "Type" }), el("th", { text: "Utilisateur" }), el("th", { text: "Détail" }))), body)));
    },
  };

  // ======================================================= Navigation
  function route() {
    const id = (location.hash.slice(1) || "glpi");
    const target = panels[id] ? id : "glpi";
    Object.entries(panels).forEach(([k, p]) => p.classList.toggle("active", k === target));
    nav.querySelectorAll("a").forEach((a) => a.setAttribute("aria-current", String(a.dataset.id === target)));
  }
  window.addEventListener("hashchange", () => { route(); main.focus({ preventScroll: true }); window.scrollTo(0, 0); });

  // ======================================================= Enregistrement
  const savebar = $("#savebar");
  const isDirty = () => JSON.stringify(W) !== JSON.stringify(saved);
  function dirty() { savebar.hidden = !isDirty(); }
  $("#discardBtn").addEventListener("click", () => { W = clone(saved); dirty(); rerender(); });
  $("#saveBtn").addEventListener("click", async () => {
    const b = $("#saveBtn"); b.disabled = true; b.textContent = "Enregistrement…";
    try {
      const r = await api("/api/admin/config", { method: "PUT", json: W });
      saved = r.config; W = clone(saved); dirty(); rerender();
      toast("Configuration enregistrée.");
    } catch (e) { toast(e.message, false); }
    finally { b.disabled = false; b.textContent = "Enregistrer"; }
  });
  window.addEventListener("beforeunload", (e) => { if (W && isDirty()) { e.preventDefault(); e.returnValue = ""; } });

  let toastTimer;
  function toast(text, ok = true) {
    const t = $("#toast"); t.textContent = text; t.className = `toast${ok ? "" : " ko"}`; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3800);
  }

  // ======================================================= Démarrage
  (async () => {
    try {
      const r = await api("/api/admin/config");
      saved = r.config; W = clone(saved); defaults = r.defaults; envLocked = r.env_locked || [];
      if (/^#[0-9a-f]{6}$/i.test(saved.ui.accent || "")) document.documentElement.style.setProperty("--accent", saved.ui.accent);
      renderAll();
    } catch (e) {
      main.replaceChildren(el("p", { class: "loading", text: `Impossible de charger la configuration : ${e.message}` }));
    }
  })();
})();
