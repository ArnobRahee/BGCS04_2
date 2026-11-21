/* Team Travel Planner front end. Vanilla JS single-page app; talks only to the Flask API.
   Layout follows the Figma Make design (sidebar + topbar + pages). Sign-in uses Supabase Auth
   in the browser (publishable key). Demo mode uses fake tokens. */
const CFG = window.APP_CONFIG;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sb = !CFG.demo && window.supabase ? window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseKey) : null;
let demoToken = null;
try { demoToken = CFG.demo ? sessionStorage.getItem("demoToken") : null; } catch (_) {}
let state = freshState();
function freshState() { return { me: null, role: null, trips: [], team: null, venues: [], feed: [], ncaa: null, prefill: null, tripId: null }; }

/* ---------- formatting ---------- */
const money = (n) => Number(n || 0).toLocaleString("en-US", { style: "currency", currency: "USD" });
const money0 = (n) => Number(n || 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const label = (t) => String(t || "").replace(/_/g, " ");
const cap = (t) => label(t).replace(/^./, (c) => c.toUpperCase());
const toDate = (s) => (s instanceof Date ? s : new Date(s));
const fmt = (s, o) => (s ? toDate(s).toLocaleString("en-US", o) : "");
const when = (s) => fmt(s, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const dayShort = (s) => fmt(s, { month: "short", day: "numeric" });
const timeOnly = (s) => fmt(s, { hour: "numeric", minute: "2-digit" });
const dowTime = (s) => (s ? `${fmt(s, { weekday: "short" }).toUpperCase()} · ${timeOnly(s)}` : "");
const addMin = (s, m) => new Date(toDate(s).getTime() + (m || 0) * 60000);
const daysUntil = (s) => Math.max(0, Math.ceil((toDate(s) - Date.now()) / 86400000));
const initials = (name) => String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
const cityOf = (address) => { const p = String(address || "").split(",").map((x) => x.trim()).filter(Boolean); return p.length >= 3 ? p[p.length - 2] : p.length === 2 ? p[1].replace(/\s*\d.*$/, "") : ""; };
const gmapsLink = (lat, lng) => `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
const stars = (n) => "★".repeat(n || 0);
const plannerOnlyNote = () => `Only the main planner${state.me && state.me.main_planner ? ", " + state.me.main_planner.name + "," : ""} can book hotels, buses and restaurants.`;

/* Hotel rating: Google rating for real hotels, star class for catalog hotels. */
function hotelRating(h) {
  if (h.rating) return el("small", { class: "g-rating", title: "Google rating" }, `★ ${Number(h.rating).toFixed(1)}`, h.user_rating_count ? ` (${Number(h.user_rating_count).toLocaleString()})` : "");
  return h.stars ? el("small", { "aria-label": `${h.stars} star` }, stars(h.stars)) : null;
}
/* Google photo of the hotel, with the photographer credit Google requires. */
function hotelPhoto(h, cls = "") {
  const author = h.photo_author || (h.photo && h.photo.author) || "";
  const authorUri = h.photo_author_uri || (h.photo && h.photo.author_uri) || "";
  const img = el("img", { src: h.photo_url, alt: h.name, loading: "lazy" });
  const box = el("figure", { class: ("hotel-photo " + cls).trim() }, img,
    el("figcaption", {}, author ? ["Photo: ", authorUri ? el("a", { href: authorUri, target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, author) : author, " · "] : null, "Google Maps"));
  img.addEventListener("error", () => box.replaceWith(el("div", { class: "hotel-art art-1" }, el("span", {}, "Photo unavailable"))));
  return box;
}
let gmapsPromise = null;
function loadGoogleMaps() {
  if (window.google && window.google.maps && window.google.maps.importLibrary) return Promise.resolve(window.google.maps);
  if (!gmapsPromise) gmapsPromise = new Promise((resolve, reject) => {
    window.__gmapsReady = () => resolve(window.google.maps);
    const sc = document.createElement("script");
    sc.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(CFG.mapsKey)}&loading=async&callback=__gmapsReady`;
    sc.async = true;
    sc.onerror = () => { gmapsPromise = null; reject(new Error("Google Maps failed to load")); };
    document.head.append(sc);
  });
  return gmapsPromise;
}
const statusText = (s) => ({ PLANNING: "Planning", AWAITING_APPROVAL: "Needs approval", BOOKED: "Booked", CANCELLED: "Cancelled", COMPLETED: "Completed", IN_PROGRESS: "On the road", DRAFT: "Draft" }[s] || s);
const isCoach = () => state.role === "coach";
const teamName = () => (state.me && state.me.team && state.me.team.name) || "Our team";

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "value") value = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) node.append(kid.nodeType ? kid : String(kid));
  if (value !== undefined) node.value = value;
  return node;
}

const kidsOf = (kids) => kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false).map((k) => (k.nodeType ? k : String(k)));
function fill(node, ...kids) { node.replaceChildren(...kidsOf(kids)); return node; }
function add(node, ...kids) { node.append(...kidsOf(kids)); return node; }

let flashTimer;
function flash(msg, kind = "info") {
  const f = $("#flash");
  $("#flash-text").textContent = msg || "";
  f.className = "flash " + kind;
  f.hidden = !msg;
  clearTimeout(flashTimer);
  if (msg && kind !== "error") flashTimer = setTimeout(() => (f.hidden = true), 6000);
}
$("#flash-close").addEventListener("click", () => ($("#flash").hidden = true));

async function token() {
  if (CFG.demo) return demoToken;
  if (!sb) return null;
  const { data } = await sb.auth.getSession();
  return data.session ? data.session.access_token : null;
}

async function api(path, opts = {}) {
  const t = await token();
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* Browser cache for slow-changing GETs (NCAA data). Falls back to the network if storage is unavailable. */
// Browser cache, keyed by the selected team so one team's NCAA data never shows on another team.
const cacheKey = (path) => `cache:${(state.me && state.me.profile && state.me.profile.team_id) || "-"}:${path}`;
async function cached(path, minutes) {
  const key = cacheKey(path);
  try {
    const hit = JSON.parse(localStorage.getItem(key) || "null");
    if (hit && hit.until > Date.now()) return hit.data;
  } catch (_) { /* storage blocked or corrupt */ }
  const data = await api(path);
  try { localStorage.setItem(key, JSON.stringify({ until: Date.now() + minutes * 60000, data })); }
  catch (_) { try { for (const k of Object.keys(localStorage)) if (k.startsWith("cache:")) localStorage.removeItem(k); } catch (__) {} }
  return data;
}
const forget = (path) => { try { localStorage.removeItem(cacheKey(path)); } catch (_) {} };

async function busy(button, fn) {
  const old = button ? [...button.childNodes] : null;
  if (button) { button.disabled = true; button.textContent = "Working..."; }
  try { await fn(); } catch (e) { flash(e.message, "error"); }
  finally { if (button && button.isConnected) { button.disabled = false; button.replaceChildren(...old); } }
}

function showView(name) {
  for (const v of ["auth", "onboard", "app"]) $("#view-" + v).hidden = v !== name;
  if (name !== "app") closeChat();
}

/* ---------- modal ---------- */
function modal(title, body, { wide = false, eyebrow = "TEAM TRAVEL PLANNER" } = {}) {
  const previous = document.activeElement;
  const box = el("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": "modal-title" },
    el("div", { class: "modal-head" }, el("div", {}, el("span", { class: "eyebrow" }, eyebrow), el("h2", { id: "modal-title" }, title)),
      el("button", { type: "button", class: "icon-btn", "aria-label": "Close dialog", onclick: () => close() }, "×")),
    body);
  const wrap = el("div", { class: "modal-wrap", onmousedown: (e) => { if (e.target === wrap) close(); } }, box);
  const key = (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Tab") {
      const items = $$("button,input,select,textarea,[href]", box).filter((x) => !x.disabled);
      if (!items.length) return;
      if (e.shiftKey && document.activeElement === items[0]) { e.preventDefault(); items[items.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === items[items.length - 1]) { e.preventDefault(); items[0].focus(); }
    }
  };
  function close() { document.removeEventListener("keydown", key); wrap.remove(); if (previous && previous.focus) previous.focus(); }
  document.addEventListener("keydown", key);
  $("#modal-root").append(wrap);
  ($("input,select,textarea", box) || $(".modal-head button", box)).focus();
  return { close, box };
}

function confirmModal(title, text, okLabel, danger = false) {
  return new Promise((resolve) => {
    let m;
    const ok = el("button", { type: "button", class: danger ? "danger" : "primary", onclick: () => { m.close(); resolve(true); } }, okLabel);
    const cancel = el("button", { type: "button", class: "secondary", onclick: () => { m.close(); resolve(false); } }, "Keep it");
    m = modal(title, el("div", {}, el("p", { class: "modal-copy" }, text), el("div", { class: "row between" }, cancel, ok)));
  });
}

/* ================= sign in ================= */
/* The Rally landing screen. "Sign in as ..." opens a sign-in pop-up. While Supabase isn't configured
   (demo deployments) the pop-up is a preview: its form explains that sign-in isn't switched on, and
   its "Demo mode" button opens the demo for that role. With Supabase configured it signs in for real. */
const ROLE_INFO = {
  planner: { name: "Team Planner", tag: "Build the trip. Bring the team.", icon: "i-clipboard", demo: "demo-coach" },
  player: { name: "Player", tag: "Know the plan. Focus on your game.", icon: "i-shirt", demo: "demo-player" },
};
const roleName = (r) => (r === "coach" ? "Team Planner" : "Player");
const svgIcon = (id, cls = "ic") => {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  const u = document.createElementNS(ns, "use"); u.setAttribute("href", "#" + id); s.append(u);
  return s;
};

function setupAuth() {
  $$("[data-signin]").forEach((b) => b.addEventListener("click", () => openSignIn(b.dataset.signin, "signin")));
  $$("[data-create]").forEach((b) => b.addEventListener("click", () => openSignIn(b.dataset.create, "create")));
  $$("[data-demo]").forEach((b) => b.addEventListener("click", () => useDemo(b.dataset.demo)));
  $("#demo-strip").hidden = !CFG.demo;
}

function openSignIn(roleKey, mode = "signin") {
  const role = ROLE_INFO[roleKey] || ROLE_INFO.planner;
  const live = !CFG.demo && !!sb;                    // real Supabase sign-in available?
  const redirectTo = window.location.origin + window.location.pathname;
  const body = el("div", { class: "rl-signin" });
  let m;
  const note = el("p", { class: "rl-note", role: "status", hidden: true });
  const say = (text, kind = "info") => { note.textContent = text; note.className = "rl-note " + kind; note.hidden = !text; };
  const notYet = () => say("Sign-in isn't switched on for this demo yet. Use Demo mode below to look around.", "info");

  const demoBtn = CFG.demo ? el("button", { type: "button", class: "rl-demo-btn", onclick: () => { m.close(); useDemo(role.demo); } },
    svgIcon("i-play"), `Demo mode · ${role.name}`) : null;

  function draw() {
    const creating = mode === "create";
    const name = el("input", { name: "full_name", autocomplete: "name", placeholder: "Your name", required: true });
    const email = el("input", { name: "email", type: "email", autocomplete: "email", placeholder: "name@school.edu", required: true });
    const pass = el("input", { name: "password", type: "password", minlength: 6, required: true, placeholder: creating ? "6+ characters" : "Your password",
      autocomplete: creating ? "new-password" : "current-password" });
    const submit = el("button", { type: "submit", class: "rl-primary" }, creating ? "Create account" : "Sign in");
    const form = el("form", { class: "rl-form", novalidate: !live },
      creating ? el("label", {}, "Full name", name) : null,
      el("label", {}, "School email", email),
      el("label", {}, el("span", { class: "rl-label-row" }, "Password",
        creating ? null : el("button", { type: "button", class: "rl-link small", onclick: () => (live ? say("Password reset isn't set up yet. Ask your team planner.") : notYet()) }, "Forgot password?")), pass),
      submit);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!live) return notYet();
      busy(submit, async () => {
        try { sessionStorage.setItem("wantedRole", roleKey); } catch (_) {}
        if (creating) {
          const { data, error } = await sb.auth.signUp({ email: email.value, password: pass.value, options: { emailRedirectTo: redirectTo, data: { full_name: name.value } } });
          if (error) throw error;
          if (!data.session) return inbox(email.value);
        } else {
          const { error } = await sb.auth.signInWithPassword({ email: email.value, password: pass.value });
          if (error && /not confirmed/i.test(error.message)) return inbox(email.value);
          if (error) throw error;
        }
        m.close(); flash(""); boot();
      });
    });
    const google = el("button", { type: "button", class: "rl-google" }, el("b", {}, "G"), "Continue with Google");
    google.addEventListener("click", async () => {
      if (!live) return notYet();
      try { sessionStorage.setItem("wantedRole", roleKey); } catch (_) {}
      const { error } = await sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo } });
      if (error) say(error.message, "error");
    });
    fill(body,
      el("div", { class: "rl-signin-head" }, el("span", { class: "rl-role-ic" + (roleKey === "planner" ? " dark" : "") }, svgIcon(role.icon)),
        el("div", {}, el("b", {}, creating ? `Create a ${role.name} account` : `Sign in as ${role.name === "Player" ? "a Player" : "a Team Planner"}`), el("small", {}, role.tag))),
      google, el("div", { class: "rl-or" }, el("span", {}, "or with your school email")), form, note,
      demoBtn ? el("div", { class: "rl-demo-row" }, demoBtn, el("small", {}, "Explore with sample data. Nothing is saved.")) : null,
      el("p", { class: "rl-switch" }, creating ? "Already on Rally? " : "New to Rally? ",
        el("button", { type: "button", class: "rl-link", onclick: () => { mode = creating ? "signin" : "create"; say(""); draw(); } }, creating ? "Sign in" : "Create an account")),
      !live ? el("p", { class: "rl-preview" }, "Preview: real sign-in turns on once the team's Supabase account is connected.") : null);
  }
  function inbox(address) {
    fill(body, el("div", { class: "success-box", "aria-hidden": "true" }, "✉"),
      el("p", { class: "modal-copy" }, "We sent a confirmation link to ", el("b", {}, address), ". Open it on this device to finish creating your account."),
      el("button", { type: "button", class: "rl-primary", onclick: (e) => busy(e.currentTarget, async () => {
        const { error } = await sb.auth.resend({ type: "signup", email: address, options: { emailRedirectTo: redirectTo } });
        if (error) throw error; flash("Sent. Check your inbox.", "ok");
      }) }, "Resend email"));
  }
  draw();
  m = modal(mode === "create" ? "Join Rally" : "Welcome back", body, { eyebrow: "RALLY" });
  m.box.classList.add("rl-modal");
}

/* Returning from the confirmation email (or Google): Supabase puts the session or an error in the
   URL hash. supabase-js reads it; we then tidy the URL so the page router takes over. */
function authReturn() {
  const h = location.hash;
  if (!/access_token=|error_description=/.test(h)) return;
  const params = new URLSearchParams(h.replace(/^#/, ""));
  if (params.get("error_description")) flash(params.get("error_description").replace(/\+/g, " ") + ". Try signing in again.", "error");
  else if (params.get("type") === "signup") flash("Email confirmed. Welcome to Rally!", "ok");
  history.replaceState(null, "", location.pathname + "#/home");
}

function useDemo(tok) {
  demoToken = tok;
  try { sessionStorage.setItem("demoToken", tok); } catch (_) {}
  state = freshState();
  boot();
}

async function signOut() {
  if (sb) await sb.auth.signOut();
  demoToken = null;
  try { sessionStorage.removeItem("demoToken"); } catch (_) {}
  state = freshState();
  $("#profile-menu").hidden = true;
  flash("");
  history.replaceState(null, "", location.pathname);
  showView("auth");
}
$("#signout").addEventListener("click", signOut);
$("#onboard-signout").addEventListener("click", signOut);

/* ================= onboarding ================= */
function dietBoxes(container, selected = []) {
  fill(container, ...CFG.dietTags.map((t) =>
    el("label", { class: "check" }, el("input", { type: "checkbox", value: t, checked: selected.includes(t) }), label(t))));
}
const checkedDiets = (container) => $$("input:checked", container).map((i) => i.value);

function setupOnboard() {
  const form = $("#onboard-form");
  dietBoxes($(".diet-boxes", form));
  // Home campus from its name (OpenStreetMap), so nobody starts with another school's location.
  $("#home-find").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    const list = await api("/api/geocode?q=" + encodeURIComponent(form.home_name.value.trim()));
    if (!list.length) return fill($("#home-found"), el("span", { class: "warn-text" }, "No match. Add the city, or type the coordinates."));
    const r = list[0];
    form.home_lat.value = r.lat; form.home_lng.value = r.lng;
    fill($("#home-found"), el("span", { class: "ok-text" }, `📍 ${r.address}`), " · ",
      el("a", { href: gmapsLink(r.lat, r.lng), target: "_blank", rel: "noopener" }, "check on Google Maps ↗"));
  }));
  form.addEventListener("change", () => {
    const role = form.role.value;
    $$("[data-for]", form).forEach((d) => (d.hidden = d.dataset.for !== role));
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    data.diet_tags = checkedDiets($(".diet-boxes", form));
    busy($("button[type=submit]", form), async () => {
      await api("/api/onboard", { method: "POST", body: data });
      flash("");
      boot();
    });
  });
}

