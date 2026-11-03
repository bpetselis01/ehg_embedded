/* PURPOSE: Behaviour of viewer/triage.html: sign-in, worklist, node detail (trend, alerts, EHR) and the alert actions.
 * INPUT: the triage API on the same origin: /ui-config.json (no login), /api/*, /fhir/* (Bearer token).
 * OUTPUT: DOM updates only. The access token is held in the variable `token` and nowhere else: not in localStorage, not in a cookie. sessionStorage holds the PKCE verifier and state during the redirect to the login page, and nothing more.
 * HOW: Text from the server is only ever inserted with textContent. SVG charts are built with createElementNS. A 401 clears the token and asks the person to sign in again. The worklist refreshes every 10 s while the tab is visible.
 * ROLE IN THESIS: Operator interface of the cloud core. Pipeline health only; it makes no clinical statement.
 * BEFORE -> AFTER: Before: nothing. After: the page described in viewer/triage.html.
 */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const REFRESH_MS = 10000;
  const ROLES = ["viewer", "operator", "admin"];
  const BAND_LABEL = { critical: "▲ Critical", warning: "◆ Warning", stable: "● Stable" };
  const RULE_LABEL = { NODE_OFFLINE: "Node silent", GATEWAY_OFFLINE: "Gateway offline", LOSS_UNRECOVERED: "Packets lost for good", LOSS_RATE: "Packet loss rate", LATENCY_P95: "High latency (p95)", FLATLINE: "Flat channel", SATURATION: "Saturated channel", SEQ_RESTART: "Sequence restarted" };
  const ACTIONS = [["ack", "Acknowledge", false], ["escalate", "Escalate", true], ["resolve", "Resolve", true], ["note", "Add note", true]];
  let cfg = null, token = null, me = null, selected = null, range = "24h", tab = "trend", timer = null, pending = null;

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    for (const k of kids) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return el;
  }
  function svg(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }
  const clear = (el) => el.replaceChildren();
  const atLeast = (r) => me && ROLES.indexOf(me.role) >= ROLES.indexOf(r);
  const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { timeZoneName: "short" }) : "n/a");
  const ruleName = (r) => RULE_LABEL[r] || r;
  function say(text, isError) {
    const n = $("notice");
    n.textContent = text || "";
    n.className = isError ? "error" : "";
  }
  function waiting(epochS) {
    if (epochS == null) return "none";
    const m = Math.max(0, Math.round((Date.now() / 1000 - epochS) / 60));
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  }

  // ------------------------------------------------------------------ sign-in
  const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  function randomString() { return b64url(crypto.getRandomValues(new Uint8Array(32))); }

  async function startLogin() {
    const verifier = randomString(), state = randomString();
    const challenge = b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
    sessionStorage.setItem("ehg.pkce", JSON.stringify({ verifier, state }));
    const q = new URLSearchParams({ response_type: "code", client_id: cfg.oidc.client_id, redirect_uri: location.origin + "/", scope: "openid", state, code_challenge: challenge, code_challenge_method: "S256" });
    location.assign(`${cfg.oidc.authority}/protocol/openid-connect/auth?${q}`);
  }

  async function finishLogin(params) {
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem("ehg.pkce")); } catch (e) { saved = null; }
    sessionStorage.removeItem("ehg.pkce");
    history.replaceState(null, "", "/");
    if (!saved || params.get("state") !== saved.state) throw new Error("The sign-in answer did not match this browser's request. Please sign in again.");
    const r = await fetch(`${cfg.oidc.authority}/protocol/openid-connect/token`, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", client_id: cfg.oidc.client_id, code: params.get("code"), redirect_uri: location.origin + "/", code_verifier: saved.verifier }),
    });
    if (!r.ok) throw new Error("Sign-in failed. Please try again.");
    const t = await r.json();
    token = t.access_token;
    setTimeout(expire, Math.max(10, (t.expires_in || 300) - 20) * 1000);
  }

  function expire() {
    token = null;
    me = null;
    clearInterval(timer);
    $("app").hidden = true;
    $("login").hidden = false;
    $("signout").hidden = true;
    $("who").textContent = "";
    say("Your session ended. Sign in again to continue.", true);
  }

  // ------------------------------------------------------------------ API
  async function api(path, opts) {
    const r = await fetch(path, { ...(opts || {}), headers: { Authorization: `Bearer ${token}`, ...((opts && opts.headers) || {}) } });
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (r.status === 401) { expire(); throw new Error("Not signed in"); }
    if (!r.ok) { const err = new Error((body && body.error) || `Request failed (${r.status})`); err.status = r.status; throw err; }
    return body;
  }

  // ------------------------------------------------------------------ charts
  function spark(values) {
    const s = svg("svg", { class: "spark", viewBox: "0 0 100 24", role: "img", "aria-label": values.length ? `Delivery over 7 days, latest ${values[values.length - 1].toFixed(1)} percent` : "No delivery history yet" });
    if (values.length > 1) {
      const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${24 - (Math.min(100, Math.max(0, v)) / 100) * 22 - 1}`).join(" ");
      s.append(svg("polyline", { points: pts }));
    }
    return s;
  }
  function chart(points, key, label, unit) {
    const vals = points.filter((p) => p[key] != null);
    const box = h("figure", {}, h("figcaption", {}, label));
    const s = svg("svg", { class: "chart", viewBox: "0 0 100 40", preserveAspectRatio: "none", role: "img", "aria-label": `${label}, ${vals.length} points` });
    s.append(svg("line", { class: "axis", x1: 0, y1: 39.5, x2: 100, y2: 39.5 }));
    if (vals.length > 1) {
      const lo = Math.min(...vals.map((p) => p[key])), hi = Math.max(...vals.map((p) => p[key])), span = hi - lo || 1;
      s.append(svg("polyline", { points: vals.map((p, i) => `${(i / (vals.length - 1)) * 100},${37 - ((p[key] - lo) / span) * 34}`).join(" ") }));
      box.append(s, h("p", {}, `Lowest ${lo.toFixed(1)} ${unit}, highest ${hi.toFixed(1)} ${unit}`));
    } else {
      box.append(s, h("p", {}, vals.length ? "Only one point so far." : "No data in this range."));
    }
    return box;
  }

  // ------------------------------------------------------------------ worklist
  async function loadWorklist() {
    try {
      const w = await api("/api/worklist");
      const body = document.querySelector("#worklist tbody");
      clear(body);
      for (const n of w.nodes) {
        const btn = h("button", { class: "link", type: "button", "data-node": n.node }, n.node);
        btn.addEventListener("click", () => select(n.node));
        const tr = h("tr", n.node === selected ? { class: "selected" } : {},
          h("td", { class: `band band-${n.band}` }, BAND_LABEL[n.band] || n.band), h("td", {}, btn), h("td", { class: "num" }, n.score), h("td", { class: "num" }, n.open_alerts),
          h("td", {}, n.rules.map(ruleName).join(", ") || "none"), h("td", {}, spark(n.sparkline || [])), h("td", {}, waiting(n.oldest_unacked)));
        body.append(tr);
      }
      $("wl-meta").textContent = `${w.nodes.length} node(s) shown, updated ${when(w.generated_at)}.` + (w.hidden_no_consent ? ` ${w.hidden_no_consent} node(s) are not shown because their subject has no active consent record.` : "");
      say("");
    } catch (e) {
      if (token) say(`Could not load the worklist: ${e.message}`, true);
    }
  }

  async function select(node) {
    selected = node;
    $("detail").hidden = false;
    await loadWorklist();
    await showDetail();
  }

  // ------------------------------------------------------------------ detail
  async function showDetail() {
    $("d-h").textContent = selected;
    const panel = $("panel");
    clear(panel);
    try {
      const d = await api(`/api/nodes/${encodeURIComponent(selected)}`);
      const reg = d.registry, hl = d.health;
      $("d-meta").textContent = `${reg ? `${reg.fs_hz} Hz, ${reg.channels} channels` : "not in the registry"}; consent: ${d.consent === null ? "no subject registered" : d.consent ? "active" : "not active"}.` + (hl ? ` Latest health: delivery ${hl.delivery_pct} %, p95 latency ${hl.p95_latency_ms} ms, missing ${hl.missing}, unrecovered ${hl.unrecovered}.` : " No health rows yet.");
      for (const t of document.querySelectorAll('[role="tab"]')) t.setAttribute("aria-selected", String(t.dataset.tab === tab));
      if (tab === "trend") await showTrend(panel);
      else if (tab === "alerts") await showAlerts(panel, d.alerts);
      else await showEhr(panel);
    } catch (e) {
      $("d-meta").textContent = "";
      panel.append(h("p", { role: "alert" }, e.status === 403 ? "This node cannot be shown: its subject has no active consent record." : `Could not load this node: ${e.message}`));
    }
  }

  async function showTrend(panel) {
    const sel = h("select", { id: "range", "aria-label": "Time range" }, ...["24h", "7d", "30d", "90d"].map((r) => { const o = h("option", { value: r }, r); if (r === range) o.selected = true; return o; }));
    sel.addEventListener("change", () => { range = sel.value; showDetail(); });
    const t = await api(`/api/nodes/${encodeURIComponent(selected)}/trend?range=${range}`);
    panel.append(h("label", {}, "Range ", sel));
    if (!t.complete) panel.append(h("p", { role: "status" }, t.covers_from ? `The data only reach back to ${when(t.covers_from)}, so this range is not fully covered.` : "There is no history for this node yet."));
    panel.append(chart(t.points, "delivery_pct", `Packet delivery (${t.bucket}), percent`, "%"), chart(t.points, "p95_latency_ms", `95th percentile latency (${t.bucket}), ms`, "ms"));
  }

  async function showAlerts(panel, alerts) {
    if (!alerts.length) { panel.append(h("p", {}, "No open alerts for this node.")); return; }
    const list = h("ul", { class: "plain" });
    for (const a of alerts) {
      const li = h("li", {}, h("strong", {}, `${ruleName(a.rule)} (${a.severity}, ${a.state})`), h("div", {}, `Opened ${when(a.opened_at)}` + (a.condition_cleared_at ? `; condition cleared ${when(a.condition_cleared_at)}` : "")));
      const row = h("div", { class: "row" });
      const hist = h("button", { type: "button" }, "History");
      hist.addEventListener("click", () => showHistory(a.id, li));
      row.append(hist);
      if (atLeast("operator")) for (const [action, label, needsNote] of ACTIONS) {
        const b = h("button", { type: "button" }, label);
        b.addEventListener("click", () => openDialog(a, action, label, needsNote));
        row.append(b);
      }
      li.append(row);
      list.append(li);
    }
    panel.append(list);
  }

  async function showHistory(id, li) {
    const old = li.querySelector(".history");
    if (old) { old.remove(); return; }
    try {
      const a = await api(`/api/alerts/${id}`);
      li.append(h("ol", { class: "history" }, ...a.events.map((e) => h("li", {}, `${when(e.ts)}: ${e.actor} (${e.role}) ${e.action}` + (e.to_state ? ` → ${e.to_state}` : "") + (e.note ? `: ${e.note}` : "")))));
    } catch (e) { say(`Could not load the history: ${e.message}`, true); }
  }

  async function showEhr(panel) {
    panel.append(h("p", {}, "Waveform windows copied to the reference FHIR server (a research stand-in for an EHR). Newest first."));
    try {
      const b = await api(`/fhir/Observation?node=${encodeURIComponent(selected)}&_count=5`);
      if (!b.entry.length) { panel.append(h("p", {}, "Nothing has been copied for this node yet.")); return; }
      panel.append(h("ul", { class: "plain" }, ...b.entry.map((e) => {
        const o = e.resource, comp = o.component || [];
        const n = comp.length ? comp[0].valueSampledData.data.split(" ").length : 0;
        return h("li", {}, `${when(o.effectivePeriod && o.effectivePeriod.start)} to ${when(o.effectivePeriod && o.effectivePeriod.end)}: ${comp.length} channel(s), ${n} samples each (raw ADC counts)`);
      })));
    } catch (e) {
      panel.append(h("p", { role: "alert" }, e.status === 404 ? "No FHIR server is configured." : e.status === 403 ? "Not shown: this node's subject has no active consent record." : e.status === 502 ? "The FHIR server is unavailable." : `Could not load: ${e.message}`));
    }
  }

  // ------------------------------------------------------------------ alert actions
  function openDialog(alert, action, label, needsNote) {
    pending = { id: alert.id, action, needsNote };
    $("dlg-h").textContent = `${label}: ${ruleName(alert.rule)} on ${alert.node}`;
    $("dlg-note").value = "";
    $("dlg-note").required = needsNote;
    $("dlg-error").textContent = "";
    $("action-dialog").showModal();
    $("dlg-note").focus();
  }
  async function submitDialog(ev) {
    ev.preventDefault();
    const note = $("dlg-note").value.trim();
    if (pending.needsNote && !note) { $("dlg-error").textContent = "A note is required for this action."; return; }
    try {
      await api(`/api/alerts/${pending.id}/${pending.action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: note || null }) });
      $("action-dialog").close();
      await loadWorklist();
      await showDetail();
    } catch (e) { $("dlg-error").textContent = e.message; }
  }

  // ------------------------------------------------------------------ start
  async function enter() {
    me = await api("/api/session");
    $("login").hidden = true;
    $("app").hidden = false;
    $("signout").hidden = false;
    $("who").textContent = `${me.user} (${me.role})`;
    $("purpose").textContent = me.purpose;
    if (me.banner) { $("dev-banner").textContent = me.banner; $("dev-banner").hidden = false; }
    await loadWorklist();
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden && token) loadWorklist(); }, REFRESH_MS);
  }

  async function boot() {
    for (const t of document.querySelectorAll('[role="tab"]')) t.addEventListener("click", () => { tab = t.dataset.tab; if (selected) showDetail(); });
    $("action-form").addEventListener("submit", submitDialog);
    $("dlg-cancel").addEventListener("click", () => $("action-dialog").close());
    $("signout").addEventListener("click", () => { token = null; location.assign("/"); });
    try { cfg = await (await fetch("/ui-config.json")).json(); } catch (e) { say("The page could not load its settings.", true); return; }
    $("purpose").textContent = cfg.purpose;
    if (cfg.banner) { $("dev-banner").textContent = cfg.banner; $("dev-banner").hidden = false; }
    const params = new URLSearchParams(location.search);
    try {
      if (cfg.mode === "oidc" && params.has("code")) { await finishLogin(params); await enter(); return; }
    } catch (e) { say(e.message, true); }
    $("login").hidden = false;
    if (cfg.mode === "dev") {
      $("dev-pick").hidden = false;
      for (const u of cfg.dev_users) $("dev-user").append(h("option", { value: u.token }, `${u.user} (${u.role})`));
      $("login-btn").addEventListener("click", async () => { token = $("dev-user").value; try { await enter(); } catch (e) { say(e.message, true); } });
    } else {
      $("login-btn").addEventListener("click", () => startLogin().catch((e) => say(e.message, true)));
    }
  }
  boot();
})();