/* ================= boot + shell ================= */
async function boot() {
  const t = await token();
  authReturn();
  if (!t) return showView("auth");
  try {
    state.me = await api("/api/me");
  } catch (e) {
    if (e.status === 401) { flash("Please sign in.", "info"); return showView("auth"); }
    showView("auth");
    return flash(e.message, "error");
  }
  const p = state.me.profile;
  if (!p || !p.team_id) {
    const form = $("#onboard-form");
    try { if (sessionStorage.getItem("wantedRole") === "player") { form.role.value = "player"; form.dispatchEvent(new Event("change")); } } catch (_) {}
    const nameInput = form.full_name;
    if (!nameInput.value && state.me.user.name) nameInput.value = state.me.user.name;
    return showView("onboard");
  }
  state.role = p.role;
  showView("app");
  renderShell();
  loadChat();
  if (!location.hash || location.hash === "#") location.hash = "#/home";
  else route();
  refreshBadges();
}

const NAV = {
  coach: [["home", "Home", "⌂"], ["trips", "My Trips", "▣"], ["plan", "Plan a Trip", "＋"], ["itinerary", "Itinerary", "≡"],
    ["team", "Team", "◎"], ["announcements", "Announcements", "◫"], ["ncaa", "NCAA", "◆"], ["settings", "Settings", "⚙"]],
  player: [["home", "Home", "⌂"], ["trips", "My Trips", "▣"], ["itinerary", "Itinerary", "≡"], ["announcements", "Announcements", "◫"],
    ["ncaa", "NCAA", "◆"], ["settings", "Profile", "◎"]],
};

function renderShell() {
  const p = state.me.profile, team = state.me.team || {};
  fill($("#nav"), ...NAV[state.role].map(([id, text, icon]) =>
    el("button", { type: "button", class: "nav-link", "data-page": id, onclick: () => go(id) },
      el("span", { class: "nav-ico", "aria-hidden": "true" }, icon), el("span", { class: "nav-label" }, text),
      id === "announcements" ? el("b", { class: "nav-dot", id: "unread-dot", hidden: true }) : null)));
  paintIdentity();
  $("#avatar").textContent = initials(p.full_name);
  $("#who-name").textContent = p.full_name;
  $("#who-role").textContent = roleName(p.role);
  $("#who-email").textContent = state.me.user.email || "";
  $("#role-toggle").hidden = $("#demo-label").hidden = !CFG.demo;
  $$("[data-demo-switch]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.demoSwitch === demoToken));
  $("#chat-input").placeholder = isCoach() ? "e.g. Which package is closest to the stadium?" : "e.g. When does the bus leave?";
  $("#assistant-sub").textContent = isCoach() ? "Prepares bookings · you approve them" : "Answers from your published trip info";
}

/* ---------- selected team identity: one source (state.me.team.identity) for every team label ---------- */
const ident = () => (state.me && state.me.team && state.me.team.identity) || null;
function teamBadge(i, cls) {
  const box = el("span", { class: cls, "aria-hidden": "true" }, i ? i.initials : "?");
  if (i && i.logo) {                                  // NCAA's own logo for this school; initials if it fails
    const img = el("img", { src: i.logo, alt: "", loading: "lazy" });
    img.addEventListener("load", () => { box.textContent = ""; box.classList.add("has-logo"); box.append(img); });
    img.addEventListener("error", () => img.remove());
  }
  return box;
}
function paintIdentity() {
  const i = ident();
  const sportLine = i && i.linked ? [i.sport_name, i.division_name].filter(Boolean).join(" · ") : "NCAA program not linked yet";
  $("#brand-mark").replaceWith(Object.assign(teamBadge(i, "brand-mark"), { id: "brand-mark" }));
  $("#brand-team").textContent = i ? [i.display_name, i.linked ? i.sport_name : null].filter(Boolean).join(" · ") : "No team selected";
  $("#team-mark").replaceWith(Object.assign(teamBadge(i, "n-mark"), { id: "team-mark" }));
  $("#team-title").textContent = i ? i.display_name : "No team selected";
  $("#team-sub").textContent = sportLine;
  fill($("#season"), el("span", {}, `${i ? i.season : new Date().getFullYear()} SEASON`), el("strong", {}, i ? i.display_name : "No team selected"),
    el("small", {}, i && i.linked ? `NCAA: ${i.university} ${i.sport_name}` : isCoach() ? "Link your NCAA program on the Team page" : "Player account"));
  const sw = $(".team-switch");
  sw.classList.toggle("can-switch", (state.me.teams || []).length > 1);
  sw.title = (state.me.teams || []).length > 1 ? "Switch team" : "";
}

// Switch the selected team (server checks membership), then reload everything team-specific.
async function switchTeam(teamId, message) {
  await api("/api/teams/select", { method: "POST", body: { team_id: teamId } });
  await refreshTeam(message);
}
async function refreshTeam(message) {
  ["/api/ncaa/my-team", "/api/suggestions"].forEach(forget);
  state.ncaa = null; state.trips = null; state.tripId = null; state.prefill = null;
  try { sessionStorage.removeItem("rally-trip-draft"); } catch (_) {}
  state.me = await api("/api/me");
  renderShell();
  loadChat();
  if (message) flash(message, "ok");
  if (location.hash.replace(/^#\/?/, "") === "home") route(); else go("home");
}
function openTeamMenu() {
  const teams = state.me.teams || [];
  if (teams.length < 2) return;
  const m = modal("Switch team", el("div", { class: "team-menu" }, teams.map((t) => el("button", {
    type: "button", class: t.selected ? "current" : "", disabled: t.selected,
    onclick: () => { m.close(); switchTeam(t.team_id, `Now planning for ${t.display_name}.`); } },
    teamBadge(t, "n-mark"), el("span", {}, el("b", {}, t.display_name), el("small", {}, t.linked ? `${t.sport_name} · ${t.division_name}` : "NCAA program not linked"))))),
    { eyebrow: "YOUR TEAMS" });
}
$(".team-switch").addEventListener("click", openTeamMenu);

async function refreshBadges() {
  if (isCoach()) return;
  try {
    state.feed = await api("/api/announcements");
    const n = state.feed.filter((a) => !a.read).length;
    const dot = $("#unread-dot");
    if (dot) { dot.textContent = n; dot.hidden = !n; }
  } catch (_) {}
}

$$("[data-demo-switch]").forEach((b) => b.addEventListener("click", () => { if (b.dataset.demoSwitch !== demoToken) { location.hash = "#/home"; useDemo(b.dataset.demoSwitch); } }));
$("#avatar-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const m = $("#profile-menu"); m.hidden = !m.hidden;
  $("#avatar-btn").setAttribute("aria-expanded", !m.hidden);
});
document.addEventListener("click", (e) => { if (!e.target.closest("#profile-menu")) $("#profile-menu").hidden = true; });
$$("[data-go]").forEach((b) => b.addEventListener("click", () => { $("#profile-menu").hidden = true; go(b.dataset.go); }));
$("#collapse-btn").addEventListener("click", () => {
  const on = document.body.classList.toggle("collapsed");
  try { localStorage.setItem("sidebarCollapsed", on ? "1" : ""); } catch (_) {}
  $("#collapse-btn .nav-ico").textContent = on ? "›" : "‹";
  $("#collapse-btn").setAttribute("aria-label", on ? "Expand sidebar" : "Collapse sidebar");
});
try { if (localStorage.getItem("sidebarCollapsed")) { document.body.classList.add("collapsed"); $("#collapse-btn .nav-ico").textContent = "›"; } } catch (_) {}
function setMobileMenu(open) {
  document.body.classList.toggle("menu-open", open);
  $("#scrim").hidden = !open;
  $("#mobile-menu").setAttribute("aria-expanded", open);
}
$("#mobile-menu").addEventListener("click", () => setMobileMenu(!document.body.classList.contains("menu-open")));
$("#scrim").addEventListener("click", () => setMobileMenu(false));

/* ================= router ================= */
function go(path) {
  const h = "#/" + path;
  if (location.hash === h) route(); else location.hash = h;
}
window.addEventListener("hashchange", () => { if (state.me && state.role) route(); });

const PAGES = {
  home: () => (isCoach() ? coachHome() : playerHome()),
  trips: (id) => (isCoach() ? (id ? coachTrip(id) : coachTrips()) : playerTrips()),
  plan: () => (isCoach() ? planTrip() : playerTrips()),
  itinerary: (id) => itinerary(id),
  team: () => (isCoach() ? teamPage() : settingsPage()),
  announcements: () => announcementsPage(),
  ncaa: () => ncaaPage(),
  settings: () => settingsPage(),
};

let routeSeq = 0;
async function route() {
  const [page = "home", arg] = location.hash.replace(/^#\/?/, "").split("/");
  const fn = PAGES[page] || PAGES.home;
  const seq = ++routeSeq;
  state.tripId = page === "trips" && arg ? arg : state.tripId;
  $$("#nav .nav-link").forEach((b) => {
    const on = b.dataset.page === (PAGES[page] ? page : "home");
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  setMobileMenu(false);
  const main = $("#page");
  fill(main, el("div", { class: "loading" }, "Loading..."));
  try {
    const content = await fn(arg ? decodeURIComponent(arg) : undefined);
    if (seq !== routeSeq) return;
    fill(main, content);
    window.scrollTo(0, 0);
  } catch (e) {
    if (seq !== routeSeq) return;
    if (e.status === 401) { showView("auth"); return flash("Your session ended. Please sign in again.", "info"); }
    fill(main, el("div", { class: "page" }, el("div", { class: "empty" }, el("b", {}, "Couldn't load this page"), el("p", {}, e.message),
      el("button", { type: "button", class: "secondary", onclick: route }, "Try again"))));
  }
}
const intro = (eyebrow, title, text, ...actions) => el("div", { class: "page-intro" },
  el("div", {}, el("span", { class: "eyebrow" }, eyebrow), el("h1", {}, title), text ? el("p", {}, text) : null),
  actions.length ? el("div", { class: "header-buttons" }, actions) : null);

/* ---------- shared data ---------- */
async function loadTrips() { state.trips = await api("/api/trips"); return state.trips; }
const byDate = (a, b) => toDate(a.match_at) - toDate(b.match_at);
const upcoming = (trips) => trips.filter((t) => t.status !== "CANCELLED" && toDate(t.match_at) > Date.now() - 86400000).sort(byDate);
const pastOrCancelled = (trips) => trips.filter((t) => !upcoming(trips).includes(t)).sort(byDate).reverse();
const travelers = (t) => t.num_players + t.num_coaches + t.num_staff;
const chosenPackage = (t) => (t.packages || []).find((p) => p.package_no === (t.selected_package_no || (t.ai_recommendation || {}).package_no)) || (t.packages || [])[0];

function statusClass(t) { return t.status === "AWAITING_APPROVAL" ? "warn" : t.status === "BOOKED" ? "ok" : t.status === "CANCELLED" ? "bad" : ""; }
function statusPill(t) {
  const txt = t.status === "BOOKED" && !t.published ? "Booked · not published" : t.status === "PLANNING" && !isCoach() ? "Being planned" : statusText(t.status);
  return el("em", { class: "pill s-" + t.status }, txt);
}

function tripList(trips, onOpen) {
  return el("div", { class: "compact-list" }, trips.map((t) => el("button", { type: "button", onclick: () => onOpen(t) },
    el("b", {}, `${fmt(t.match_at, { month: "short" }).toUpperCase()} ${fmt(t.match_at, { day: "2-digit" })}\n${timeOnly(t.match_at)}`),
    el("span", {}, el("strong", {}, `at ${t.opponent}`), el("small", {}, [t.venue_name, cityOf(t.venue_address)].filter(Boolean).join(" · "))),
    statusPill(t), el("i", { "aria-hidden": "true" }, "→"))));
}

/* ================= COACH: home ================= */
async function coachHome() {
  const trips = await loadTrips();
  const next = upcoming(trips)[0];
  const full = next ? await api(`/api/trips/${next.id}`) : null;
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const waiting = trips.filter((t) => t.status === "AWAITING_APPROVAL").length;
  const planning = trips.filter((t) => t.status === "PLANNING").length;
  const unpublished = trips.filter((t) => t.status === "BOOKED" && !t.published).length;
  const todo = [waiting && `${waiting} booking request${waiting > 1 ? "s" : ""} waiting for your approval`,
    planning && `${planning} trip${planning > 1 ? "s" : ""} still choosing a package`,
    unpublished && `${unpublished} booked trip${unpublished > 1 ? "s" : ""} not shared with players yet`].filter(Boolean);
  const page = el("div", { class: "page" },
    intro(fmt(new Date(), { weekday: "long", month: "long", day: "numeric" }), `${greet}, ${state.me.profile.full_name}.`,
      !trips.length ? "No trips planned yet." : todo.length ? todo.join(" · ") + "." : "Everything is up to date.",
      el("button", { type: "button", class: "primary", onclick: () => { state.prefill = null; go("plan"); } }, "＋ Plan new trip")));
  // Upcoming NCAA games are suggestions, never trips: nothing is saved until the planner confirms in the wizard.
  const suggest = el("section", { class: full ? "suggest compact" : "suggest" });
  add(page, full ? nextTripCard(full) : suggest);
  const pulse = el("aside", { class: "activity" });
  add(page, el("div", { class: "home-grid" },
    el("section", {}, full ? suggest : null,
      el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "Planned trips"), el("h2", {}, "On the road")),
        trips.length ? el("button", { type: "button", class: "text-btn", onclick: () => go("trips") }, "All trips →") : null),
    upcoming(trips).length ? tripList(upcoming(trips), (t) => go("trips/" + t.id)) : el("p", { class: "sub" }, trips.length ? "No upcoming trips." : "No trips planned yet.")),
    pulse));
  gameSuggestions(suggest, { compact: !!full });
  teamPulse(pulse, full);
  return page;
}

/* Upcoming away games -> suggestions. The server filters (future, away, no trip yet, opponent and date
   known); Aivana only chooses which listed game to plan next. Nothing here creates a trip. */
async function gameSuggestions(box, { compact = false } = {}) {
  const head = (h, p) => el("div", {}, el("span", { class: "eyebrow" }, ident() ? ident().display_name.toUpperCase() + (ident().sport_name ? " · " + ident().sport_name.toUpperCase() : "") : "YOUR TEAM"),
    el("h2", {}, h), p ? el("p", { class: "sub" }, p) : null);
  if (!compact) fill(box, head("Your next away game is coming up.", "You haven't planned any team travel yet. Start with an upcoming game and let Aivana help you compare travel options."),
    el("p", { class: "sub" }, "Loading your schedule..."));
  else fill(box);
  let s;
  try { s = await cached("/api/suggestions", 5); }
  catch (e) {
    if (compact) return;
    return fill(box, head("Plan your next away trip.", "Couldn't load the NCAA schedule: " + e.message), customButtons(true));
  }
  const games = s.games || [];
  if (!games.length) {
    if (compact) return;
    const why = s.warning || (s.upcoming_away === 0 ? "No upcoming away games found." : "Every upcoming away game already has a trip.");
    return fill(box, head(s.warning ? "Plan your next away trip." : "No upcoming away games found.", why), customButtons(true));
  }
  const pick = games.find((g) => s.pick && g.game_id === s.pick.game_id) || games[0];
  const fromAivana = s.pick && s.pick.source === "aivana";
  const detail = (g) => [
    ["Opponent", g.opponent_full || g.opponent],
    ["Date & time", (g.start_time && !/TBA|TBD/i.test(g.start_time)) ? when(g.match_at) : `${g.start_date} · time not announced`],
    ["Venue", g.missing.includes("venue") ? `${g.venue_name} · venue to confirm` : g.venue_name],
    ["Destination", g.missing.includes("venue") ? `${g.opponent_full || g.opponent} (home game)` : g.venue_name]];
  const plan = (g) => { state.prefill = { ...g.plan, from_suggestion: true, missing: g.missing }; go("plan"); };
  const card = el("div", { class: "suggest-card" },
    el("div", { class: "suggest-facts" }, detail(pick).map(([k, v]) => el("span", {}, el("small", {}, k), v))),
    s.pick ? el("p", { class: "suggest-why" }, el("b", {}, fromAivana ? "✦ Aivana: " : "Next up: "), s.pick.reason,
      s.pick.note ? el("span", { class: "hint" }, " " + s.pick.note) : null) : null,
    pick.missing.length ? el("p", { class: "warn-text" }, `Still needed from you: ${pick.missing.join(", ")}.`) : null,
    el("div", { class: "row" },
      el("button", { type: "button", class: "primary", onclick: () => plan(pick) }, fromAivana ? "✦ Plan with Aivana" : "Plan this trip"),
      el("button", { type: "button", class: "secondary", onclick: () => { state.prefill = null; go("plan"); } }, "Plan a custom trip")));
  const others = games.filter((g) => g !== pick).slice(0, 4);
  fill(box, compact ? head("Suggested next trip", null) : head("Your next away game is coming up.", "You haven't planned any team travel yet. Start with an upcoming game and let Aivana help you compare travel options."),
    card,
    others.length ? el("div", { class: "suggest-more" }, el("span", { class: "eyebrow" }, "Other upcoming away games"),
      others.map((g) => el("div", { class: "suggest-row" }, el("span", {}, el("b", {}, `at ${g.opponent}`), ` · ${g.start_date}${g.missing.length ? " · needs " + g.missing.join(", ") : ""}`),
        el("button", { type: "button", class: "text-btn", onclick: () => plan(g) }, "Plan →")))) : null);
}
const customButtons = (create) => el("div", { class: "row" },
  el("button", { type: "button", class: "primary", onclick: () => { state.prefill = null; go("plan"); } }, create ? "＋ Create a trip" : "Plan a custom trip"),
  isCoach() && !(ident() && ident().linked) ? el("button", { type: "button", class: "secondary", onclick: () => go("team") }, "Link NCAA program") : null);

function nextTripCard(t) {
  const pkg = chosenPackage(t);
  const d = pkg && pkg.detail;
  const statusLine = {
    PLANNING: [`Planning · ${(t.packages || []).length} packages priced`, ""],
    AWAITING_APPROVAL: ["Bookings waiting for your approval", "warn"],
    BOOKED: [t.published ? "Booked · shared with players" : "Booked · not shared with players yet", "ok"],
  }[t.status] || [statusText(t.status), ""];
  const cta = { PLANNING: "Choose package →", AWAITING_APPROVAL: "Review & approve →", BOOKED: t.published ? "Open trip →" : "Share with players →" }[t.status] || "Open trip →";
  return el("section", { class: "next-trip" },
    el("div", { class: "game-date" }, el("span", {}, fmt(t.match_at, { month: "short" }).toUpperCase()), el("strong", {}, fmt(t.match_at, { day: "2-digit" })), el("small", {}, dowTime(t.match_at))),
    el("div", { class: "game-main" },
      el("div", { class: "status " + statusLine[1] }, el("i"), statusLine[0]),
      el("h2", {}, teamName(), el("em", {}, "at"), t.opponent),
      el("p", {}, [t.venue_name, t.venue_address].filter(Boolean).join(" · ")),
      el("div", { class: "trip-facts" },
        el("span", {}, el("small", {}, "Departure"), d ? when(d.suggested_departure) : "Not set"),
        el("span", {}, el("small", {}, "Travelers"), `${t.num_players} players · ${t.num_coaches + t.num_staff} staff`),
        el("span", {}, el("small", {}, t.selected_package_no ? "Booked total" : "Best package"), pkg ? `${money0(pkg.total_cost)}${Number(t.budget) ? " of " + money0(t.budget) : ""}` : "No packages"),
        el("span", {}, el("small", {}, "RSVPs"), `${t.rsvp_counts.going} going · ${t.rsvp_counts.maybe} maybe`))),
    el("div", { class: "trip-actions" },
      el("button", { type: "button", class: "primary", onclick: () => go("trips/" + t.id) }, cta),
      el("button", { type: "button", class: "text-btn", onclick: () => go("itinerary/" + t.id) }, "View itinerary")));
}

/* Team pulse: NCAA record/rank + next games, roster RSVPs and the latest announcement. */
async function teamPulse(box, trip) {
  fill(box, el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "Team pulse"), el("h2", {}, "This week"))),
    el("p", { class: "sub" }, "Loading NCAA info..."));
  const parts = [];
  try {
    const info = state.ncaa || await cached("/api/ncaa/my-team", 10);
    state.ncaa = info;
    parts.push(...ncaaSummary(info, { compact: true }));
  } catch (e) { parts.push(el("p", { class: "sub" }, "NCAA data unavailable: " + e.message)); }
  if (trip) {
    parts.push(el("div", { class: "activity-row" }, el("span", {}, trip.rsvp_counts.going), el("p", {}, `Going to ${trip.opponent}`,
      el("small", {}, `${trip.rsvp_counts.maybe} maybe · ${trip.rsvp_counts.not_going} not going`))));
    if ((trip.diet_tags || []).length) parts.push(el("div", { class: "activity-row" }, el("span", {}, trip.diet_tags.length),
      el("p", {}, "Dietary needs on this trip", el("small", {}, trip.diet_tags.map(label).join(", ")))));
  }
  try {
    const feed = await api("/api/announcements");
    if (feed[0]) parts.push(el("button", { type: "button", class: "announcement-preview", onclick: () => go("announcements") },
      el("span", {}, "LATEST ANNOUNCEMENT"), el("b", {}, feed[0].title), el("p", {}, feed[0].body)));
  } catch (_) {}
  fill(box, box.firstChild, ...parts);
}

/* ---------- NCAA team summary (used on home, team page, player home) ---------- */
const gameWhen = (g) => (g.start_epoch ? when(new Date(g.start_epoch * 1000)) : `${g.start_date} ${g.start_time}`);
const teamLabel = (t) => (t.rank ? `#${t.rank} ` : "") + (t.short || t.full);

function ncaaSummary(info, { compact = false } = {}) {
  if (!info.linked) {
    return [el("p", { class: "sub" }, isCoach() ? "Link your NCAA program to see your record, ranking and away games." : "Your team planner hasn't linked the team to its NCAA program yet."),
      isCoach() ? el("button", { type: "button", class: "text-btn", onclick: () => go("team") }, "Link NCAA team →") : null];
  }
  const s = info.summary || {};
  const logo = el("img", { src: info.logo, alt: "", width: 44, height: 44 });
  logo.addEventListener("error", () => logo.remove());
  const games = (info.games || []).slice(0, compact ? 3 : 8);
  return [
    el("div", { class: "team-line" }, logo, el("div", {}, el("b", {}, info.name), el("small", { class: "sub" }, `${info.sport_name} · ${info.division_name}`))),
    el("div", { class: "stat-row" },
      el("div", {}, el("small", {}, "Record"), el("strong", {}, s.record || "–")),
      el("div", {}, el("small", {}, "Rank"), el("strong", { title: s.poll || "" }, s.rank ? "#" + s.rank : "NR")),
      el("div", {}, el("small", {}, "Conference"), el("strong", { title: s.conference || "" }, s.conference || "–"))),
    info.games_warning ? el("p", { class: "warn-text" }, info.games_warning) : null,
    el("ul", { class: "game-list" }, games.length ? games.map((g) => el("li", {},
      el("span", {}, el("strong", {}, `${g.is_away ? "at" : "vs"} ${teamLabel(g.opponent)}`),
        `${gameWhen(g)}${g.network ? " · " + g.network : ""}${g.state === "final" ? ` · ${g.away.score}-${g.home.score} final` : ""}`),
      g.planned_trip_id && isCoach() ? el("button", { type: "button", class: "text-btn small", onclick: () => go("trips/" + g.planned_trip_id) }, "Open trip →")
      : g.plan && isCoach() && g.state !== "final" && g.state !== "live" ? el("button", { type: "button", class: "secondary small", onclick: () => { state.prefill = { ...g.plan, missing: g.plan.venue_confirmed ? [] : ["venue"] }; go("plan"); } }, "Plan trip")
        : g.url ? el("a", { href: g.url, target: "_blank", rel: "noopener", class: "text-btn small" }, "ncaa.com") : null))
      : el("li", { class: "sub" }, info.sport === "football" ? "No game this week." : "No games in the next two weeks.")),
  ];
}

/* ================= COACH: trips ================= */
async function coachTrips() {
  const trips = await loadTrips();
  const up = upcoming(trips), past = pastOrCancelled(trips);
  const page = el("div", { class: "page" }, intro("Away games", "My trips", `${up.length} upcoming · ${past.length} past or cancelled`,
    el("button", { type: "button", class: "primary", onclick: () => { state.prefill = null; go("plan"); } }, "＋ Plan a trip")));
  if (!trips.length) add(page, el("div", { class: "empty" }, el("b", {}, "No trips yet"), el("p", {}, "Create a trip and the planner prices every hotel, meal and bus combination."),
    el("button", { type: "button", class: "primary", onclick: () => go("plan") }, "＋ Plan a trip")));
  if (up.length) add(page, el("div", { class: "section-head" }, el("h2", {}, "Upcoming")), tripList(up, (t) => go("trips/" + t.id)));
  if (past.length) add(page, el("div", { class: "section-head", style: "margin-top:34px" }, el("h2", {}, "Past and cancelled")), tripList(past, (t) => go("trips/" + t.id)));
  return page;
}

async function coachTrip(id) {
  const t = await api(`/api/trips/${id}`);
  state.tripId = t.id;
  const wrap = el("div", { class: "travel-page" });
  const brief = [`${travelers(t)} travelers`, `${t.nights} night${t.nights === 1 ? "" : "s"}`, `${t.meals} team meal${t.meals === 1 ? "" : "s"}`,
    Number(t.budget) ? `budget ${money0(t.budget)}` : "no budget"].join(" · ");
  add(wrap, el("div", { class: "travel-head" },
    el("div", {}, el("button", { type: "button", class: "back-link", onclick: () => go("trips") }, "← My trips"),
      el("h1", {}, `${t.opponent} · ${dayShort(t.match_at)}`),
      el("p", {}, `${t.venue_name}${cityOf(t.venue_address) ? ", " + cityOf(t.venue_address) : ""} · ${when(t.match_at)}`), el("p", {}, brief)),
    el("div", {}, statusPill(t), el("button", { type: "button", class: "assistant-btn", onclick: openChat }, "✦ Refine with Aivana"))));
  if (t.status === "PLANNING") add(wrap, ...planningView(t));
  else add(wrap, statusView(t));
  return wrap;
}

async function tripAction(button, t, path, body, okMsg) {
  await busy(button, async () => {
    const res = await api(`/api/trips/${t.id}/${path}`, { method: "POST", body });
    if (res.error) flash(res.error, "error"); else if (okMsg) flash(okMsg, "ok");
    route();
  });
}

/* ---------- planning: package list + map ---------- */
function planningView(t) {
  const rec = t.ai_recommendation;
  const pkgs = t.packages || [];
  const ui = { sort: "recommended", budget: "any", amenity: "All", selected: (rec && rec.package_no) || (pkgs[0] && pkgs[0].package_no), compare: [] };
  const minCost = Math.min(...pkgs.map((p) => p.total_cost));
  const hotelKm = pkgs.filter((p) => p.detail.hotel).map((p) => p.detail.hotel.km_to_venue);
  const minKm = hotelKm.length ? Math.min(...hotelKm) : null;
  const hotelOrder = [...new Set(pkgs.map((p) => p.detail.hotel && p.detail.hotel.id).filter(Boolean))];
  const art = (p) => "art-" + ((hotelOrder.indexOf(p.detail.hotel && p.detail.hotel.id) % 4) + 1);
  const amenities = [...new Set(pkgs.flatMap((p) => (p.detail.hotel && p.detail.hotel.amenities) || []))].sort();
  const isPick = (p) => rec && rec.package_no === p.package_no;
  const fromAivana = !!rec && rec.source === "aivana";
  // The three options are chosen in code (CostCalc.highlights); Aivana's pick is marked separately.
  const HL = { lowest_cost: "Lowest cost", closest: "Closest", best_value: "Best value" };
  const hlName = (p) => { const d = p.detail; if (!d.highlight) return "";
    if (d.highlight === "lowest_cost" && d.also_closest) return "Lowest cost · closest";
    if (d.highlight === "closest" && d.next_closest) return "Next closest";
    return HL[d.highlight]; };
  const tagOf = (p) => hlName(p) || (isPick(p) && fromAivana ? "Aivana's pick" : p.detail.hotel ? (p.detail.hotel.rating ? `★ ${Number(p.detail.hotel.rating).toFixed(1)} on Google` : p.detail.hotel.stars ? `${p.detail.hotel.stars}-star hotel` : "Hotel") : "Day trip");
  const hlRank = (p) => ({ lowest_cost: 1, closest: 2, best_value: 3 }[p.detail.highlight] || 9);

  const recBar = el("div", { class: "recommendation-bar" }, el("span", {}, fromAivana ? "AIVANA RECOMMENDS" : "BEST VALUE"),
    el("p", {}, rec ? [/^package/i.test(rec.reason) ? null : el("b", {}, `Package ${rec.package_no}. `), rec.reason, !fromAivana && rec.note ? ` ${rec.note}` : ""] : "No recommendation yet.",
      rec && rec.hotel_note ? el("span", { class: "warn-text" }, " " + rec.hotel_note) : null,
      rec && rec.hotel_source === "google" ? el("span", { class: "g-credit" }, " Hotels, photos and ratings from Google Maps; room prices are estimates.") : null,
      rec && rec.hotel_source !== "google" ? el("span", { class: "sim-inline" }, " Demo data: hotels, restaurants, room rates and bus quotes are simulated samples (the bus distance and all totals are calculated).") : null));
  const select = (name, options, value, onchange) => el("select", { "aria-label": name, value, onchange }, options.map(([v, l]) => el("option", { value: v }, l)));
  const count = el("span");
  const replan = el("button", { type: "button", class: "secondary small" }, "↻ Re-price with RSVPs");
  replan.addEventListener("click", () => tripAction(replan, t, "replan", {}, "Packages re-priced with the latest RSVPs."));
  const filters = el("div", { class: "filters" },
    el("label", {}, "Sort by", select("Sort by", [["recommended", "Recommended"], ["cost", "Total cost"], ["distance", "Distance"]], ui.sort, (e) => { ui.sort = e.target.value; draw(); })),
    el("label", {}, "Budget", select("Budget", [["any", "Any total"], ["within", "Within budget"]], ui.budget, (e) => { ui.budget = e.target.value; draw(); })),
    el("label", {}, "Amenities", select("Amenities", [["All", "All"], ...amenities.map((a) => [a, cap(a)])], ui.amenity, (e) => { ui.amenity = e.target.value; draw(); })),
    replan, count);
  const list = el("div", { class: "package-list" });
  const map = el("aside", { class: "map-panel" });
  const options = el("div", { class: "option-strip", role: "group", "aria-label": "Three options" });
  const compareBar = el("div", { class: "compare-bar", hidden: true });

  function visible() {
    const v = pkgs.filter((p) => (ui.budget === "any" || p.detail.within_budget) && (ui.amenity === "All" || ((p.detail.hotel && p.detail.hotel.amenities) || []).includes(ui.amenity)));
    const km = (p) => (p.detail.hotel ? p.detail.hotel.km_to_venue : 0);
    return v.sort(ui.sort === "cost" ? (a, b) => a.total_cost - b.total_cost : ui.sort === "distance" ? (a, b) => km(a) - km(b) || a.total_cost - b.total_cost
      : (a, b) => (isPick(b) - isPick(a)) || hlRank(a) - hlRank(b) || a.total_cost - b.total_cost);
  }

  function row(p, i) {
    const d = p.detail, h = d.hotel, r = d.restaurant;
    const reason = isPick(p) ? rec.reason
      : [r ? `Team meal at ${r.name} (${r.cuisine}).` : null, `${d.bus.count} × ${d.bus.company} ${d.bus.vehicle}, leaves ${when(d.suggested_departure)}.`].filter(Boolean).join(" ");
    const cb = el("input", { type: "checkbox", checked: ui.compare.includes(p.package_no), "aria-label": `Compare package ${p.package_no}` });
    cb.addEventListener("change", () => {
      ui.compare = ui.compare.includes(p.package_no) ? ui.compare.filter((x) => x !== p.package_no) : [...ui.compare, p.package_no].slice(-2);
      draw();
    });
    const selected = ui.selected === p.package_no;
    return el("article", { class: "package-row" + (selected ? " selected" : ""), onclick: () => { ui.selected = p.package_no; draw(); if (window.matchMedia("(max-width: 760px)").matches) map.scrollIntoView({ behavior: "smooth" }); } },
      el("div", { class: "hotel-image" }, h && h.photo_url ? hotelPhoto(h) : el("div", { class: "hotel-art " + art(p), title: h ? "Illustration: sample hotels have no real photos" : null }, el("span", {}, h ? `${cityOf(t.venue_address) || t.venue_name} · sample hotel` : "Day trip")), el("b", {}, tagOf(p))),
      el("div", { class: "package-copy" },
        el("div", {}, el("span", { class: "rank" }, `${String(i + 1).padStart(2, "0")} · Package ${p.package_no}${isPick(p) ? (fromAivana ? " · Aivana's pick" : " · Recommended") : ""}`),
          el("h2", {}, h ? h.name : "Day trip · no hotel", h ? hotelRating(h) : null),
          el("p", { class: "meta" }, h ? `${d.rooms} rooms · ${h.km_to_venue} km (${h.drive_min_to_venue} min) to ${t.venue_name}` : `${d.route_km} km each way`)),
        el("p", { class: "reason" }, reason),
        el("div", { class: "amenities" }, ((h && h.amenities) || []).map((a) => el("span", {}, "✓ " + label(a))),
          r && r.missing_diets && r.missing_diets.length ? el("span", { class: "warn-text" }, `⚠ no main for ${r.missing_diets.map(label).join(", ")}`) : null,
          r && r.fits_group === false ? el("span", { class: "warn-text" }, `⚠ ${r.name} seats ${r.group_capacity || "fewer"}, not all ${d.travelers}`) : null),
        el("div", { class: "package-bottom" },
          el("label", { onclick: (e) => e.stopPropagation() }, cb, "Compare"),
          el("em", { class: "pill " + (d.within_budget ? "ok" : "warn") }, d.within_budget ? "Within budget" : "Over budget"),
          el("span", {}, el("small", {}, "Team total"), el("strong", {}, money(p.total_cost))),
          el("button", { type: "button", class: selected ? "selected-btn" : "secondary" }, selected ? "Selected ✓" : "View package"))));
  }

  // Map: a real Google map (markers at real coordinates) when GOOGLE_MAPS_BROWSER_KEY is set,
  // otherwise a drawn map with pins at their real relative positions.
  const hotelsOnMap = [];
  for (const x of pkgs) if (x.detail.hotel && !hotelsOnMap.find((y) => y.hotel.id === x.detail.hotel.id)) hotelsOnMap.push({ hotel: x.detail.hotel, no: x.package_no });
  const mapHost = el("div", { class: "map", role: "region", "aria-label": `Map of ${t.venue_name} and nearby hotels` });
  const detailBox = el("div", { class: "map-detail" });
  fill(map, mapHost, detailBox);
  let gm = null;                                  // {map, hotelMarkers, foodMarker}
  const pickHotel = (x) => {
    const cur = pkgs.find((q) => q.package_no === ui.selected);
    ui.selected = cur && cur.detail.hotel && cur.detail.hotel.id === x.hotel.id ? cur.package_no : x.no;
    draw();
  };
  if (CFG.mapsKey) {
    loadGoogleMaps().then(async (maps) => {
      const { Map } = await maps.importLibrary("maps");
      const { Marker } = await maps.importLibrary("marker");
      mapHost.classList.add("gmap");
      const venue = { lat: t.venue_lat, lng: t.venue_lng };
      const gmap = new Map(mapHost, { center: venue, zoom: 13, mapTypeControl: false, streetViewControl: false, fullscreenControl: true });
      const bounds = new maps.LatLngBounds(venue);
      new Marker({ map: gmap, position: venue, title: t.venue_name, label: { text: "★", color: "#fff" }, zIndex: 50 });
      const hotelMarkers = hotelsOnMap.map((x) => {
        const pos = { lat: x.hotel.lat, lng: x.hotel.lng };
        bounds.extend(pos);
        const m = new Marker({ map: gmap, position: pos, title: x.hotel.name, label: String(x.no) });
        m.addListener("click", () => pickHotel(x));
        return { m, x };
      });
      gmap.fitBounds(bounds, 40);
      gm = { map: gmap, hotelMarkers, food: new Marker({ map: gmap, title: "Team meal", label: { text: "🍴" }, visible: false }) };
      paintGoogle();
    }).catch(() => { fill(mapHost, el("p", { class: "sub", style: "padding:20px" }, "Google Maps couldn't load. Check GOOGLE_MAPS_BROWSER_KEY and its allowed websites.")); });
  }
  function paintGoogle() {
    if (!gm) return;
    const p = pkgs.find((x) => x.package_no === ui.selected);
    const h = p && p.detail.hotel, r = p && p.detail.restaurant;
    for (const { m, x } of gm.hotelMarkers) {
      const on = h && h.id === x.hotel.id;
      m.setZIndex(on ? 40 : 10);
      m.setOpacity(on ? 1 : 0.75);
    }
    if (r) { gm.food.setPosition({ lat: r.lat, lng: r.lng }); gm.food.setVisible(true); } else gm.food.setVisible(false);
    if (h) gm.map.panTo({ lat: h.lat, lng: h.lng });
  }
  function paintDrawn(p) {
    const h = p.detail.hotel, r = p.detail.restaurant;
    const pts = [{ lat: t.venue_lat, lng: t.venue_lng }, ...hotelsOnMap.map((x) => x.hotel), ...(r ? [r] : [])];
    const k = Math.cos((t.venue_lat * Math.PI) / 180);
    const xs = pts.map((q) => q.lng * k), ys = pts.map((q) => q.lat);
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 0.005);
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    const style = (q) => `left:${50 + (84 * (q.lng * k - cx)) / span}%;top:${46 - (70 * (q.lat - cy)) / span}%`;
    fill(mapHost, el("div", { class: "river" }),
      el("div", { class: "venue-pin", style: style({ lat: t.venue_lat, lng: t.venue_lng }) }, el("b", {}, "★"), el("span", {}, t.venue_name)),
      hotelsOnMap.map((x) => el("button", { type: "button", class: "map-pin" + (h && h.id === x.hotel.id ? " active" : ""), style: style(x.hotel),
        title: x.hotel.name, "aria-label": `Select ${x.hotel.name}`, onclick: () => pickHotel(x) }, x.no)),
      r ? el("span", { class: "map-pin food", style: style(r), title: r.name, "aria-hidden": "true" }, "🍴") : null,
      el("div", { class: "map-legend" }, el("span", {}, "★ Venue"), el("span", {}, "① Hotels by package"), r ? el("span", {}, "🍴 Team meal") : null,
        el("a", { href: gmapsLink(t.venue_lat, t.venue_lng), target: "_blank", rel: "noopener" }, "Open in Google Maps ↗")));
  }

  function drawMap() {
    const p = pkgs.find((x) => x.package_no === ui.selected);
    if (!p) return fill(detailBox, el("p", { class: "sub" }, pkgs.length ? "No package matches these filters." : "Select a package."));
    const d = p.detail, h = d.hotel, r = d.restaurant;
    if (CFG.mapsKey) paintGoogle(); else paintDrawn(p);
    const canBook = state.me.is_main_planner;
    const choose = el("button", { type: "button", class: "primary full", disabled: !canBook }, isPick(p) && fromAivana ? "Request Aivana's pick" : `Request package ${p.package_no}`);
    choose.addEventListener("click", () => tripAction(choose, t, "select", { package_no: p.package_no }, "Booking requests prepared. Review and approve them."));
    fill(detailBox,
      el("span", { class: "eyebrow" }, `Selected package · ${p.package_no}`),
      h && h.photo_url ? hotelPhoto(h, "detail-photo") : null,
      el("h2", {}, h ? h.name : "Day trip · no hotel", h ? " " : null, h ? hotelRating(h) : null),
      el("p", {}, h ? [`${h.km_to_venue} km · ${h.drive_min_to_venue} min drive to ${t.venue_name} · `,
        el("a", { href: h.maps_url || gmapsLink(h.lat, h.lng), target: "_blank", rel: "noopener" }, "Google Maps ↗")] : "Bus there and back on game day"),
      h && h.address ? el("p", {}, h.address) : null,
      el("div", { class: "cost-lines" },
        h ? el("span", {}, el("em", {}, `Hotel · ${d.rooms} rooms × ${t.nights} night × ${h.price_estimated ? "~" : ""}${money(h.price_per_night)}${h.price_estimated ? " (estimate)" : ""}`), el("b", {}, money(d.costs.hotel))) : null,
        el("span", {}, el("em", {}, `Bus · ${d.bus.count} × ${d.bus.vehicle}`), el("b", {}, money(d.costs.bus))),
        r ? el("span", {}, el("em", {}, `Team meals · ${d.travelers} × ${t.meals} × ${money(r.price_per_person)}`), el("b", {}, money(d.costs.meal))) : null,
        el("strong", {}, h && h.price_estimated ? "Estimated total" : "Calculated total", el("b", {}, money(p.total_cost))),
        Number(t.budget) ? el("span", {}, el("em", {}, "Budget"), el("b", { class: d.within_budget ? "" : "warn-text" }, `${money(t.budget)} · ${d.within_budget ? "within" : "over by " + money(p.total_cost - t.budget)}`)) : null),
      h && h.price_estimated ? el("p", { class: "hint" }, "Google Maps doesn't publish room rates. The hotel price is estimated from Google's price level; confirm the real rate with the hotel.") : null,
      r ? el("div", { class: "detail-block" }, el("b", {}, "Team meal"), `${r.name} · ${cap(r.cuisine)} · ${r.km_to_venue} km from venue${r.halal_certified ? " · halal certified" : ""} · `,
        el("a", { href: r.maps_url || gmapsLink(r.lat, r.lng), target: "_blank", rel: "noopener" }, "Google Maps ↗"),
        r.missing_diets && r.missing_diets.length ? el("p", { class: "warn-text" }, `No main dish for: ${r.missing_diets.map(label).join(", ")}`) : null,
        r.fits_group === false ? el("p", { class: "warn-text" }, `Group seating for ${r.group_capacity || "fewer"} people, but ${d.travelers} are travelling. Call ahead or split the group.`) : null,
        el("button", { type: "button", class: "text-btn small", onclick: () => openMenu(r.id, r.name, t.diet_tags || []) }, "View menu →")) : null,
      el("div", { class: "detail-block" }, el("b", {}, "Bus"), `${d.bus.company} ${d.bus.vehicle} (${d.bus.seats} seats${d.bus.wifi ? ", Wi-Fi" : ""}). Leaves ${when(d.suggested_departure)}, ${d.route_km} km / ${d.drive_min} min.`,
        d.late_arrival ? el("p", { class: "warn-text" }, "Arrives later than ideal because of the departure limit.") : null),
      h && h.cancellation_policy ? el("div", { class: "detail-block" }, el("b", {}, "Cancellation"), h.cancellation_policy) : null,
      choose, el("small", {}, canBook ? "This prepares the bookings for your approval. Nothing is reserved or paid yet." : plannerOnlyNote()));
  }

  function drawCompare() {
    compareBar.hidden = !ui.compare.length;
    const btn = el("button", { type: "button", class: "primary", disabled: ui.compare.length < 2, onclick: () => compareModal(t, ui.compare.map((n) => pkgs.find((p) => p.package_no === n))) }, "Compare packages");
    fill(compareBar, el("span", {}, el("b", {}, `${ui.compare.length} selected`), el("small", {}, "Choose 2 packages to compare line by line")),
      el("div", {}, ui.compare.map((n) => { const p = pkgs.find((x) => x.package_no === n); return el("b", {}, `#${n} ${p.detail.hotel ? p.detail.hotel.name : "Day trip"}`,
        el("button", { type: "button", "aria-label": `Remove package ${n}`, onclick: () => { ui.compare = ui.compare.filter((x) => x !== n); draw(); } }, "×")); })),
      btn);
  }

  function drawOptions() {
    const picks = pkgs.filter((p) => p.detail.highlight).sort((a, b) => hlRank(a) - hlRank(b));
    options.hidden = picks.length < 2;
    fill(options, ...picks.map((p) => {
      const d = p.detail, h = d.hotel;
      const where = h ? `${h.name} · ${h.km_to_venue} km` : d.restaurant ? `Day trip · meal ${d.restaurant.km_to_venue} km from venue` : "Day trip";
      return el("button", { type: "button", class: "option-card" + (ui.selected === p.package_no ? " current" : ""), "aria-pressed": String(ui.selected === p.package_no),
        onclick: () => { ui.selected = p.package_no; if (!visible().includes(p)) { ui.budget = "any"; ui.amenity = "All"; } draw(); } },
        el("span", {}, hlName(p), isPick(p) && fromAivana ? el("em", {}, "Aivana's pick") : null),
        el("strong", {}, money0(p.total_cost)),
        el("small", {}, `Package ${p.package_no} · ${where}`),
        el("small", { class: d.within_budget ? "" : "warn-text" }, d.within_budget ? "Within budget" : "Over budget"));
    }));
  }

  function draw() {
    const v = visible();
    // Keep the map/detail in step with the list: if the filters hide the selected package,
    // select the first one still shown.
    if (!v.some((p) => p.package_no === ui.selected)) ui.selected = v.length ? v[0].package_no : null;
    drawOptions();
    count.textContent = `${v.length} of ${pkgs.length} packages`;
    fill(list, ...(v.length ? v.map(row) : [el("div", { class: "empty" }, el("b", {}, pkgs.length ? "No packages fit these filters." : "No hotel within 25 km of this venue has enough rooms."),
      el("p", {}, pkgs.length ? "Allow over-budget packages or clear the amenity filter."
        : rec && rec.hotel_source === "google" ? "Try fewer nights, fewer travelers, or plan it as a day trip (0 nights)."
        : "The demo hotel list covers the venues in the Plan a Trip menu. For other venues, switch on Google Places (GOOGLE_MAPS_API_KEY) to search real hotels, or plan it as a day trip (0 nights)."))]));
    drawMap();
    drawCompare();
  }
  draw();
  return [recBar, options, filters, el("div", { class: "discovery" }, list, map), compareBar];
}

function compareModal(t, [a, b]) {
  const cols = [a, b];
  const best = (vals, low = true) => { const m = low ? Math.min(...vals) : Math.max(...vals); return vals.map((v) => v === m && vals[0] !== vals[1]); };
  const rows = [
    ["Total", cols.map((p) => money(p.total_cost)), best(cols.map((p) => p.total_cost))],
    ["Budget", cols.map((p) => (p.detail.within_budget ? "Within budget" : "Over budget"))],
    ["Hotel", cols.map((p) => (p.detail.hotel ? `${p.detail.hotel.name} ${p.detail.hotel.rating ? "★ " + p.detail.hotel.rating + " on Google" : stars(p.detail.hotel.stars)}` : "Day trip"))],
    ["To venue", cols.map((p) => (p.detail.hotel ? `${p.detail.hotel.km_to_venue} km · ${p.detail.hotel.drive_min_to_venue} min` : "–")), best(cols.map((p) => (p.detail.hotel ? p.detail.hotel.km_to_venue : 0)))],
    ["Rooms", cols.map((p) => (p.detail.hotel ? `${p.detail.rooms} × ${money(p.detail.hotel.price_per_night)} = ${money(p.detail.costs.hotel)}` : "–"))],
    ["Amenities", cols.map((p) => ((p.detail.hotel && p.detail.hotel.amenities) || []).map(label).join(", ") || "–")],
    ["Team meal", cols.map((p) => (p.detail.restaurant ? `${p.detail.restaurant.name} (${cap(p.detail.restaurant.cuisine)}) · ${money(p.detail.costs.meal)}` : "–"))],
    ["Diets", cols.map((p) => (p.detail.restaurant && p.detail.restaurant.missing_diets.length ? "Missing " + p.detail.restaurant.missing_diets.map(label).join(", ") : "All covered"))],
    ["Bus", cols.map((p) => `${p.detail.bus.count} × ${p.detail.bus.company} · ${money(p.detail.costs.bus)}`)],
    ["Departs", cols.map((p) => when(p.detail.suggested_departure))],
    ["Cancellation", cols.map((p) => (p.detail.hotel && p.detail.hotel.cancellation_policy) || "–")],
  ];
  const m = modal("Compare packages", el("div", {},
    el("div", { class: "data-scroll", style: "border:0" }, el("table", { class: "compare-table" },
      el("thead", {}, el("tr", {}, el("th", {}), cols.map((p) => el("th", {}, `Package ${p.package_no}`)))),
      el("tbody", {}, rows.map(([name, vals, marks]) => el("tr", {}, el("th", {}, name), vals.map((v, i) => el("td", { class: marks && marks[i] ? "best" : "" }, v))))))),
    el("div", { class: "row between", style: "margin-top:18px" }, cols.map((p) => {
      const b = el("button", { type: "button", class: "primary", disabled: !state.me.is_main_planner }, `Request package ${p.package_no}`);
      b.addEventListener("click", () => { m.close(); tripAction(null, t, "select", { package_no: p.package_no }, "Booking requests prepared. Review and approve them."); });
      return b;
    }))), { wide: true, eyebrow: t.opponent.toUpperCase() });
}

/* ---------- approval / booked ---------- */
function bookingRows(rows) {
  return rows.map((b) => el("div", { class: "booking-row" }, el("em", { class: "pill s-" + b.status }, b.type), el("span", {}, b.summary), el("b", {}, money(b.amount)),
    b.confirmation_code ? el("code", { title: "Generated by the demo booking provider; no real reservation exists." }, "Simulated confirmation " + b.confirmation_code) : null));
}

// Bookings go to a mock provider: nothing is reserved or paid anywhere. Say so wherever bookings appear.
const simulatedNote = () => el("p", { class: "sim-note" }, el("b", {}, "SIMULATED"),
  " Demo booking: no hotel, bus or restaurant is contacted and nothing is paid. Confirmation codes are generated by the app.");

function statusView(t) {
  const left = el("div", {});
  if (t.status === "AWAITING_APPROVAL") {
    const pending = t.bookings.filter((b) => b.status === "PENDING_APPROVAL");
    const total = pending.reduce((s, b) => s + Number(b.amount), 0);
    const approve = el("button", { type: "button", class: "primary" }, `Approve and book · ${money(total)}`);
    approve.addEventListener("click", () => tripAction(approve, t, "approve", {}, "Booked (simulated). Assign rooms, then share the plan with players."));
    const change = el("button", { type: "button", class: "secondary" }, "Pick a different package");
    change.addEventListener("click", () => tripAction(change, t, "cancel", {}, "Request withdrawn. Choose another package."));
    if (!state.me.is_main_planner) { approve.disabled = true; change.disabled = true; }
    add(left, el("section", { class: "panel" },
      el("span", { class: "eyebrow" }, "Step 2 of 3 · Approval"), el("h2", {}, state.me.is_main_planner ? "Waiting for your approval" : "Waiting for the main planner"),
      el("p", { class: "sub" }, "Aivana and the planner only prepare bookings. Nothing is reserved until you approve. Check the cancellation policy first."),
      simulatedNote(), el("div", { style: "margin-top:14px" }, bookingRows(pending)),
      el("div", { class: "total-band" }, el("span", {}, "TOTAL TO APPROVE"), el("strong", {}, money(total)),
        el("small", {}, Number(t.budget) ? `Budget ${money(t.budget)} · ${total <= t.budget ? "within budget" : "over by " + money(total - t.budget)}` : "No budget set")),
      el("div", { class: "row", style: "margin-top:20px" }, approve, change),
      state.me.is_main_planner ? null : el("p", { class: "hint" }, plannerOnlyNote())));
  } else if (t.status === "BOOKED") {
    const confirmed = t.bookings.filter((b) => b.status === "CONFIRMED");
    const rooms = el("button", { type: "button", class: "secondary" }, "Assign rooms & buses");
    rooms.addEventListener("click", () => tripAction(rooms, t, "rooms", {}, "Rooms and buses assigned. Re-run this after RSVPs change."));
    const cancel = el("button", { type: "button", class: "danger", disabled: !state.me.is_main_planner, title: state.me.is_main_planner ? null : plannerOnlyNote() }, "Cancel bookings");
    cancel.addEventListener("click", async () => {
      if (await confirmModal("Cancel every booking?", "The hotel, bus and team meal for this trip will be cancelled. Check the cancellation policy before you continue.", "Cancel bookings", true))
        tripAction(cancel, t, "cancel", {}, "Bookings cancelled.");
    });
    add(left, el("div", { class: "publish-banner" + (t.published ? " live" : "") },
      el("b", {}, t.published ? "● SHARED WITH PLAYERS" : "○ NOT SHARED YET"),
      el("span", {}, t.published ? "Players can see the hotel, bus time, their room and the menu." : "Players only see the game until you publish the plan below.")));
    add(left, el("section", { class: "panel" }, el("span", { class: "eyebrow" }, "Step 3 of 3 · Booked"), el("h2", {}, "Confirmed bookings"),
      simulatedNote(), el("div", { style: "margin-top:10px" }, bookingRows(confirmed)),
      el("div", { class: "row", style: "margin-top:18px" }, rooms, el("button", { type: "button", class: "secondary", onclick: () => go("itinerary/" + t.id) }, "View itinerary"), cancel)));
    add(left, composerPanel(t));
  } else {
    add(left, el("section", { class: "panel" }, el("h2", {}, statusText(t.status)),
      el("p", { class: "sub" }, t.status === "CANCELLED" ? "This trip's bookings were cancelled." : ""), bookingRows(t.bookings || [])));
  }
  const right = el("div", {}, rosterPanel(t), el("section", { class: "panel" }, el("h2", {}, "Trip brief"),
    ...[["Match", when(t.match_at)], ["Venue", t.venue_name], ["Address", t.venue_address || "–"], ["Travelers", `${t.num_players} players · ${t.num_coaches} coaches · ${t.num_staff} staff`],
      ["Hotel nights", t.nights], ["Team meals", t.meals], ["Budget", Number(t.budget) ? money(t.budget) : "None"], ["Diet needs", (t.diet_tags || []).map(label).join(", ") || "None"]]
      .map(([k, v]) => el("div", { class: "kv" }, el("span", {}, k), el("b", {}, v)))));
  return el("div", { class: "status-page" }, el("div", { class: "two-col" }, left, right));
}

function composerPanel(t) {
  const title = el("input", { name: "title", required: true, placeholder: "e.g. Ohio State trip: bus leaves Saturday 3:08 PM" });
  const text = el("textarea", { rows: 7, placeholder: "Bus time, what to pack, hotel, team meal..." });
  const draft = el("button", { type: "button" }, "✦ Generate starting draft");
  const draftNote = el("p", { class: "hint draft-note", hidden: true });
  draft.addEventListener("click", () => busy(draft, async () => { const d = await api(`/api/trips/${t.id}/draft`); title.value = d.title; text.value = d.body; showDraftNote(draftNote, d); }));
  const publish = el("button", { type: "button", class: "primary" }, t.published ? "Post update to players" : "Publish plan to players");
  publish.addEventListener("click", () => {
    if (!title.value.trim()) return flash("Add a title first.", "error");
    if (t.published) busy(publish, async () => {
      await api(`/api/trips/${t.id}/announcements`, { method: "POST", body: { title: title.value, body: text.value } });
      flash("Announcement posted.", "ok"); title.value = ""; text.value = "";
    });
    else tripAction(publish, t, "publish", { title: title.value, body: text.value }, "Published. Players can now see the plan.");
  });
  if (!t.published) setTimeout(() => draft.click(), 0);
  return el("section", { class: "composer", style: "margin-top:18px" },
    el("div", { class: "composer-head" }, el("b", {}, t.published ? "Message the players" : "Tell the players"), draft),
    draftNote, el("label", { class: "field" }, "Title", title), el("label", { class: "field" }, "Message", text),
    el("div", { class: "composer-foot" }, el("span", {}, t.published ? "Goes to every player on the team." : "Publishing shows players the hotel, bus time, their room and the menu. Costs stay hidden."), publish));
}

// Says who wrote a draft: Aivana, or the app's template (when Aivana isn't connected or failed).
function showDraftNote(node, d) {
  node.hidden = false;
  node.classList.toggle("warn-text", d.source !== "aivana");
  node.textContent = d.source === "aivana" ? "✦ Drafted by Aivana from the booked package. Read it through before you send it."
    : d.note || "Fill-in template from the booked package. Edit it before sending.";
}

function rosterPanel(t) {
  const head = el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "Roster"), el("h2", {}, "Players")),
    el("span", { class: "sub" }, `${t.rsvp_counts.going} going · ${t.rsvp_counts.maybe} maybe · ${t.rsvp_counts.not_going} no`));
  if (!t.participants.length) return el("section", { class: "panel" }, head, el("p", { class: "sub" }, "No players have joined yet. Share the join code from the Team page."));
  const rsvpClass = { going: "ok", maybe: "warn", not_going: "bad" };
  return el("section", { class: "panel", style: "padding-bottom:6px" }, head, el("div", { class: "trip-roster" },
    el("div", { class: "table-row table-header" }, el("span", {}, "Player"), el("span", {}, "RSVP"), el("span", {}, "Room"), el("span", {}, "Bus")),
    t.participants.map((p) => el("div", { class: "table-row" },
      el("span", { class: "member" }, el("i", {}, initials(p.full_name)), el("b", {}, p.full_name)),
      el("span", {}, el("em", { class: "pill " + (rsvpClass[p.rsvp] || "") }, label(p.rsvp))),
      el("span", {}, p.room_no ?? "–"), el("span", {}, p.bus_no ?? "–")))));
}

/* ---------- menu dialog ---------- */
async function openMenu(rid, name, preselect = []) {
  const active = new Set(preselect.slice(0, 1));
  const chips = el("div", { class: "chips" });
  const list = el("ul", { class: "menu-items", style: "margin-top:14px" }, el("li", { class: "sub" }, "Loading..."));
  const render = async () => {
    fill(chips, el("span", { class: "sub" }, "Show dishes that are:"), ...CFG.dietTags.map((tg) => el("button", {
      type: "button", class: "chip", "aria-pressed": active.has(tg) ? "true" : "false",
      onclick: () => { active.has(tg) ? active.delete(tg) : active.add(tg); render(); } }, label(tg))));
    try {
      const items = await api(`/api/restaurants/${rid}/menu?diet=${[...active].join(",")}`);
      fill(list, ...(items.length ? items.map((m) => el("li", {},
        el("span", {}, m.name, el("small", {}, `${m.course} · ${(m.diet_tags || []).map(label).join(", ") || "no tags"}`)), el("b", {}, money(m.price))))
        : [el("li", { class: "sub" }, "Nothing on this menu matches every selected need.")]));
    } catch (e) { fill(list, el("li", { class: "sub" }, e.message)); }
  };
  modal(name, el("div", {}, chips, list), { eyebrow: "TEAM MEAL MENU" });
  render();
}

/* ================= COACH: plan a trip ================= */
async function planTrip() {
  const [team, venues] = await Promise.all([api("/api/team"), api("/api/venues")]);
  state.team = team; state.venues = venues;
  const pre = state.prefill || {};
  state.prefill = null;
  // Keep an unfinished brief if the coach navigates away (cleared once the trip is created).
  const DRAFT_KEY = "rally-trip-draft";
  let saved = null;
  const teamId = state.me.profile.team_id;
  if (!pre.opponent) { try { saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null"); } catch (_) {} }
  if (saved && saved.data && saved.data.team_id !== teamId) saved = null;      // a draft from another team
  const data = {
    team_id: pre.team_id || teamId,
    opponent: pre.opponent || "", venue_id: pre.venue_id || (pre.venue_name ? "" : (venues[0] || {}).id || ""),
    venue_name: pre.venue_name || "", venue_address: "", venue_lat: "", venue_lng: "", ncaa_game_id: pre.ncaa_game_id || "",
    match_at: pre.match_at || "", depart_after: "", num_players: team.players.length || 20, num_coaches: 3, num_staff: 2,
    nights: 1, meals: 2, budget: 9000,
  };
  if (saved && saved.data) Object.assign(data, saved.data);
  const keepDraft = () => { try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ data, step })); } catch (_) {} };
  const diets = [...new Set(team.players.flatMap((p) => p.diet_tags || []))];
  let step = (saved && saved.step) || 1, shown = step, started = false;
  const page = el("div", { class: "page narrow" });
  const stepper = el("div", { class: "stepper" });
  const shell = el("div", { class: "form-shell" });
  shell.addEventListener("input", () => keepDraft());
  add(page, el("div", { class: "page-intro" },
    el("div", {}, el("span", { class: "eyebrow" }, `${(ident() && ident().display_name || "").toUpperCase()} · ${pre.ncaa_game_id ? "NEW AWAY TRIP · FROM NCAA SCHEDULE" : "NEW AWAY TRIP"}`), el("h1", {}, "Build the travel brief"),
      el("p", {}, pre.ncaa_game_id ? "Opponent and date come from the NCAA schedule. Check them, add the travel party and budget, then generate packages. Nothing is saved until you click Generate packages."
        : "We'll use these details to price every hotel, team meal and bus combination."),
      pre.missing && pre.missing.length ? el("p", { class: "warn-text" }, `The schedule doesn't confirm the ${pre.missing.join(" or ")}. Please fill ${pre.missing.length > 1 ? "them" : "it"} in.`) : null),
    el("button", { type: "button", class: "secondary", onclick: () => { try { sessionStorage.removeItem(DRAFT_KEY); } catch (_) {} go("trips"); } }, "Cancel")), stepper, shell);

  const venueName = () => (data.venue_id ? (venues.find((v) => v.id === data.venue_id) || {}).name : data.venue_name) || "Custom venue";
  const input = (name, labelText, attrs = {}, wide = false, hint = "") => el("label", { class: wide ? "wide" : "" }, labelText,
    el("input", { name, value: data[name] ?? "", oninput: (e) => (data[name] = e.target.value), ...attrs }), hint ? el("p", { class: "hint" }, hint) : null);
  const title = (n, h, p) => el("div", { class: "form-title" }, el("i", {}, n), el("div", {}, el("h2", {}, h), el("p", {}, p)));
  const errBox = el("div", { class: "form-error", role: "alert", hidden: true });

  // Describe the trip in words; Aivana's structured output fills the form for the planner to check.
  let parsedNote = null, describeText = "";
  function describeBox() {
    if (!state.me.aivana) return null;
    const text = el("textarea", { rows: 2, value: describeText, oninput: (e) => (describeText = e.target.value), placeholder: "e.g. At Toledo on Dec 5 at 7pm. 22 players, 3 coaches, 2 staff. Leave after 2pm the day before, one night, budget $8,000." });
    const note = el("p", { class: "hint", "aria-live": "polite" }, parsedNote || "Optional: Aivana fills in the form from your description. You check everything before saving.");
    const go_ = el("button", { type: "button", class: "secondary" }, "✦ Fill with Aivana");
    go_.addEventListener("click", () => busy(go_, async () => {
      const r = await api("/api/trips/parse", { method: "POST", body: { text: text.value } });
      const f = r.fields || {};
      const names = { opponent: "opponent", match_at: "date", depart_after: "departure limit", num_players: "players", num_coaches: "coaches",
        num_staff: "staff", nights: "nights", meals: "meals", budget: "budget", venue_name: "venue" };
      const filled = Object.keys(names).filter((k) => f[k] !== undefined);
      for (const k of filled) data[k] = typeof f[k] === "number" ? String(f[k]) : f[k];
      if (f.venue_name) { data.venue_id = ""; data.venue_lat = ""; data.venue_lng = ""; autoFound = false; }
      parsedNote = filled.length ? `✦ Aivana filled: ${filled.map((k) => names[k]).join(", ")}.${f.match_time_missing ? " No game time was given, so it's set to 12:00; change it." : ""} Check every field.`
        : "Aivana didn't find trip details in that text.";
      draw();
    }));
    return el("div", { class: "describe-box" }, el("label", { class: "field" }, "Describe the trip (optional)", text), el("div", { class: "row" }, go_), note);
  }

  // "Other venue": find coordinates from the name (OpenStreetMap via /api/geocode), never ask for lat/lng first.
  let autoFound = false;
  function venueFinder() {
    const out = el("div", { class: "venue-found", "aria-live": "polite" });
    const showPicked = () => fill(out, data.venue_lat !== "" && data.venue_lng !== ""
      ? el("p", { class: "ok-text" }, "📍 ", el("b", {}, data.venue_address || data.venue_name), ` · `,
          el("a", { href: gmapsLink(data.venue_lat, data.venue_lng), target: "_blank", rel: "noopener" }, "check on Google Maps ↗"))
      : el("p", { class: "hint" }, "Type the stadium or arena name and city, then Find on map."));
    const find = el("button", { type: "button", class: "secondary" }, "Find on map");
    find.addEventListener("click", () => busy(find, async () => {
      const q = data.venue_name.trim();
      if (q.length < 3) throw new Error("Type the venue name first, e.g. Ohio Stadium Columbus.");
      const list = await api("/api/geocode?q=" + encodeURIComponent(q));
      if (!list.length) return fill(out, el("p", { class: "warn-text" }, "No match. Add the city (e.g. “Kelly/Shorts Stadium, Mount Pleasant”) or enter the coordinates below."));
      fill(out, el("p", { class: "hint" }, "Pick the right place:"), el("div", { class: "results geo-results" }, list.map((r) =>
        el("button", { type: "button", onclick: () => { data.venue_lat = String(r.lat); data.venue_lng = String(r.lng); data.venue_address = r.address; showPicked(); } },
          el("b", {}, r.name), el("small", {}, r.address)))));
    }));
    showPicked();
    if (!autoFound && data.venue_name && data.venue_lat === "") { autoFound = true; setTimeout(() => find.click(), 0); }
    const manual = el("details", { class: "manual-coords" }, el("summary", {}, "Enter coordinates yourself"),
      el("div", { class: "form-grid" },
        input("venue_lat", "Latitude", { type: "number", step: "any", min: -90, max: 90 }),
        input("venue_lng", "Longitude", { type: "number", step: "any", min: -180, max: 180 }, false, "Right-click the stadium in Google Maps to copy them.")));
    return el("div", { class: "wide venue-finder" },
      el("label", {}, "Venue name", el("div", { class: "row" },
        el("input", { name: "venue_name", value: data.venue_name, placeholder: "e.g. Ohio Stadium, Columbus",
          oninput: (e) => { data.venue_name = e.target.value; data.venue_lat = ""; data.venue_lng = ""; data.venue_address = ""; } }), find)),
      out, manual);
  }

  // Same limits as the server (travel_planner.LIMITS); the server checks again.
  const LIMITS = { num_players: [0, 150, "players"], num_coaches: [0, 30, "coaches"], num_staff: [0, 50, "staff"],
    nights: [0, 14, "hotel nights"], meals: [0, 30, "team meals"] };
  const GRACE = 12 * 3600 * 1000;
  const whole = (k) => {
    const [lo, hi, name] = LIMITS[k];
    const raw = String(data[k]).trim();
    if (raw === "") return `Enter the number of ${name}.`;
    const n = Number(raw);
    if (!Number.isInteger(n)) return `Use a whole number for ${name}.`;
    if (n < lo || n > hi) return `Enter between ${lo} and ${hi} ${name}.`;
    return "";
  };
  function validate() {
    if (step === 1) {
      if (!data.opponent.trim()) return "Enter the opponent.";
      if (!data.match_at) return "Enter the match date and time.";
      if (!data.venue_id && !data.venue_name.trim()) return "Enter the venue's name.";
      if (!data.venue_id && (data.venue_lat === "" || data.venue_lng === "")) return "Click “Find on map” and pick the venue, so we can find hotels near it.";
      const match = new Date(data.match_at).getTime();
      if (Number.isNaN(match)) return "Check the match date and time.";
      if (match < Date.now() - GRACE) return "The match date is in the past. Pick an upcoming game.";
      if (data.depart_after) {
        const dep = new Date(data.depart_after).getTime();
        if (dep >= match) return "The bus must be able to leave before the match starts.";
        if (dep < Date.now() - GRACE) return "The earliest departure time is in the past.";
      }
    }
    if (step === 2) {
      for (const k of ["num_players", "num_coaches", "num_staff"]) { const e = whole(k); if (e) return e; }
      if (Number(data.num_players) + Number(data.num_coaches) + Number(data.num_staff) === 0) return "Add at least one traveler.";
    }
    if (step === 3) {
      for (const k of ["nights", "meals"]) { const e = whole(k); if (e) return e; }
      const b = String(data.budget).trim() === "" ? 0 : Number(data.budget);
      if (!Number.isFinite(b) || b < 0) return "The budget can't be negative.";
      if (b > 1000000) return "Enter a budget up to $1,000,000.";
    }
    return "";
  }
  const nowLocal = () => { const d = new Date(Date.now() - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 16); };
  function draw() {
    fill(stepper, ...["Game details", "Travelers", "Stay & budget", "Review"].map((x, i) => el("button", {
      type: "button", class: step === i + 1 ? "current" : step > i + 1 ? "complete" : "", "aria-current": step === i + 1 ? "step" : null,
      onclick: () => { if (i + 1 < step) { step = i + 1; draw(); } } },
      el("i", {}, step > i + 1 ? "✓" : i + 1), el("span", {}, x, el("small", {}, `Step ${i + 1}`)))));
    let body;
    if (step === 1) {
      const sel = el("select", { name: "venue_id", value: data.venue_id, onchange: (e) => { data.venue_id = e.target.value; draw(); } },
        venues.map((v) => el("option", { value: v.id }, v.name)), el("option", { value: "" }, "Other venue..."));
      body = [title("01", "Where are you headed?", "Start with the game and the away venue."), describeBox(), el("div", { class: "form-grid" },
        input("opponent", "Opponent", { required: true, placeholder: "e.g. Ohio State" }),
        el("label", {}, "Away venue", sel),
        !data.venue_id ? venueFinder() : null,
        input("match_at", "Match date and time", { type: "datetime-local", required: true, min: nowLocal() }),
        input("depart_after", "Bus can't leave before", { type: "datetime-local", min: nowLocal(), max: data.match_at || null }, false, "Optional, must be before the match, e.g. after Friday classes."))];
    } else if (step === 2) {
      const partyTotal = () => ["num_players", "num_coaches", "num_staff"].reduce((n, k) => n + (Number(data[k]) || 0), 0);
      const totalOut = el("strong", {}, `${partyTotal()} people`);
      const roomsOut = el("small", {}, "");
      const showTotal = () => { const n = partyTotal(); totalOut.textContent = `${n} people`;
        roomsOut.textContent = `About ${Math.ceil(n / 2)} hotel rooms (2 per room) · the bus is sized from the fleet automatically`; };
      showTotal();
      body = [title("02", "Who's traveling?", "Counts set the room block, meals and bus capacity. Type a number or use − / +."), el("div", { class: "counter-grid" },
        [["num_players", "Players", `${team.players.length} on the roster`], ["num_coaches", "Coaches", "Travel party"], ["num_staff", "Staff", "Trainers, managers"]].map(([k, name, sub]) => {
          const [lo, hi] = LIMITS[k];
          const box = el("input", { type: "number", inputmode: "numeric", min: lo, max: hi, step: 1, value: data[k], class: "counter-input",
            "aria-label": `Number of ${name.toLowerCase()}`, oninput: (e) => { data[k] = e.target.value; showTotal(); } });
          const nudge = (d) => { const n = Math.min(hi, Math.max(lo, Math.round(Number(data[k]) || 0) + d)); data[k] = n; box.value = n; showTotal(); };
          return el("div", { class: "counter" }, el("span", {}, name), el("small", {}, sub), el("div", {},
            el("button", { type: "button", "aria-label": `Remove one ${name.toLowerCase()}`, onclick: () => nudge(-1) }, "−"),
            box,
            el("button", { type: "button", "aria-label": `Add one ${name.toLowerCase()}`, onclick: () => nudge(1) }, "＋")));
        })),
        el("div", { class: "total-band" }, el("span", {}, "TOTAL TRAVEL PARTY"), totalOut, roomsOut)];
    } else if (step === 3) {
      body = [title("03", "Stay and budget", "These shape the packages. You still approve before anything is booked."), el("div", { class: "form-grid" },
        input("nights", "Hotel nights", { type: "number", min: 0, max: 14, step: 1 }, false, "0 = day trip, no hotel. Up to 14."),
        input("meals", "Team meals", { type: "number", min: 0, max: 30, step: 1 }),
        input("budget", "Total budget (USD)", { type: "number", min: 0, max: 1000000, step: 50 }, true, "Packages over budget are still shown, marked as over.")),
        el("div", { class: "estimate-note" }, el("b", {}, "Dietary needs from your roster"),
          diets.length ? ["Every restaurant menu is checked for: ", diets.map((d) => el("span", { class: "tag" }, label(d)))] : "No player has listed dietary needs.")];
    } else {
      const total = Number(data.num_players) + Number(data.num_coaches) + Number(data.num_staff);
      const review = (name, value, sub, s) => el("div", {}, el("span", {}, name), el("p", {}, el("b", {}, value), el("small", {}, sub)),
        el("button", { type: "button", onclick: () => { step = s; draw(); } }, "Edit"));
      body = [title("04", "Review the travel brief", "Check the details, then generate the package comparison."), el("div", { class: "review-list" },
        review("Game", `${teamName()} at ${data.opponent}`, `${data.match_at ? when(data.match_at) : "No date"} · ${venueName()}`, 1),
        review("Travel party", `${total} travelers`, `${data.num_players} players · ${data.num_coaches} coaches · ${data.num_staff} staff`, 2),
        review("Stay & budget", Number(data.budget) ? `${money0(data.budget)} budget` : "No budget", `${data.nights} night(s) · ${data.meals} team meal(s)${data.depart_after ? " · leave after " + when(data.depart_after) : ""}`, 3)),
        el("div", { class: "estimate-note" }, el("b", {}, "What happens next"), "The planner prices up to 12 hotel × restaurant packages with the cheapest bus that fits, and Aivana recommends one. Nothing is booked yet.")];
    }
    const back = el("button", { type: "button", class: "secondary", onclick: () => { if (step === 1) { try { sessionStorage.removeItem(DRAFT_KEY); } catch (_) {} go("trips"); } else { step--; draw(); } } }, step === 1 ? "← Cancel" : "← Back");
    const next = el("button", { type: "button", class: "primary" }, step === 4 ? "Generate packages →" : "Continue →");
    next.addEventListener("click", () => {
      const err = validate();
      errBox.textContent = err; errBox.hidden = !err;
      if (err) return;
      if (step < 4) { step++; return draw(); }
      const body = { ...data };
      for (const k of ["venue_id", "venue_name", "venue_address", "venue_lat", "venue_lng", "ncaa_game_id", "depart_after"]) if (!body[k]) delete body[k];
      busy(next, async () => {
        const trip = await api("/api/trips", { method: "POST", body });
        try { sessionStorage.removeItem(DRAFT_KEY); } catch (_) {}
        forget("/api/suggestions");
        flash(`${trip.opponent}: ${trip.packages.length} packages priced.`, "ok");
        go("trips/" + trip.id);
      });
    });
    const moved = shown !== step;
    shown = step;
    keepDraft();
    fill(shell, ...body, errBox, el("div", { class: "form-actions" }, back, next));
    if (moved && started) { const first = $("input,select,.counter button", shell); if (first) first.focus(); }
    started = true;
  }
  draw();
  return page;
}

/* ================= COACH: team ================= */
async function teamPage() {
  const team = await api("/api/team");
  state.team = team;
  const ui = { q: "", diet: "all" };
  const page = el("div", { class: "page" }, intro("Roster", "Team membership", `${team.players.length} player${team.players.length === 1 ? "" : "s"} · join code ${team.team.join_code}`,
    el("button", { type: "button", class: "primary", onclick: () => inviteModal(team.team) }, "＋ Invite players")));
  const table = el("div", { class: "member-table" });
  const search = el("input", { placeholder: "Search players", "aria-label": "Search players", oninput: (e) => { ui.q = e.target.value.toLowerCase(); draw(); } });
  const diet = el("select", { "aria-label": "Filter by dietary need", onchange: (e) => { ui.diet = e.target.value; draw(); } },
    el("option", { value: "all" }, "All dietary needs"), CFG.dietTags.map((t) => el("option", { value: t }, label(t))));
  function draw() {
    const shown = team.players.filter((p) => p.full_name.toLowerCase().includes(ui.q) && (ui.diet === "all" || (p.diet_tags || []).includes(ui.diet)));
    fill(table, el("div", { class: "table-row table-header" }, el("span", {}, "Member"), el("span", {}, "Role"), el("span", {}, "Dietary needs"), el("span", {}, "Joined")),
      el("div", { class: "table-row" }, el("span", { class: "member" }, el("i", {}, initials(state.me.profile.full_name)), el("b", {}, state.me.profile.full_name, el("small", {}, state.me.user.email || "You"))),
        el("span", {}, el("em", { class: "pill ok" }, state.me.is_main_planner ? "Main planner" : "Team Planner")), el("span", {}, "–"), el("span", {}, fmt(state.me.profile.created_at, { month: "short", day: "numeric", year: "numeric" }))),
      shown.map((p) => el("div", { class: "table-row" }, el("span", { class: "member" }, el("i", {}, initials(p.full_name)), el("b", {}, p.full_name)),
        el("span", {}, "Player"), el("span", {}, (p.diet_tags || []).length ? p.diet_tags.map((d) => el("span", { class: "tag" }, label(d))) : "None"),
        el("span", {}, fmt(p.created_at, { month: "short", day: "numeric", year: "numeric" })))),
      !shown.length ? el("div", { class: "table-row" }, el("span", { class: "sub" }, team.players.length ? "No players match." : "No players yet. Share the join code.")) : null);
  }
  draw();
  const ncaaBox = el("section", { class: "panel", style: "margin-top:26px" });
  const staffBox = el("section", { class: "panel", style: "margin-top:26px" });
  const staff = team.staff || [];
  const others = staff.filter((c) => c.id !== team.team.owner_id);
  const plannerName = (staff.find((c) => c.id === team.team.owner_id) || {}).full_name || "Not set";
  const handOver = el("select", { "aria-label": "Team planner to make main planner" }, others.map((c) => el("option", { value: c.id }, c.full_name)));
  const handBtn = el("button", { type: "button", class: "secondary" }, "Make main planner");
  handBtn.addEventListener("click", async () => {
    const who = others.find((c) => c.id === handOver.value);
    if (!who || !(await confirmModal("Hand over the main planner role?", `${who.full_name} will be the only one who can book hotels, buses and restaurants. You'll keep view access.`, "Hand over"))) return;
    busy(handBtn, async () => {
      await api("/api/team/planner", { method: "POST", body: { profile_id: who.id } });
      state.me = await api("/api/me");
      renderShell();
      flash(`${who.full_name} is now the main planner.`, "ok");
      route();
    });
  });
  fill(staffBox, el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "Staff"), el("h2", {}, "Main planner")),
    el("span", { class: "sub" }, plannerName)),
    el("p", { class: "sub", style: "margin-bottom:12px" }, "Only the main planner can book hotels, buses and restaurants. Other team planners can see trips and prepare plans."),
    staff.map((c) => el("div", { class: "kv" }, el("span", {}, c.full_name), el("b", {}, c.id === team.team.owner_id ? el("em", { class: "pill ok" }, "Main planner") : "Team Planner"))),
    state.me.is_main_planner && others.length ? el("div", { class: "row", style: "margin-top:14px" }, handOver, handBtn)
      : state.me.is_main_planner ? el("p", { class: "hint", style: "margin-top:10px" }, "When another team planner joins your team you can hand the role to them here.") : null);
  add(page, el("div", { class: "table-tools" }, search, diet), table, staffBox, ncaaBox);
  renderTeamNcaa(ncaaBox);
  return page;
}

function inviteModal(team) {
  const copy = el("button", { type: "button" }, "Copy code");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(team.join_code); copy.textContent = "Copied ✓"; } catch (_) { copy.textContent = "Select and copy"; }
  });
  modal("Invite players", el("div", {},
    el("p", { class: "modal-copy" }, "Players create an account (Google or email), choose ", el("b", {}, "Player"), ", and enter this code. They join as players; only you can plan trips and approve bookings."),
    el("div", { class: "link-box" }, el("span", {}, "TEAM JOIN CODE"), el("code", {}, team.join_code), copy),
    el("p", { class: "auth-note" }, `Link to share: ${location.origin}${location.pathname}`)));
}

async function renderTeamNcaa(box) {
  fill(box, el("p", { class: "sub" }, "Loading NCAA info..."));
  let info;
  try { info = await cached("/api/ncaa/my-team", 10); state.ncaa = info; }
  catch (e) { return fill(box, el("h2", {}, "NCAA program"), el("p", { class: "sub" }, e.message)); }
  if (!info.linked) return renderLinkForm(box);
  fill(box, el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "NCAA program"), el("h2", {}, "Record, ranking & schedule")),
    el("button", { type: "button", class: "text-btn", onclick: () => renderLinkForm(box, info) }, "Switch or add a team")), ...ncaaSummary(info),
    (state.me.teams || []).length > 1 ? el("div", { class: "team-list" }, el("span", { class: "eyebrow" }, "Your teams"), (state.me.teams || []).map((t) =>
      el("div", { class: "team-row" }, teamBadge(t, "n-mark"), el("span", {}, el("b", {}, t.display_name), el("small", {}, t.linked ? `${t.sport_name} · ${t.division_name}` : "Not linked")),
        t.selected ? el("em", { class: "pill ok" }, "Selected") : el("button", { type: "button", class: "secondary small", onclick: () => switchTeam(t.team_id, `Now planning for ${t.display_name}.`) }, "Switch")))) : null);
}

async function renderLinkForm(box, current = {}) {
  const sports = await cached("/api/ncaa/sports", 1440);
  const sport = el("select", { value: current.sport || "football" }, sports.map((sp) => el("option", { value: sp.slug }, sp.name)));
  const division = el("select");
  const fillDivisions = () => {
    const sp = sports.find((x) => x.slug === sport.value);
    fill(division, ...sp.divisions.map((d) => el("option", { value: d.slug }, d.name)));
  };
  fillDivisions();
  if (current.division) division.value = current.division;
  sport.addEventListener("change", fillDivisions);
  const search = el("input", { placeholder: "Type your school, e.g. Bowling Green", autocomplete: "off" });
  const results = el("div", { class: "results", hidden: true });
  let picked = current.seo ? { slug: current.seo, name: current.name } : null;
  const pickedLine = el("p", { class: "sub" }, picked ? `Selected: ${picked.name}` : "No school selected yet.");
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (search.value.trim().length < 2) { results.hidden = true; return; }
      try {
        const list = await api("/api/ncaa/schools?q=" + encodeURIComponent(search.value.trim()));
        results.hidden = false;
        fill(results, ...(list.length ? list.slice(0, 8).map((sc) => el("button", { type: "button",
          onclick: () => { picked = sc; pickedLine.textContent = `Selected: ${sc.name} (${sc.long || sc.slug})`; results.hidden = true; } },
          el("b", {}, sc.name), el("small", {}, sc.long || sc.slug))) : [el("p", { class: "sub", style: "padding:10px" }, "No match.")]));
      } catch (e) { results.hidden = false; fill(results, el("p", { class: "sub", style: "padding:10px" }, e.message)); }
    }, 250);
  });
  const save = el("button", { type: "button", class: "primary" }, current.seo ? "Switch to this team" : "Save NCAA team");
  save.addEventListener("click", () => busy(save, async () => {
    if (!picked) throw new Error("Pick your school from the list first.");
    const r = await api("/api/team", { method: "PATCH", body: { sport: sport.value, division: division.value, ncaa_seo: picked.slug, ncaa_name: picked.name } });
    const who = `${r.identity.display_name} ${r.identity.sport_name}`;
    await refreshTeam({ linked: `Linked ${teamName()} to ${who}.`, switched: `Switched to your ${who} team.`,
      created: `Created a new ${who} team. It starts with no trips.`, unchanged: `Already on ${who}.` }[r.action]);
  }));
  fill(box, el("span", { class: "eyebrow" }, "NCAA program"), el("h2", { style: "margin:4px 0 6px" }, "Link your NCAA team"),
    el("p", { class: "sub", style: "margin-bottom:18px" }, current.seo
      ? `Each NCAA program has its own team with its own trips. Picking a different program switches to that team, or creates it empty; ${teamName()}'s trips stay with ${teamName()}.`
      : "Shows your record, ranking and upcoming games, and lets you plan trips straight from away games."),
    el("div", { class: "form-grid" }, el("label", {}, "Sport", sport), el("label", {}, "Division", division),
      el("label", { class: "wide" }, "School", search, results)),
    el("div", { class: "row between", style: "margin-top:16px" }, pickedLine,
      el("div", { class: "row" }, current.seo ? el("button", { type: "button", class: "secondary", onclick: () => renderTeamNcaa(box) }, "Cancel") : null, save)));
}

/* ================= PLAYER ================= */
async function playerHome() {
  const [trips, feed] = await Promise.all([loadTrips(), api("/api/announcements")]);
  state.feed = feed;
  const t = upcoming(trips)[0];
  const page = el("div", { class: "page" });
  if (!t) {
    add(page, el("div", { class: "player-hero", "data-mark": initials(teamName()) },
      el("div", {}, el("span", { class: "eyebrow light" }, "NEXT AWAY GAME"), el("h1", {}, "No away trips yet."),
        el("p", {}, "Your team planner hasn't scheduled the next away game. You'll see it here as soon as they do."))));
  } else {
    const city = cityOf(t.venue_address) || t.venue_name;
    const depart = t.published && t.suggested_departure ? t.suggested_departure : null;
    const n = daysUntil(depart || t.match_at);
    add(page, el("div", { class: "player-hero", "data-mark": initials(teamName()) },
      el("div", {}, el("span", { class: "eyebrow light" }, "NEXT AWAY GAME"), el("h1", {}, `${city},`, el("br"), "we're on our way."),
        el("p", {}, `${teamName()} at ${t.opponent} · ${when(t.match_at)}`),
        el("button", { type: "button", class: "light-btn", onclick: () => go("itinerary/" + t.id) }, "Open full itinerary →")),
      el("div", { class: "countdown" }, el("span", {}, depart ? "DEPARTURE IN" : "GAME IN"), el("strong", {}, n), el("b", {}, n === 1 ? "day" : "days"),
        el("small", {}, depart ? when(depart) : "Bus time coming soon"))));
    const my = t.my || {};
    const steps = t.published && t.bus ? [
      [timeOnly(addMin(t.suggested_departure, -15)), "Meet at the bus", `${state.me.team.home_name || "Home campus"} · 15 minutes early`],
      [timeOnly(t.suggested_departure), "Bus departs", `${t.bus.company} ${t.bus.vehicle}${my.bus_no ? " · bus " + my.bus_no : ""}`],
      [timeOnly(addMin(t.suggested_departure, t.drive_min)), t.hotel ? "Hotel check-in" : `Arrive in ${city}`, t.hotel ? t.hotel.name : t.venue_name],
    ] : [[timeOnly(t.match_at), "Game time", t.venue_name]];
    add(page, el("div", { class: "player-grid" },
      el("section", { class: "journey" }, el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "Your travel day"),
        el("h2", {}, fmt(t.published && t.suggested_departure ? t.suggested_departure : t.match_at, { weekday: "long", month: "long", day: "numeric" })))),
        steps.map((x, i) => el("div", { class: "journey-row" }, el("i", {}, i + 1), el("b", {}, x[0]), el("span", {}, el("strong", {}, x[1]), el("small", {}, x[2])))),
        !t.published ? el("p", { class: "sub", style: "margin-top:12px" }, "Your team planner is still planning. The bus time and hotel appear here once they publish the plan.") : null,
        el("div", { class: "rsvp-box" }, el("b", {}, "Are you going?"), rsvpButtons(t))),
      el("section", { class: "room-card" }, el("span", { class: "eyebrow" }, "Your stay"),
        el("div", { class: "hotel-thumb" }, t.hotel && t.hotel.photo_url ? hotelPhoto(t.hotel) : el("div", { class: "hotel-art art-1" }, el("span", {}, city))),
        el("h2", {}, t.hotel ? t.hotel.name : t.published ? "Day trip" : "Hotel not published yet"),
        el("p", {}, t.hotel ? [my.room_no ? `Room ${my.room_no}` : "Room not assigned yet", ` · ${t.hotel.km_to_venue} km from the stadium`] : t.published ? "No overnight stay on this trip." : "You'll see your room here."),
        el("button", { type: "button", class: "secondary", onclick: () => go("trips") }, "View trip details")),
      feed[0] ? el("section", { class: "player-notice" }, el("span", {}, `${feed[0].read ? "" : "NEW · "}${fmt(feed[0].created_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).toUpperCase()}`),
        el("h2", {}, feed[0].title), el("p", { class: "pre" }, feed[0].body.length > 220 ? feed[0].body.slice(0, 220) + "..." : feed[0].body),
        el("button", { type: "button", class: "text-btn", onclick: () => go("announcements") }, "Read all updates →"))
        : el("section", { class: "player-notice" }, el("span", {}, "ANNOUNCEMENTS"), el("h2", {}, "No updates yet"), el("p", {}, "Your team planner's messages show up here."))));
  }
  const ncaa = el("section", { class: "activity", style: "margin-top:24px" }, el("div", { class: "section-head" }, el("div", {}, el("span", { class: "eyebrow" }, "NCAA"), el("h2", {}, "Our season"))));
  add(page, ncaa);
  cached("/api/ncaa/my-team", 10).then((info) => add(ncaa, ...ncaaSummary(info))).catch((e) => add(ncaa, el("p", { class: "sub" }, e.message)));
  return page;
}

function rsvpButtons(t) {
  const my = t.my || {};
  return el("div", { class: "segmented", role: "group", "aria-label": `RSVP for ${t.opponent}` }, ["going", "maybe", "not_going"].map((s) => {
    const b = el("button", { type: "button", "aria-pressed": my.rsvp === s ? "true" : "false" }, s === "not_going" ? "Not going" : label(s));
    b.addEventListener("click", () => busy(b, async () => {
      await api(`/api/trips/${t.id}/rsvp`, { method: "POST", body: { rsvp: s } });
      flash("RSVP saved.", "ok");
      route();
    }));
    return b;
  }));
}

async function playerTrips() {
  const trips = await loadTrips();
  const list = [...upcoming(trips), ...pastOrCancelled(trips)];
  const page = el("div", { class: "page" }, intro("Away games", "Your trips", "Only the travel information your team planner has published is shown here."));
  if (!list.length) add(page, el("div", { class: "empty" }, el("b", {}, "No trips yet"), el("p", {}, "Your team planner hasn't planned an away trip.")));
  for (const t of list) {
    const my = t.my || {};
    const city = cityOf(t.venue_address) || t.venue_name;
    const rows = [["Game", when(t.match_at)], ["Venue", [t.venue_name, t.venue_address].filter(Boolean).join(" · ")]];
    if (t.published && t.bus) {
      rows.push(["Bus leaves", `${when(t.suggested_departure)} · ${t.bus.company}${my.bus_no ? " · bus " + my.bus_no : ""}`]);
      if (t.hotel) rows.push(["Hotel", `${t.hotel.name}${t.hotel.rating ? " · ★ " + t.hotel.rating + " on Google" : ""} · ${t.hotel.km_to_venue} km from the stadium`], ["Your room", my.room_no ? `Room ${my.room_no}` : "Not assigned yet"]);
      if (t.restaurant) rows.push(["Team meal", `${t.restaurant.name} (${cap(t.restaurant.cuisine)})`]);
    }
    const diet = state.me.profile.diet_tags || [];
    add(page, el("article", { class: "player-trip" },
      t.hotel && t.hotel.photo_url ? hotelPhoto(t.hotel, "trip-photo") : el("div", { class: "hotel-art art-" + ((list.indexOf(t) % 4) + 1) }, el("span", {}, city)),
      el("div", {}, el("div", { class: "row between" }, el("span", { class: "eyebrow" }, t.published ? "Plan ready" : "Being planned"), statusPill(t)),
        el("h2", {}, `${teamName()} at ${t.opponent}`), el("p", {}, city),
        el("dl", {}, rows.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v)))),
        el("div", { class: "rsvp-box" }, el("b", {}, "Are you going?"), rsvpButtons(t)),
        t.restaurant ? el("details", { class: "menu" }, el("summary", {}, diet.length ? `Dishes that fit your diet (${diet.map(label).join(", ")})` : "Team meal menu"),
          el("ul", {}, (t.my_menu || []).length ? t.my_menu.map((m) => el("li", {}, el("span", {}, m.name), el("span", { class: "sub" }, m.course)))
            : el("li", { class: "sub" }, "No dish matches all your needs. Tell your team planner."))) : null,
        el("div", { class: "row", style: "margin-top:16px" }, el("button", { type: "button", class: "secondary small", onclick: () => go("itinerary/" + t.id) }, "Itinerary"),
          t.hotel ? el("a", { class: "text-btn small", href: t.hotel.maps_url || gmapsLink(t.hotel.lat, t.hotel.lng), target: "_blank", rel: "noopener" }, "Hotel on Google Maps ↗") : null,
          el("a", { class: "text-btn small", href: gmapsLink(t.venue_lat, t.venue_lng), target: "_blank", rel: "noopener" }, "Stadium on Google Maps ↗")),
        el("p", { class: "privacy" }, "Travel costs and planning notes are hidden from player accounts."))));
  }
  return page;
}

/* ================= itinerary (both roles) ================= */
async function itinerary(id) {
  const trips = await loadTrips();
  const pool = isCoach() ? trips.filter((t) => t.status !== "CANCELLED") : trips;
  const pick = id ? pool.find((t) => t.id === id) : upcoming(pool)[0] || pool[0];
  const page = el("div", { class: "page" });
  if (!pick) {
    add(page, intro("Itinerary", "Trip itinerary", ""), el("div", { class: "empty" }, el("b", {}, "No trips yet"), el("p", {}, isCoach() ? "Plan a trip first." : "Your team planner hasn't planned an away trip.")));
    return page;
  }
  const t = isCoach() ? await api(`/api/trips/${pick.id}`) : pick;
  const team = state.me.team || {};
  let src = null;
  if (isCoach()) {
    const p = chosenPackage(t);
    if (p) src = { dep: p.detail.suggested_departure, drive: p.detail.drive_min, bus: p.detail.bus, hotel: p.detail.hotel, restaurant: p.detail.restaurant, final: !!t.selected_package_no };
  } else if (t.published && t.bus) src = { dep: t.suggested_departure, drive: t.drive_min, bus: t.bus, hotel: t.hotel, restaurant: t.restaurant, final: true };
  const my = t.my || {};
  const city = cityOf(t.venue_address) || t.venue_name;
  const items = [];
  if (src) {
    items.push({ time: dowTime(src.dep), kind: "Travel", title: `Depart from ${team.home_name || "home campus"}`,
      detail: `${src.bus.company} ${src.bus.vehicle} · be there 15 minutes early${my.bus_no ? " · you're on bus " + my.bus_no : ""}`,
      more: [`Drive is about ${Math.round(src.drive / 6) / 10} hours.`, src.bus.wifi ? "The bus has Wi-Fi." : null] });
    items.push(src.hotel ? { time: dowTime(addMin(src.dep, src.drive)), kind: "Stay", title: `Check in at ${src.hotel.name}`,
      detail: `${src.hotel.km_to_venue} km from ${t.venue_name}${my.room_no ? " · room " + my.room_no : ""}`,
      more: [src.hotel.address || null, el("a", { href: src.hotel.maps_url || gmapsLink(src.hotel.lat, src.hotel.lng), target: "_blank", rel: "noopener" }, "Hotel on Google Maps ↗")] }
      : { time: dowTime(addMin(src.dep, src.drive)), kind: "Travel", title: `Arrive in ${city}`, detail: "Day trip, no hotel." });
    if (src.restaurant) items.push({ time: "Team meal", kind: "Meal", title: `Team meal at ${src.restaurant.name}`, detail: `${cap(src.restaurant.cuisine)} · ${src.restaurant.km_to_venue} km from the venue`,
      more: isCoach() ? [src.restaurant.missing_diets && src.restaurant.missing_diets.length ? `No main dish for ${src.restaurant.missing_diets.map(label).join(", ")}.` : "Every dietary need on the roster is covered.",
        el("button", { type: "button", class: "text-btn small", onclick: () => openMenu(src.restaurant.id, src.restaurant.name, t.diet_tags || []) }, "View menu →")]
        : [(t.my_menu || []).length ? `Fits your diet: ${t.my_menu.filter((m) => m.course === "main").map((m) => m.name).join(", ") || t.my_menu.map((m) => m.name).join(", ")}.` : "No dish matches all your needs. Tell your team planner."] });
  }
  items.push({ time: dowTime(t.match_at), kind: "Game", title: `${teamName()} at ${t.opponent}`, detail: [t.venue_name, t.venue_address].filter(Boolean).join(" · "),
    more: [el("a", { href: gmapsLink(t.venue_lat, t.venue_lng), target: "_blank", rel: "noopener" }, "Stadium on Google Maps ↗")] });
  if (src) items.push({ time: "After the game", kind: "Travel", title: `Return to ${team.home_name || "campus"}`, detail: `About ${Math.round(src.drive / 6) / 10} hours on the bus` });

  const chooser = pool.length > 1 ? el("label", { class: "field", style: "min-width:240px" }, "Trip",
    el("select", { value: pick.id, onchange: (e) => go("itinerary/" + e.target.value) }, pool.sort(byDate).map((x) => el("option", { value: x.id }, `at ${x.opponent} · ${dayShort(x.match_at)}`)))) : null;
  add(page, el("div", { class: "page-intro" },
    el("div", {}, el("span", { class: "eyebrow" }, `${t.opponent} · ${dayShort(t.match_at)}`), el("h1", {}, "Trip itinerary"),
      el("p", {}, isCoach() ? (t.published ? "Shared with players" : src && src.final ? "Team planner view · not shared with players yet" : "Draft based on the recommended package") : t.published ? "From your team planner's published plan" : "Your team planner hasn't published the travel plan yet")),
    el("div", { class: "header-buttons" }, chooser, el("button", { type: "button", class: "secondary no-print", onclick: () => window.print() }, "Print / save PDF"),
      isCoach() ? el("button", { type: "button", class: "primary", onclick: () => go("trips/" + t.id) }, t.published ? "Open trip" : "Open trip to publish") : null)));
  add(page, el("div", { class: "publish-banner" + (t.published ? " live" : "") }, el("b", {}, t.published ? "● LIVE ITINERARY" : "○ DRAFT ITINERARY"),
    el("span", {}, t.published ? "Players can see everything below." : isCoach() ? "Only team planners can see the travel items until you publish the plan." : "Only the game is shown until your team planner publishes the plan.")));
  let open = 0;
  const tl = el("div", { class: "timeline" });
  const draw = () => fill(tl, ...items.map((x, i) => el("article", {},
    el("div", { class: "timeline-time" }, x.time, el("i")),
    el("button", { type: "button", class: "timeline-main", "aria-expanded": open === i ? "true" : "false", onclick: () => { open = open === i ? -1 : i; draw(); } },
      el("span", { class: "kind " + x.kind.toLowerCase() }, x.kind), el("h2", {}, x.title), el("p", {}, x.detail), x.more ? el("b", { "aria-hidden": "true" }, open === i ? "−" : "＋") : null),
    open === i && x.more ? el("div", { class: "timeline-detail" }, x.more.filter(Boolean).map((m) => el("p", {}, m))) : null)));
  draw();
  add(page, tl);
  return page;
}

/* ================= announcements (both roles) ================= */
async function announcementsPage() {
  const [feed, trips] = await Promise.all([api("/api/announcements"), isCoach() ? loadTrips() : Promise.resolve(state.trips)]);
  state.feed = feed;
  const page = el("div", { class: "page announcements" });
  const composer = el("section", { class: "composer", hidden: true });
  add(page, intro("Team communications", "Announcements", `Travel updates for ${teamName()}.`,
    isCoach() ? el("button", { type: "button", class: "primary", onclick: () => { composer.hidden = !composer.hidden; if (!composer.hidden) $("select,input", composer).focus(); } }, "＋ New announcement") : null));
  if (isCoach()) {
    const usable = trips.filter((t) => t.status !== "CANCELLED").sort(byDate);
    if (!usable.length) fill(composer, el("p", { class: "sub" }, "Announcements are attached to a trip. Plan a trip first."));
    else {
      const tripSel = el("select", { "aria-label": "Trip" }, usable.map((t) => el("option", { value: t.id }, `at ${t.opponent} · ${dayShort(t.match_at)}`)));
      const title = el("input", { placeholder: "Title" });
      const text = el("textarea", { placeholder: "Share a schedule change, packing reminder, or team update...", maxlength: 2000 });
      const counter = el("span", {}, "0/2000");
      text.addEventListener("input", () => (counter.textContent = `${text.value.length}/2000`));
      const draft = el("button", { type: "button" }, "✦ Generate starting draft");
      draft.addEventListener("click", () => busy(draft, async () => {
        const d = await api(`/api/trips/${tripSel.value}/draft`);
        title.value = d.title; text.value = d.body; counter.textContent = `${text.value.length}/2000`; showDraftNote(draftNote, d);
      }));
      const draftNote = el("p", { class: "hint draft-note", hidden: true });
      const publish = el("button", { type: "button", class: "primary" }, "Publish to team");
      publish.addEventListener("click", () => {
        if (!title.value.trim()) return flash("Add a title first.", "error");
        busy(publish, async () => {
          await api(`/api/trips/${tripSel.value}/announcements`, { method: "POST", body: { title: title.value, body: text.value } });
          flash("Announcement posted.", "ok");
          route();
        });
      });
      fill(composer, el("div", { class: "composer-head" }, el("b", {}, "New team announcement"), draft), draftNote,
        el("label", { class: "field" }, "Trip", tripSel), el("label", { class: "field" }, "Title", title), el("label", { class: "field" }, "Message", text),
        el("div", { class: "composer-foot" }, counter, el("button", { type: "button", class: "secondary", onclick: () => (composer.hidden = true) }, "Cancel"), publish),
        el("p", { class: "auth-note" }, "To share a booked trip's hotel, bus and rooms with players, publish the plan from the trip page."));
    }
  }
  add(page, composer);
  const tripName = (id) => { const t = (trips || []).find((x) => x.id === id); return t ? `at ${t.opponent}` : ""; };
  add(page, el("div", { class: "feed" }, feed.length ? feed.map((a) => {
    const save = !isCoach() ? el("button", { type: "button" }, a.saved ? "Saved ✓" : "Save") : null;
    if (save) save.addEventListener("click", () => busy(save, async () => {
      const r = await api(`/api/announcements/${a.id}/save`, { method: "POST" });
      a.saved = r.saved;
      flash(r.saved ? "Saved." : "Removed from saved.", "ok");
    }).then(() => (save.textContent = a.saved ? "Saved ✓" : "Save")));
    const unread = !isCoach() && !a.read;
    if (unread) api(`/api/announcements/${a.id}/read`, { method: "POST" }).then(refreshBadges).catch(() => {});
    return el("article", { class: unread ? "unread" : "" },
      el("div", { class: "post-meta" }, el("span", { class: "avatar-small" }, initials(a.author)),
        el("p", {}, el("b", {}, a.author), el("small", {}, fmt(a.created_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))), unread ? el("em", {}, "NEW") : null),
      el("h2", {}, a.title), el("p", { class: "pre" }, a.body),
      el("div", { class: "post-foot" }, el("span", {}, [tripName(a.trip_id), "Sent to the team"].filter(Boolean).join(" · ")), save));
  }) : el("div", { class: "empty" }, el("b", {}, "No announcements yet"), el("p", {}, isCoach() ? "Post travel updates here; every player on the team sees them." : "Your team planner's travel updates show up here."))));
  return page;
}

/* ================= settings / profile ================= */
async function settingsPage() {
  const p = state.me.profile, team = state.me.team || {};
  const name = el("input", { value: p.full_name, autocomplete: "name" });
  const saveName = el("button", { type: "button", class: "primary" }, "Save profile");
  saveName.addEventListener("click", () => busy(saveName, async () => {
    if (!name.value.trim()) throw new Error("Enter your name.");
    await api("/api/me", { method: "PATCH", body: { full_name: name.value.trim() } });
    state.me = await api("/api/me"); renderShell(); flash("Profile saved.", "ok");
  }));
  const profile = el("section", {}, el("h2", {}, "Profile"), el("label", {}, "Display name", name),
    el("label", {}, "Email", el("input", { value: state.me.user.email || "", disabled: true })), saveName);
  const kv = (k, v) => el("div", { class: "kv" }, el("span", {}, k), el("b", {}, v));
  const sections = [profile];
  if (isCoach()) {
    sections.push(el("section", {}, el("h2", {}, "Team"), kv("Team", team.name || ""), kv("Main planner", state.me.main_planner ? state.me.main_planner.name + (state.me.is_main_planner ? " (you)" : "") : "Not set"),
      kv("Join code", team.join_code || ""), kv("Home campus", team.home_name || ""),
      kv("NCAA program", team.ncaa_name ? `${team.ncaa_name} · ${label(team.sport)} · ${String(team.division || "").toUpperCase()}` : "Not linked"),
      el("button", { type: "button", class: "text-btn", onclick: () => go("team") }, "Manage roster & NCAA link →")));
  } else {
    const box = el("div", {});
    dietBoxes(box, p.diet_tags || []);
    const save = el("button", { type: "button", class: "primary", style: "margin-top:14px" }, "Save dietary needs");
    save.addEventListener("click", () => busy(save, async () => {
      await api("/api/me", { method: "PATCH", body: { diet_tags: checkedDiets(box) } });
      state.me = await api("/api/me"); flash("Saved. Menus now show dishes that fit.", "ok");
    }));
    sections.push(el("section", {}, el("h2", {}, "Dietary needs"), el("p", { class: "sub", style: "margin-bottom:10px" }, "Your team planner sees these when choosing restaurants. Your menu shows dishes that fit all of them."), box, save));
  }
  sections.push(el("section", {}, el("h2", {}, "Account"), kv("Role", roleName(p.role)), kv("Team", team.name || ""),
    kv("Signed in with", CFG.demo ? "Demo account" : state.me.user.provider === "google" ? "Google" : "Email & password"),
    el("button", { type: "button", class: "secondary", style: "margin-top:14px", onclick: signOut }, "Sign out")));
  return el("div", { class: "page" }, intro(isCoach() ? "Workspace" : "Player account", isCoach() ? "Settings" : "Your profile", "Manage your account and travel details."),
    el("div", { class: "settings-grid" }, sections));
}

/* ================= NCAA explorer ================= */
const nx = { tab: "rankings" };
async function ncaaPage() {
  const sports = await cached("/api/ncaa/sports", 1440);
  const team = state.me.team || {};
  const sport = el("select", { value: nx.sport || team.sport || "football" }, sports.map((sp) => el("option", { value: sp.slug }, sp.name)));
  const division = el("select");
  const fillDiv = () => {
    const sp = sports.find((x) => x.slug === sport.value) || sports[0];
    fill(division, ...sp.divisions.map((d) => el("option", { value: d.slug }, d.name)));
  };
  fillDiv();
  division.value = nx.division || (sport.value === team.sport && team.division) || division.value;
  const date = el("input", { type: "date", value: nx.date || new Date().toISOString().slice(0, 10) });
  const school = el("input", { placeholder: "e.g. Kent State", autocomplete: "off", value: nx.school || "" });
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const body = el("div", {});
  const save = () => Object.assign(nx, { sport: sport.value, division: division.value, date: date.value, school: school.value });
  const setTab = (tab) => { nx.tab = tab; drawTabs(); load(); };
  const drawTabs = () => fill(tabs, ...[["rankings", "Rankings"], ["standings", "Standings"], ["scores", "Scores"], ["schools", "Schools"]].map(([k, l]) =>
    el("button", { type: "button", role: "tab", "aria-selected": nx.tab === k ? "true" : "false", onclick: () => setTab(k) }, l)));
  sport.addEventListener("change", () => { fillDiv(); load(); });
  division.addEventListener("change", load);
  date.addEventListener("change", () => { if (nx.tab === "scores") load(); });
  let timer;
  school.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => setTab("schools"), 300); });
  const table = (rows) => {
    if (!rows || !rows.length) return el("p", { class: "sub" }, "Nothing published for this yet.");
    const cols = Object.keys(rows[0]);
    return el("div", { class: "data-scroll" }, el("table", {}, el("thead", {}, el("tr", {}, cols.map((c) => el("th", {}, c)))),
      el("tbody", {}, rows.map((r) => el("tr", {}, cols.map((c) => el("td", {}, r[c] ?? "")))))));
  };
  async function load() {
    save();
    const sp = sports.find((x) => x.slug === sport.value);
    const s = sport.value, d = division.value;
    fill(body, el("p", { class: "loading" }, "Loading..."));
    try {
      if (nx.tab === "rankings") {
        const r = await cached(`/api/ncaa/rankings/${s}/${d}`, 60);
        fill(body, el("div", { class: "section-head" }, el("div", {}, el("h2", {}, r.title || "Rankings"), el("p", { class: "sub" }, r.updated || ""))), table(r.data));
      } else if (nx.tab === "standings") {
        const r = await cached(`/api/ncaa/standings/${s}/${d}`, 60);
        fill(body, el("p", { class: "sub", style: "margin-bottom:12px" }, r.updated || ""), ...(r.data || []).map((c) =>
          el("div", { style: "margin-bottom:18px" }, el("h2", { style: "font-size:16px;margin-bottom:8px" }, c.conference), table(c.standings))));
        if (!(r.data || []).length) add(body, el("p", { class: "sub" }, "No standings published."));
      } else if (nx.tab === "scores") {
        if (sp.scoreboard === false) return fill(body, el("div", { class: "empty" }, el("b", {}, `No scoreboard for ${sp.name}`), el("p", {}, "NCAA doesn't publish scoreboards for this sport. Try Rankings.")));
        const q = sp.weekly ? "" : "?date=" + date.value;
        const games = await cached(`/api/ncaa/scoreboard/${s}/${d}${q}`, 5);
        fill(body, el("p", { class: "sub", style: "margin-bottom:12px" }, sp.weekly ? "This week's games" : `${games.length} games on ${dayShort(date.value + "T12:00")}`),
          games.length ? el("ul", { class: "score-list" }, games.map((g) => el("li", {},
            el("span", {}, el("b", {}, `${teamLabel(g.away)} ${g.away.score}`), " at ", el("b", {}, `${teamLabel(g.home)} ${g.home.score}`),
              el("small", {}, `${g.state === "final" ? g.final_message || "Final" : gameWhen(g)}${g.network ? " · " + g.network : ""}`)),
            g.url ? el("a", { href: g.url, target: "_blank", rel: "noopener", class: "text-btn small" }, "Details ↗") : null)))
            : el("div", { class: "empty" }, el("b", {}, "No games that day"), el("p", {}, "Pick another date.")));
      } else {
        const q = school.value.trim();
        if (q.length < 2) return fill(body, el("p", { class: "sub" }, "Type at least 2 letters in \"Find a school\"."));
        const list = await api("/api/ncaa/schools?q=" + encodeURIComponent(q));
        fill(body, list.length ? el("ul", { class: "school-list", style: "border:1px solid var(--line2)" }, list.map((sc) => {
          const img = el("img", { src: `${CFG.ncaaBase}/logo/${sc.slug}.svg`, alt: "", width: 26, height: 26, loading: "lazy" });
          img.addEventListener("error", () => img.remove());
          return el("li", {}, img, el("b", {}, sc.name), el("span", { class: "sub" }, sc.long || ""));
        })) : el("p", { class: "sub" }, "No schools match."));
      }
    } catch (e) {
      fill(body, el("div", { class: "empty" }, el("b", {}, "NCAA data unavailable"), el("p", {}, e.message)));
    }
  }
  drawTabs();
  load();
  return el("div", { class: "page" }, intro("College sports", "NCAA", "Rankings, standings and scores from ncaa.com (via the free ncaa-api). Rosters aren't published there."),
    el("div", { class: "ncaa-filters" }, el("label", { class: "field" }, "Sport", sport), el("label", { class: "field" }, "Division", division),
      el("label", { class: "field" }, "Date (scores)", date), el("label", { class: "field" }, "Find a school", school)), tabs, body);
}

/* ================= Aivana assistant ================= */
let chat = [];
const chatKey = () => "chat:" + ((state.me && state.me.user.id) || "anon") + ":" + ((state.me && state.me.profile && state.me.profile.team_id) || "-");
function loadChat() {
  try { chat = JSON.parse(localStorage.getItem(chatKey()) || "[]"); } catch (_) { chat = []; }
  renderChat();
}
function saveChat() { try { localStorage.setItem(chatKey(), JSON.stringify(chat.slice(-40))); } catch (_) {} }
function renderChat() {
  const tips = isCoach() ? ["Compare the packages", "Why that recommendation?", "What if we stay 2 nights?", "What's our record?"]
    : ["When does the bus leave?", "What can I eat?", "Where am I staying?", "I'm going"];
  fill($("#chat-chips"), ...tips.map((t) => el("button", { type: "button", class: "chip", onclick: () => sendChat(t) }, t)));
  const log = $("#chat-log");
  const off = state.me && !state.me.aivana;
  $("#chat-input").disabled = off;
  $$("#chat-chips button").forEach((b) => (b.disabled = off));
  if (off) return fill(log, el("div", { class: "msg assistant" }, "Aivana isn't connected yet, so this panel can't answer. ",
    isCoach() ? "Add AIVANA_API_KEY to the server's .env file and restart it." : "Ask your team planner."));
  if (!chat.length) return fill(log, el("div", { class: "msg assistant" }, isCoach()
    ? "Ask Aivana to compare packages, explain the recommendation, prepare a booking for the main planner to approve, or look up NCAA info."
    : "Ask Aivana about your trip: bus time, hotel, room, what you can eat, or RSVP."));
  fill(log, ...chat.map((m, i) => el("div", { class: "msg " + m.role }, m.content, m.proposal ? proposalButtons(m, i) : null)));
  log.scrollTop = log.scrollHeight;
}
// A what-if change Aivana suggested: the app priced it; the coach decides whether to apply it.
function proposalButtons(m, i) {
  if (m.proposal.state === "applied") return el("p", { class: "proposal-state" }, "✓ Applied. Packages re-priced.");
  if (m.proposal.state === "dismissed") return el("p", { class: "proposal-state" }, "Not applied.");
  const apply = el("button", { type: "button", class: "primary small" }, "Apply and re-price");
  apply.addEventListener("click", () => busy(apply, async () => {
    await api(`/api/trips/${m.proposal.trip_id}/update`, { method: "POST", body: { changes: m.proposal.changes } });
    chat[i].proposal.state = "applied"; saveChat(); renderChat();
    flash("Trip updated and packages re-priced. Nothing was booked.", "ok");
    go("trips/" + m.proposal.trip_id);
  }));
  const dismiss = el("button", { type: "button", class: "secondary small", onclick: () => { chat[i].proposal.state = "dismissed"; saveChat(); renderChat(); } }, "Keep current plan");
  return el("div", { class: "proposal-actions" }, apply, dismiss);
}
function openChat() { $("#assistant").hidden = false; $("#chat-toggle").setAttribute("aria-expanded", "true"); $("#chat-input").focus(); }
function closeChat() { $("#assistant").hidden = true; $("#chat-toggle").setAttribute("aria-expanded", "false"); }
$("#chat-toggle").addEventListener("click", () => ($("#assistant").hidden ? openChat() : closeChat()));
$("#chat-close").addEventListener("click", closeChat);
$("#chat-clear").addEventListener("click", () => { chat = []; saveChat(); renderChat(); });
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const t = $("#chat-input").value.trim(); if (t) sendChat(t); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#assistant").hidden && !$("#modal-root").children.length) closeChat(); });

async function sendChat(text) {
  $("#chat-input").value = "";
  chat.push({ role: "user", content: text });
  renderChat();
  add($("#chat-log"), el("div", { class: "msg assistant typing" }, el("i"), el("i"), el("i"), " Checking your trips"));
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  try {
    const r = await api("/api/chat", { method: "POST", body: { messages: chat.slice(-12), trip_id: state.tripId || undefined } });
    const msg = { role: "assistant", content: r.reply || "(no answer)" };
    if (r.action && r.action.ok && r.action.type === "propose_change") msg.proposal = { trip_id: r.action.trip_id, changes: r.action.changes };
    chat.push(msg);
    if (r.action && r.action.ok && r.action.type !== "propose_change") {
      if (r.action.type === "request_booking") go("trips/" + r.action.trip_id);
      else route();
    }
  } catch (e) {
    chat.push({ role: "assistant", content: "Sorry, something went wrong: " + e.message });
  }
  saveChat();
  renderChat();
}

/* ---------- start ---------- */
setupAuth();
setupOnboard();
if (sb) sb.auth.onAuthStateChange((event) => { if (event === "SIGNED_OUT") showView("auth"); });
boot();
