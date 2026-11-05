// Persephone — a small, dependency-free single page app talking to /api.

const STATUS = { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Abandoned" };
const FORMATS = {
  hardcover: "Hardcover",
  paperback: "Paperback",
  ebook: "E-book",
  audiobook: "Audiobook",
  other: "Other",
};
const SORTS = {
  title: { label: "Title", order: "asc" },
  author: { label: "Author", order: "asc" },
  added: { label: "Recently added", order: "desc" },
  updated: { label: "Recently updated", order: "desc" },
  rating: { label: "Rating", order: "desc" },
  year: { label: "Year published", order: "desc" },
  finished: { label: "Recently finished", order: "desc" },
};
const PAGE_SIZE = 60;

// localStorage can throw (private windows, blocked storage): it only keeps conveniences.
const storage = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
  },
};

const state = {
  config: { default_owner: "Me", openlibrary: true, version: "" },
  layout: storage.get("persephone.layout") || "grid",
  library: null, // { mode, params, items, total }
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const view = $("#view");

// ----------------------------------------------------------------- helpers --

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function icon(name) {
  const paths = {
    heart: '<path d="M12 20s-7-4.4-9.2-9A5 5 0 0 1 12 6a5 5 0 0 1 9.2 5c-2.2 4.6-9.2 9-9.2 9z"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    door: '<path d="M13 4h3a2 2 0 0 1 2 2v14"/><path d="M2 20h3"/><path d="M13 20h9"/><path d="M10 12v.01"/><path d="M13 4.562v16.157a1 1 0 0 1-1.242.97L5 20V5.562a2 2 0 0 1 1.515-1.94l4-1A2 2 0 0 1 13 4.561z"/>',
    back: '<path d="M15 18l-6-6 6-6"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    book: '<path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4V4z"/><path d="M5 16a4 4 0 0 1 4-4h10"/>',
    tablet: '<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M11 18h2"/>',
    headphones: '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="4" height="6" rx="1"/><rect x="17" y="14" width="4" height="6" rx="1"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    upload: '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
    return: '<path d="M9 14l-4-4 4-4"/><path d="M5 10h9a5 5 0 0 1 0 10h-2"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ""}</svg>`;
}

function hashString(text) {
  let h = 0;
  for (const ch of String(text)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return h;
}

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function plural(n, word, many = `${word}s`) {
  return `${n.toLocaleString()} ${n === 1 ? word : many}`;
}

function splitList(text) {
  return String(text || "").split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// --------------------------------------------------------------------- api --

class ApiError extends Error {}

function describeError(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const where = (d.loc || []).filter((p) => p !== "body").join(" → ");
      const msg = String(d.msg || "").replace(/^Value error, /, "");
      return where ? `${where}: ${msg}` : msg;
    }).join(" · ");
  }
  return `Request failed (${status})`;
}

async function api(path, { method = "GET", body, query } = {}) {
  let url = `/api${path}`;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") qs.set(k, v);
    if ([...qs].length) url += `?${qs}`;
  }
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new ApiError("Cannot reach the Persephone server. Is it running?");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(describeError(data?.detail, response.status));
  return data;
}

// ------------------------------------------------------------------- toast --

let toastTimer;
function toast(message, { error = false } = {}) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("is-error", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 2800);
}

function confirmDialog(text, okLabel = "Delete") {
  const dialog = $("#confirm-dialog");
  $("#confirm-text", dialog).textContent = text;
  $("#confirm-ok", dialog).textContent = okLabel;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
  });
}

// ------------------------------------------------------------- rendering --

function coverHtml(book) {
  const variant = hashString(book.title) % 6;
  const src = book.cover_url
    || (state.config.openlibrary && book.isbn
      ? `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(book.isbn)}-L.jpg?default=false`
      : null);
  return `
    <div class="cover">
      <div class="cover-art v${variant}" aria-hidden="true">
        <div class="cover-title">${esc(book.title)}</div>
        <div class="cover-seed"></div>
        <div class="cover-author">${esc(book.authors[0] || "")}</div>
      </div>
      ${src ? `<img src="${esc(src)}" alt="" loading="lazy" decoding="async" data-cover style="position:relative">` : ""}
    </div>`;
}

const STAR_PATH = "M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5L2.5 9.4l6.6-.9z";

function starsHtml(value, { size } = {}) {
  const v = Number(value) || 0;
  let html = `<span class="stars"${size ? ` style="--size:${size}"` : ""} aria-hidden="true">`;
  for (let i = 1; i <= 5; i++) {
    const fill = Math.max(0, Math.min(1, v - (i - 1))) * 100;
    html += `<span class="star"><svg viewBox="0 0 24 24"><path class="bg" d="${STAR_PATH}"/></svg>`
      + `<span class="clip" style="width:${fill}%"><svg viewBox="0 0 24 24" style="width:var(--size);height:var(--size)"><path class="fg" d="${STAR_PATH}"/></svg></span></span>`;
  }
  return `${html}</span>`;
}

function ratingText(value) {
  return value ? `${value} out of 5 stars` : "Not rated";
}

function statusPill(status) {
  return `<span class="pill status-${esc(status)}">${esc(STATUS[status] || status)}</span>`;
}

function isLent(book) {
  return book.copies.some((c) => c.lent_to);
}

function bookCard(book) {
  const authors = book.authors.join(", ") || "Unknown author";
  return `
    <article class="card" data-book="${book.id}">
      <a class="card-link" href="#/book/${book.id}">
        ${coverHtml(book)}
        <h3 class="card-title" style="margin-top:10px">${esc(book.title)}</h3>
        <p class="card-authors">${esc(authors)}</p>
      </a>
      <div class="card-meta">
        ${book.rating ? `<span title="${esc(ratingText(book.rating))}">${starsHtml(book.rating, { size: "0.9rem" })}<span class="visually-hidden">${esc(ratingText(book.rating))}</span></span>` : ""}
        ${book.status ? statusPill(book.status) : ""}
        ${isLent(book) ? '<span class="pill lent">Lent</span>' : ""}
      </div>
      ${favButton(book)}
    </article>`;
}

function favButton(book) {
  const label = book.favorite ? `Remove “${book.title}” from favorites` : `Add “${book.title}” to favorites`;
  return `<button type="button" class="fav-btn" data-fav="${book.id}" aria-pressed="${book.favorite}" aria-label="${esc(label)}" title="${book.favorite ? "Favorite" : "Mark as favorite"}">${icon("heart")}</button>`;
}

function bookRow(book) {
  const sub = [book.subtitle, book.series && `${book.series}${book.series_index != null ? ` #${book.series_index}` : ""}`, book.published_year]
    .filter(Boolean).join(" · ");
  return `
    <article class="row" data-book="${book.id}">
      ${coverHtml(book)}
      <div style="min-width:0">
        <a class="row-title" href="#/book/${book.id}">${esc(book.title)}</a>
        ${sub ? `<div class="row-sub">${esc(sub)}</div>` : ""}
      </div>
      <div class="row-authors">${esc(book.authors.join(", "))}</div>
      <div class="row-rating" title="${esc(ratingText(book.rating))}">${book.rating ? starsHtml(book.rating, { size: "0.85rem" }) : ""}</div>
      <div class="card-meta">
        ${book.status ? statusPill(book.status) : ""}
        ${book.owned ? `<span class="pill owned">${plural(book.copies.length, "copy", "copies")}</span>` : ""}
      </div>
      ${favButton(book)}
    </article>`;
}

// ------------------------------------------------------------------ router --

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(query) };
}

function libraryHref(mode, params) {
  const qs = params.toString();
  return `#/${mode === "favorites" ? "favorites" : ""}${qs ? `?${qs}` : ""}`;
}

function setNav(section) {
  for (const link of $$("[data-nav]")) {
    if (link.dataset.nav === section) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function setTitle(text) {
  document.title = text ? `${text} · Persephone` : "Persephone";
}

async function route() {
  const { parts, params } = parseHash();
  const [section = "library", id] = parts;
  try {
    if (section === "library" || section === "favorites") {
      await renderLibrary(section, params);
    } else if (section === "book" && id) {
      await renderBookPage(Number(id));
    } else if (section === "stats") {
      await renderStats();
    } else if (section === "data") {
      await renderBackup();
    } else if (section === "help") {
      renderHelp();
    } else {
      setNav(null);
      view.dataset.view = "missing";
      view.innerHTML = `<div class="empty"><h2>Nothing grows here</h2><p>This page does not exist.</p><a class="btn btn-primary" href="#/">Back to the library</a></div>`;
    }
  } catch (error) {
    view.dataset.view = "error";
    view.innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(error.message)}</p><a class="btn" href="#/">Back to the library</a></div>`;
  }
}

// ----------------------------------------------------------------- library --

async function renderLibrary(mode, params) {
  setNav(mode);
  setTitle(mode === "favorites" ? "Favorites" : "Library");
  if (view.dataset.view !== `library-${mode}`) {
    view.dataset.view = `library-${mode}`;
    view.innerHTML = libraryShell(mode);
    bindLibraryToolbar(mode);
    loadFacets();
  }
  syncToolbar(params);
  await loadBooks(mode, params);
}

function libraryShell(mode) {
  const favorites = mode === "favorites";
  return `
    <div class="page-head">
      <div>
        <h1>${favorites ? "Favorites" : "Library"}</h1>
        <p class="lede">${favorites ? "The books you would carry back from the underworld." : "Every book you own, read, or dream of reading."}</p>
        <span class="ornament" aria-hidden="true"></span>
      </div>
    </div>
    <section class="toolbar" aria-label="Filters">
      <div class="toolbar-row">
        <label class="search">
          <span class="visually-hidden">Search</span>
          ${icon("search")}
          <input type="search" name="q" placeholder="Search title, author, series, ISBN…" autocomplete="off">
        </label>
        <label class="visually-hidden" for="filter-sort">Sort by</label>
        <select id="filter-sort" name="sort" class="select-compact">
          ${Object.entries(SORTS).map(([k, s]) => `<option value="${k}">${esc(s.label)}</option>`).join("")}
        </select>
      </div>
      <div class="toolbar-row">
        <div class="chips" role="group" aria-label="Reading status">
          <button type="button" class="chip" data-status="">All</button>
          ${Object.entries(STATUS).map(([k, label]) => `<button type="button" class="chip" data-status="${k}">${esc(label)}</button>`).join("")}
        </div>
        <label class="visually-hidden" for="filter-own">Ownership</label>
        <select id="filter-own" name="own" class="select-compact">
          <option value="">Owned or not</option>
          <option value="owned">Owned</option>
          <option value="not_owned">Not owned (wishlist)</option>
          <option value="lent">Lent out</option>
        </select>
        <label class="visually-hidden" for="filter-owner">Owner</label>
        <select id="filter-owner" name="owner" class="select-compact"><option value="">Any owner</option></select>
        <label class="visually-hidden" for="filter-tag">Tag</label>
        <select id="filter-tag" name="tag" class="select-compact"><option value="">Any tag</option></select>
        <span id="author-filter"></span>
      </div>
    </section>
    <div class="result-meta">
      <span id="result-count" aria-live="polite"></span>
      <div class="segmented" role="group" aria-label="Layout">
        <button type="button" data-layout="grid">${icon("grid")} Covers</button>
        <button type="button" data-layout="list">${icon("list")} List</button>
      </div>
    </div>
    <div id="results"></div>
    <div class="load-more" hidden><button type="button" class="btn" id="load-more">Show more</button></div>`;
}

function currentParams() {
  return parseHash().params;
}

function updateParams(mode, changes, { replace = false } = {}) {
  const params = currentParams();
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === undefined || value === "") params.delete(key);
    else params.set(key, value);
  }
  const href = libraryHref(mode, params);
  if (replace) history.replaceState(null, "", href);
  else history.pushState(null, "", href);
  route();
}

function bindLibraryToolbar(mode) {
  const search = $('input[name="q"]', view);
  search.addEventListener("input", debounce(() => updateParams(mode, { q: search.value.trim() }, { replace: true }), 250));
  $("#filter-sort", view).addEventListener("change", (e) => updateParams(mode, { sort: e.target.value === "title" ? null : e.target.value, order: null }));
  $("#filter-own", view).addEventListener("change", (e) => updateParams(mode, { own: e.target.value }));
  $("#filter-owner", view).addEventListener("change", (e) => updateParams(mode, { owner: e.target.value }));
  $("#filter-tag", view).addEventListener("change", (e) => updateParams(mode, { tag: e.target.value }));
  for (const chip of $$("[data-status]", view)) {
    chip.addEventListener("click", () => updateParams(mode, { status: chip.dataset.status }));
  }
  for (const button of $$("[data-layout]", view)) {
    button.addEventListener("click", () => {
      state.layout = button.dataset.layout;
      storage.set("persephone.layout", state.layout);
      syncLayoutButtons();
      renderResults();
    });
  }
  $("#load-more", view).addEventListener("click", () => loadBooks(mode, currentParams(), { append: true }));
  $("#author-filter", view).addEventListener("click", (e) => {
    if (e.target.closest("[data-clear-author]")) updateParams(mode, { author: null });
  });
}

function syncLayoutButtons() {
  for (const button of $$("[data-layout]", view)) {
    button.setAttribute("aria-pressed", String(button.dataset.layout === state.layout));
  }
}

function syncToolbar(params) {
  const search = $('input[name="q"]', view);
  if (document.activeElement !== search) search.value = params.get("q") || "";
  $("#filter-sort", view).value = params.get("sort") || "title";
  $("#filter-own", view).value = params.get("own") || "";
  setSelectValue($("#filter-owner", view), params.get("owner") || "");
  setSelectValue($("#filter-tag", view), params.get("tag") || "");
  for (const chip of $$("[data-status]", view)) {
    chip.setAttribute("aria-pressed", String(chip.dataset.status === (params.get("status") || "")));
  }
  const author = params.get("author");
  $("#author-filter", view).innerHTML = author
    ? `<button type="button" class="chip" aria-pressed="true" data-clear-author aria-label="Stop filtering by ${esc(author)}">${esc(author)} ${icon("x")}</button>`
    : "";
  syncLayoutButtons();
}

function setSelectValue(select, value) {
  if (value && ![...select.options].some((o) => o.value === value)) {
    select.add(new Option(value, value));
  }
  select.value = value;
}

async function loadFacets() {
  try {
    const [owners, tags] = await Promise.all([api("/owners"), api("/tags")]);
    const params = currentParams();
    fillSelect($("#filter-owner", view), "Any owner", owners, params.get("owner"));
    fillSelect($("#filter-tag", view), "Any tag", tags, params.get("tag"));
  } catch { /* the filters still work without the lists */ }
}

function fillSelect(select, emptyLabel, items, selected) {
  if (!select) return;
  select.innerHTML = `<option value="">${esc(emptyLabel)}</option>`
    + items.map((i) => `<option value="${esc(i.name)}">${esc(i.name)} (${i.count})</option>`).join("");
  setSelectValue(select, selected || "");
}

function apiQuery(mode, params, offset) {
  const sort = SORTS[params.get("sort")] ? params.get("sort") : "title";
  const own = params.get("own");
  return {
    q: params.get("q"),
    status: params.get("status"),
    owner: params.get("owner"),
    tag: params.get("tag"),
    author: params.get("author"),
    favorite: mode === "favorites" ? true : null,
    owned: own === "owned" ? true : own === "not_owned" ? false : null,
    lent: own === "lent" ? true : null,
    sort,
    order: params.get("order") || SORTS[sort].order,
    limit: PAGE_SIZE,
    offset,
  };
}

let loadToken = 0;
async function loadBooks(mode, params, { append = false } = {}) {
  const token = ++loadToken;
  const offset = append && state.library ? state.library.items.length : 0;
  const page = await api("/books", { query: apiQuery(mode, params, offset) });
  if (token !== loadToken) return; // a newer request superseded this one
  const items = append && state.library ? [...state.library.items, ...page.items] : page.items;
  state.library = { mode, params: params.toString(), items, total: page.total };
  renderResults();
}

function hasFilters(params) {
  return ["q", "status", "own", "owner", "tag", "author"].some((k) => params.get(k));
}

function renderResults() {
  const results = $("#results", view);
  if (!results || !state.library) return;
  const { mode, items, total } = state.library;
  const params = new URLSearchParams(state.library.params);
  $("#result-count", view).textContent = total ? `${plural(total, "book")}${items.length < total ? `, showing ${items.length}` : ""}` : "";
  $(".load-more", view).hidden = items.length >= total;

  if (!items.length) {
    if (hasFilters(params)) {
      results.innerHTML = `<div class="empty"><h2>No books match</h2><p>Try a different search or filter.</p>
        <a class="btn" href="${libraryHref(mode, new URLSearchParams())}">Clear filters</a></div>`;
    } else if (mode === "favorites") {
      results.innerHTML = `<div class="empty"><h2>No favorites yet</h2><p>Tap the heart on a book to keep it here.</p>
        <a class="btn btn-primary" href="#/">Browse the library</a></div>`;
    } else {
      results.innerHTML = `<div class="empty"><h2>Your library is an empty field</h2>
        <p>Plant the first seed: add a book, or run <span class="code">make import</span> / <span class="code">make seed</span>.</p>
        <button type="button" class="btn btn-accent" data-action="add-book">${icon("plus")} Add your first book</button></div>`;
    }
    return;
  }
  results.innerHTML = state.layout === "list"
    ? `<div class="book-list">${items.map(bookRow).join("")}</div>`
    : `<div class="book-grid">${items.map(bookCard).join("")}</div>`;
}

async function toggleFavorite(button) {
  const id = Number(button.dataset.fav);
  const next = button.getAttribute("aria-pressed") !== "true";
  button.disabled = true;
  try {
    const book = await api(`/books/${id}`, { method: "PATCH", body: { favorite: next } });
    if (state.library) {
      state.library.items = state.library.items.map((b) => (b.id === id ? book : b));
      if (state.library.mode === "favorites" && !next) {
        state.library.items = state.library.items.filter((b) => b.id !== id);
        state.library.total -= 1;
      }
    }
    if (view.dataset.view?.startsWith("library")) renderResults();
    else renderBookDetail(book);
    toast(next ? `“${book.title}” added to favorites` : `“${book.title}” removed from favorites`);
  } catch (error) {
    toast(error.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

// -------------------------------------------------------------- book page --

async function renderBookPage(id) {
  setNav(null);
  view.dataset.view = "book";
  view.innerHTML = '<p class="loading">Fetching the book…</p>';
  let book;
  try {
    book = await api(`/books/${id}`);
  } catch (error) {
    view.innerHTML = `<div class="empty"><h2>Book not found</h2><p>${esc(error.message)}</p><a class="btn btn-primary" href="#/">Back to the library</a></div>`;
    return;
  }
  renderBookDetail(book);
  window.scrollTo(0, 0);
}

function rememberFocus() {
  const key = document.activeElement?.closest("[data-key]")?.dataset.key;
  return () => {
    if (key) $(`[data-key="${CSS.escape(key)}"]`, view)?.focus();
  };
}

function renderBookDetail(book) {
  if (view.dataset.view !== "book") return;
  state.book = book;
  setTitle(book.title);
  const restoreFocus = rememberFocus();
  const authors = book.authors.length
    ? book.authors.map((a) => `<a href="#/?author=${encodeURIComponent(a)}">${esc(a)}</a>`).join(", ")
    : '<span class="muted">Unknown author</span>';
  const meta = [
    ["Publisher", book.publisher],
    ["Published", book.published_year],
    ["First published", book.original_year !== book.published_year ? book.original_year : null],
    ["Pages", book.pages?.toLocaleString()],
    ["Language", book.language],
    ["Original title", book.original_title],
    ["Original language", book.original_language !== book.language ? book.original_language : null],
    ["Translated by", book.translator],
    ["ISBN", book.isbn],
    ["Added", formatDate(book.created_at.slice(0, 10))],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");

  view.innerHTML = `
    <a class="back-link" href="${esc(state.library ? libraryHref(state.library.mode, new URLSearchParams(state.library.params)) : "#/")}">${icon("back")} ${state.library?.mode === "favorites" ? "Favorites" : "Library"}</a>
    <div class="detail">
      <aside class="detail-side">
        ${coverHtml(book)}
        <div class="actions">
          <button type="button" class="btn fav-toggle" data-key="fav" data-fav="${book.id}" aria-pressed="${book.favorite}">
            ${icon("heart")} ${book.favorite ? "Favorite" : "Add to favorites"}
          </button>
          <button type="button" class="btn" data-key="edit" data-action="edit-book">${icon("edit")} Edit</button>
        </div>
      </aside>

      <div class="detail-main">
        <header class="detail-title">
          <h1>${esc(book.title)}</h1>
          ${book.subtitle ? `<p class="subtitle">${esc(book.subtitle)}</p>` : ""}
          <p class="byline">by ${authors}</p>
          ${book.series ? `<p class="series">${esc(book.series)}${book.series_index != null ? `, book ${esc(book.series_index)}` : ""}</p>` : ""}
        </header>

        <section class="panel" aria-labelledby="reading-title">
          <div class="section-title"><h2 id="reading-title">Your reading</h2></div>
          <div class="status-picker chips" role="group" aria-label="Reading status">
            ${Object.entries(STATUS).map(([k, label]) => `<button type="button" class="chip" data-key="status-${k}" data-set-status="${k}" aria-pressed="${book.status === k}">${esc(label)}</button>`).join("")}
          </div>
          <div class="rating-row" style="margin-top:16px">
            <span class="rating-input" role="slider" tabindex="0" data-key="rating" aria-label="Your rating"
              aria-valuemin="0" aria-valuemax="5" aria-valuenow="${book.rating || 0}" aria-valuetext="${esc(ratingText(book.rating))}">
              ${starsHtml(book.rating, { size: "1.9rem" })}
            </span>
            <span class="rating-value">${book.rating ? `${book.rating} / 5` : "Not rated"}</span>
            ${book.rating ? '<button type="button" class="btn btn-ghost btn-sm" data-key="clear-rating" data-action="clear-rating">Clear</button>' : ""}
          </div>
          <div class="dates">
            <div class="field">
              <label for="d-started">Started</label>
              <input type="date" id="d-started" data-key="started" data-date="started_on" value="${esc(book.started_on || "")}">
            </div>
            <div class="field">
              <label for="d-finished">Finished</label>
              <input type="date" id="d-finished" data-key="finished" data-date="finished_on" value="${esc(book.finished_on || "")}">
            </div>
          </div>
        </section>

        <section class="panel" aria-labelledby="review-title">
          <div class="section-title"><h2 id="review-title">Review &amp; notes</h2></div>
          <form id="review-form" class="field">
            <label class="visually-hidden" for="review">Review</label>
            <textarea id="review" name="review" rows="4" data-key="review" placeholder="What stayed with you?">${esc(book.review || "")}</textarea>
            <div class="actions" style="justify-content:flex-end">
              <button type="submit" class="btn btn-secondary" data-key="save-review">Save notes</button>
            </div>
          </form>
        </section>

        <section class="panel" aria-labelledby="copies-title">
          <div class="section-title">
            <h2 id="copies-title">Copies</h2>
            <button type="button" class="btn btn-secondary btn-sm" data-key="add-copy" data-action="add-copy">${icon("plus")} Add copy</button>
          </div>
          ${book.copies.length
            ? `<div class="copies">${book.copies.map(copyHtml).join("")}</div>`
            : '<p class="muted" style="margin:0;color:var(--ink-muted)">Nobody owns this one yet. It lives on the wishlist.</p>'}
        </section>

        <section class="panel" aria-labelledby="about-title">
          <div class="section-title"><h2 id="about-title">About the book</h2></div>
          ${meta.length ? `<dl class="meta-grid">${meta.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` : ""}
          ${book.tags.length ? `<div class="chips" style="margin-top:14px">${book.tags.map((t) => `<a class="tag" href="#/?tag=${encodeURIComponent(t)}">${esc(t)}</a>`).join("")}</div>` : ""}
          ${book.description ? `<p class="description" style="margin-top:14px">${esc(book.description)}</p>` : ""}
          ${!meta.length && !book.tags.length && !book.description ? '<p style="margin:0;color:var(--ink-muted)">No details yet. Use “Edit” to add some.</p>' : ""}
        </section>

        <div class="danger-zone">
          <button type="button" class="btn btn-danger" data-action="delete-book">${icon("trash")} Delete book</button>
        </div>
      </div>
    </div>`;
  bindRating($(".rating-input", view), book);
  restoreFocus();
}

function copyHtml(copy) {
  const iconName = copy.format === "ebook" ? "tablet" : copy.format === "audiobook" ? "headphones" : "book";
  const details = [copy.location, copy.acquired_on && `acquired ${formatDate(copy.acquired_on)}`].filter(Boolean).join(" · ");
  return `
    <div class="copy">
      <span class="copy-icon">${icon(iconName)}</span>
      <div class="copy-main">
        <div><strong>${esc(FORMATS[copy.format] || copy.format)}</strong> · ${esc(copy.owner)}
          ${copy.lent_to ? `<span class="pill lent">Lent to ${esc(copy.lent_to)}</span>` : ""}</div>
        ${details ? `<div class="muted">${esc(details)}</div>` : ""}
        ${copy.notes ? `<div class="muted">${esc(copy.notes)}</div>` : ""}
      </div>
      <div class="copy-actions">
        ${copy.lent_to ? `<button type="button" class="btn btn-ghost btn-icon" data-key="return-${copy.id}" data-return-copy="${copy.id}" aria-label="Mark as returned" title="Mark as returned">${icon("return")}</button>` : ""}
        <button type="button" class="btn btn-ghost btn-icon" data-key="edit-copy-${copy.id}" data-edit-copy="${copy.id}" aria-label="Edit copy" title="Edit copy">${icon("edit")}</button>
        <button type="button" class="btn btn-ghost btn-icon" data-delete-copy="${copy.id}" aria-label="Delete copy" title="Delete copy">${icon("trash")}</button>
      </div>
    </div>`;
}

async function patchBook(changes, successMessage) {
  const book = state.book;
  try {
    const updated = await api(`/books/${book.id}`, { method: "PATCH", body: changes });
    renderBookDetail(updated);
    if (successMessage) toast(successMessage);
    return updated;
  } catch (error) {
    toast(error.message, { error: true });
    renderBookDetail(book); // put the controls back the way they were
    return null;
  }
}

function setStatus(status) {
  const book = state.book;
  const changes = { status };
  if (status === "reading" && !book.started_on) changes.started_on = today();
  if (status === "read" && !book.finished_on) changes.finished_on = today();
  patchBook(changes, status ? `Marked as “${STATUS[status]}”` : "Status cleared");
}

function bindRating(el, book) {
  const stars = $$(".star", el);
  let preview = null;
  const paint = (value) => {
    stars.forEach((star, i) => {
      const fill = Math.max(0, Math.min(1, value - i)) * 100;
      $(".clip", star).style.width = `${fill}%`;
    });
  };
  const valueAt = (event) => {
    const star = event.target.closest(".star");
    if (!star) return null;
    const index = stars.indexOf(star);
    const rect = star.getBoundingClientRect();
    return index + (event.clientX - rect.left < rect.width / 2 ? 0.5 : 1);
  };
  const commit = (value) => patchBook({ rating: value || null }, value ? `Rated ${value} / 5` : "Rating cleared");
  const commitSoon = debounce(commit, 450);

  el.addEventListener("mousemove", (e) => {
    const v = valueAt(e);
    if (v !== null && v !== preview) { preview = v; paint(v); }
  });
  el.addEventListener("mouseleave", () => { preview = null; paint(book.rating || 0); });
  el.addEventListener("click", (e) => {
    const v = valueAt(e);
    if (v !== null) commit(v === book.rating ? null : v);
  });
  el.addEventListener("keydown", (e) => {
    let value = Number(el.getAttribute("aria-valuenow")) || 0;
    if (["ArrowRight", "ArrowUp"].includes(e.key)) value = Math.min(5, value + 0.5);
    else if (["ArrowLeft", "ArrowDown"].includes(e.key)) value = Math.max(0, value - 0.5);
    else if (e.key === "End") value = 5;
    else if (["Home", "Delete", "Backspace"].includes(e.key)) value = 0;
    else return;
    e.preventDefault();
    el.setAttribute("aria-valuenow", String(value));
    el.setAttribute("aria-valuetext", ratingText(value));
    paint(value);
    commitSoon(value);
  });
}

// ------------------------------------------------------------ book dialog --

const bookDialog = $("#book-dialog");
const bookForm = $("#book-form");

function fillOptions(select, options, selected) {
  select.innerHTML = Object.entries(options).map(([k, label]) => `<option value="${k}">${esc(label)}</option>`).join("");
  select.value = selected;
}

function showFormError(form, message) {
  const box = $(".form-error", form);
  box.textContent = message || "";
  box.hidden = !message;
}

async function openBookDialog(book = null) {
  bookForm.reset();
  showFormError(bookForm, "");
  bookForm.dataset.bookId = book ? book.id : "";
  $("#book-dialog-title").textContent = book ? "Edit book" : "Add a book";
  $(".new-only", bookForm).hidden = Boolean(book);
  fillOptions($("#f-status"), { "": "No status", ...STATUS }, "");
  fillOptions($("#f-format"), FORMATS, "paperback");
  $("#f-owned").checked = !book;
  syncOwnedFields();
  $("#isbn-lookup").hidden = !state.config.openlibrary;
  setIsbnHint(state.config.openlibrary ? "Type an ISBN and look it up to fill in the details." : "");
  if (book) {
    for (const name of ["isbn", "title", "subtitle", "series", "series_index", "publisher", "published_year", "pages", "language", "original_title", "original_language", "original_year", "translator", "cover_url", "description"]) {
      bookForm.elements[name].value = book[name] ?? "";
    }
    bookForm.elements.authors.value = book.authors.join(", ");
    bookForm.elements.tags.value = book.tags.join(", ");
  }
  bookDialog.showModal();
  (book ? $("#f-title") : $("#f-isbn")).focus();
  try {
    const authors = await api("/authors");
    $("#authors-list").innerHTML = authors.map((a) => `<option value="${esc(a.name)}"></option>`).join("");
  } catch { /* suggestions are optional */ }
}

function syncOwnedFields() {
  const owned = $("#f-owned").checked;
  for (const el of $$(".owned-only", bookForm)) el.hidden = !owned;
}

function setIsbnHint(text, isError = false) {
  const hint = $("#isbn-hint");
  hint.textContent = text;
  hint.hidden = !text;
  hint.classList.toggle("is-error", isError);
}

async function lookupIsbn() {
  const isbn = bookForm.elements.isbn.value.trim();
  if (!isbn) { setIsbnHint("Type an ISBN first.", true); return; }
  const button = $("#isbn-lookup");
  button.disabled = true;
  setIsbnHint("Asking Open Library…");
  try {
    const found = await api(`/lookup/isbn/${encodeURIComponent(isbn)}`);
    const els = bookForm.elements;
    let filled = 0;
    const fill = (name, value) => {
      if (value !== null && value !== undefined && value !== "" && !els[name].value) { els[name].value = value; filled++; }
    };
    els.isbn.value = found.isbn;
    for (const name of ["title", "subtitle", "publisher", "published_year", "pages", "cover_url"]) fill(name, found[name]);
    fill("authors", found.authors.join(", "));
    setIsbnHint(filled ? `Found “${found.title}”. Check the details below.` : "Found it, but your fields were already filled.");
  } catch (error) {
    setIsbnHint(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function numberOrNull(value) {
  return value === "" || value === null ? null : Number(value);
}

function bookPayload(isNew) {
  const els = bookForm.elements;
  const payload = {
    title: els.title.value.trim(),
    subtitle: els.subtitle.value.trim() || null,
    authors: splitList(els.authors.value),
    tags: splitList(els.tags.value),
    isbn: els.isbn.value.trim() || null,
    series: els.series.value.trim() || null,
    series_index: numberOrNull(els.series_index.value),
    publisher: els.publisher.value.trim() || null,
    published_year: numberOrNull(els.published_year.value),
    pages: numberOrNull(els.pages.value),
    language: els.language.value.trim() || null,
    original_title: els.original_title.value.trim() || null,
    original_language: els.original_language.value.trim() || null,
    original_year: numberOrNull(els.original_year.value),
    translator: els.translator.value.trim() || null,
    cover_url: els.cover_url.value.trim() || null,
    description: els.description.value.trim() || null,
  };
  if (isNew) {
    payload.status = els.status.value || null;
    payload.favorite = els.favorite.checked;
    if (payload.status === "read") payload.finished_on = today();
    if (payload.status === "reading") payload.started_on = today();
    payload.copies = els.owned.checked
      ? [{ format: els.format.value, location: els.location.value.trim() || null }]
      : [];
  }
  return payload;
}

async function submitBook(event) {
  event.preventDefault();
  const id = bookForm.dataset.bookId;
  const payload = bookPayload(!id);
  if (!payload.title) {
    showFormError(bookForm, "A book needs at least a title.");
    $("#f-title").focus();
    return;
  }
  const submit = $('button[type="submit"]', bookForm);
  submit.disabled = true;
  try {
    const book = id
      ? await api(`/books/${id}`, { method: "PATCH", body: payload })
      : await api("/books", { method: "POST", body: payload });
    bookDialog.close();
    toast(id ? "Book updated" : `“${book.title}” added to the library`);
    if (id && view.dataset.view === "book") renderBookDetail(book);
    else location.hash = `#/book/${book.id}`;
  } catch (error) {
    showFormError(bookForm, error.message);
  } finally {
    submit.disabled = false;
  }
}

// ------------------------------------------------------------ copy dialog --

const copyDialog = $("#copy-dialog");
const copyForm = $("#copy-form");

async function openCopyDialog(copy = null) {
  copyForm.reset();
  showFormError(copyForm, "");
  copyForm.dataset.copyId = copy ? copy.id : "";
  $("#copy-dialog-title").textContent = copy ? "Edit copy" : "Add a copy";
  fillOptions($("#c-format"), FORMATS, copy?.format || "paperback");
  const els = copyForm.elements;
  els.owner.value = copy?.owner || state.config.default_owner;
  els.location.value = copy?.location || "";
  els.acquired_on.value = copy?.acquired_on || "";
  els.lent_to.value = copy?.lent_to || "";
  els.notes.value = copy?.notes || "";
  copyDialog.showModal();
  try {
    const owners = await api("/owners");
    $("#owners-list").innerHTML = owners.map((o) => `<option value="${esc(o.name)}"></option>`).join("");
  } catch { /* optional */ }
}

async function submitCopy(event) {
  event.preventDefault();
  const els = copyForm.elements;
  const payload = {
    owner: els.owner.value.trim(),
    format: els.format.value,
    location: els.location.value.trim() || null,
    acquired_on: els.acquired_on.value || null,
    lent_to: els.lent_to.value.trim() || null,
    notes: els.notes.value.trim() || null,
  };
  if (!payload.owner) { showFormError(copyForm, "Who owns this copy?"); return; }
  const id = copyForm.dataset.copyId;
  try {
    if (id) await api(`/copies/${id}`, { method: "PATCH", body: payload });
    else await api(`/books/${state.book.id}/copies`, { method: "POST", body: payload });
    copyDialog.close();
    toast(id ? "Copy updated" : "Copy added");
    renderBookDetail(await api(`/books/${state.book.id}`));
  } catch (error) {
    showFormError(copyForm, error.message);
  }
}

// ------------------------------------------------------------------- stats --

async function renderStats() {
  setNav("stats");
  setTitle("Stats");
  view.dataset.view = "stats";
  view.innerHTML = '<p class="loading">Counting the seeds…</p>';
  const s = await api("/stats");
  if (!s.total_books) {
    view.innerHTML = `<div class="page-head"><div><h1>Stats</h1><span class="ornament"></span></div></div>
      <div class="empty"><h2>Nothing to count yet</h2><p>Stats appear once your library has some books.</p></div>`;
    return;
  }
  const tiles = [
    ["Books", s.total_books, ""],
    ["Owned", s.owned_books, ""],
    ["Copies", s.total_copies, ""],
    ["Favorites", s.favorites, "accent"],
    ["Average rating", s.average_rating ?? "–", "gold"],
    ["Pages read", s.pages_read.toLocaleString(), ""],
    ["Lent out", s.lent_out, "accent"],
  ];
  const ratingBars = Object.entries(s.rating_distribution).map(([k, count]) => ({
    label: Number(k) % 1 === 0 ? `${k}★` : "",
    tip: `${k} stars: ${plural(count, "book")}`,
    value: count,
  }));
  let yearBars = [];
  if (s.read_per_year.length) {
    const counts = Object.fromEntries(s.read_per_year.map((y) => [y.year, y.count]));
    const first = s.read_per_year[0].year;
    const last = s.read_per_year[s.read_per_year.length - 1].year;
    const every = Math.ceil((last - first + 1) / 10);
    for (let y = first; y <= last; y++) {
      yearBars.push({ label: (y - first) % every === 0 || y === last ? String(y) : "", tip: `${y}: ${plural(counts[y] || 0, "book")}`, value: counts[y] || 0 });
    }
  }
  const formats = Object.entries(s.copies_by_format)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => ({ name: FORMATS[k] || k, count: n }));

  view.innerHTML = `
    <div class="page-head"><div><h1>Stats</h1><p class="lede">A harvest of your reading.</p><span class="ornament" aria-hidden="true"></span></div></div>
    <div class="tiles">
      ${tiles.map(([label, value, tone]) => `<div class="tile ${tone}"><div class="tile-value">${esc(value)}</div><div class="tile-label">${esc(label)}</div></div>`).join("")}
    </div>
    <div class="stats-grid">
      <section class="panel">
        <h2>By status</h2>
        <div class="status-tiles">
          ${Object.entries(STATUS).map(([k, label]) => `<a class="status-tile" href="#/?status=${k}"><span>${esc(label)}</span><strong>${s.by_status[k] || 0}</strong></a>`).join("")}
        </div>
      </section>
      <section class="panel">
        <h2>Ratings</h2>
        <p class="chart-note">${plural(s.rated, "rated book")}, from half a star to five.</p>
        ${vbarsHtml(ratingBars, "Number of books by rating")}
      </section>
      <section class="panel">
        <h2>Books finished per year</h2>
        ${yearBars.length ? vbarsHtml(yearBars, "Books finished per year") : '<p class="chart-note">Set a “Finished” date on the books you read to see this chart.</p>'}
      </section>
      <section class="panel">
        <h2>Top authors</h2>
        ${hbarsHtml(s.top_authors, (name) => `#/?author=${encodeURIComponent(name)}`)}
      </section>
      <section class="panel">
        <h2>Top tags</h2>
        ${s.top_tags.length ? hbarsHtml(s.top_tags, (name) => `#/?tag=${encodeURIComponent(name)}`) : '<p class="chart-note">No tags yet.</p>'}
      </section>
      <section class="panel">
        <h2>Copies by owner</h2>
        ${s.copies_by_owner.length ? hbarsHtml(s.copies_by_owner, (name) => `#/?owner=${encodeURIComponent(name)}`) : '<p class="chart-note">No copies recorded yet.</p>'}
        ${formats.length ? `<h3 style="margin-top:18px">Copies by format</h3>${hbarsHtml(formats)}` : ""}
      </section>
    </div>`;
}

function vbarsHtml(bars, label) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  return `
    <div class="vbars" role="list" aria-label="${esc(label)}">
      ${bars.map((b) => {
        const pct = (b.value / max) * 100;
        return `<div class="vbar" role="listitem" tabindex="0" aria-label="${esc(b.tip)}" style="--fill:${pct / 100}">
          <span class="vbar-tip">${esc(b.tip)}</span>
          <span class="vbar-fill"></span>
          <span class="vbar-label" aria-hidden="true">${esc(b.label)}</span>
        </div>`;
      }).join("")}
    </div>
    <div class="chart-gap"></div>`;
}

function hbarsHtml(items, hrefFor = null) {
  const max = Math.max(1, ...items.map((i) => i.count));
  return `<ul class="hbars">${items.map((i) => `
    <li class="hbar">
      <span class="hbar-name" title="${esc(i.name)}">${hrefFor ? `<a href="${hrefFor(i.name)}">${esc(i.name)}</a>` : esc(i.name)}</span>
      <span class="hbar-track"><span class="hbar-fill" style="display:block;width:${(i.count / max) * 100}%"></span></span>
      <span class="hbar-value">${i.count}</span>
    </li>`).join("")}</ul>`;
}

// -------------------------------------------------------------------- help --

function renderHelp() {
  setNav("help");
  setTitle("Help");
  view.dataset.view = "help";
  view.innerHTML = `
    <div class="help">
      <header class="help-hero panel">
        <img class="help-logo" src="/static/favicon.svg" alt="" width="72" height="72">
        <div>
          <h1>Welcome to Persephone 🌿</h1>
          <p class="lede">Your personal home for <mark>every book you own, read, or dream of reading</mark>.
          No accounts, no fuss: everything stays on your computer.</p>
          <span class="ornament" aria-hidden="true"></span>
        </div>
      </header>

      <nav class="help-toc panel" aria-label="On this page">
        <strong>Jump to:</strong>
        <a href="#help-start">🚀 Getting started</a>
        <a href="#help-reading">📖 Tracking your reading</a>
        <a href="#help-copies">📚 Copies &amp; lending</a>
        <a href="#help-find">🔎 Finding books</a>
        <a href="#help-stats">📊 Stats</a>
        <a href="#help-backup">💾 Backups</a>
        <a href="#help-tips">✨ Handy tricks</a>
      </nav>

      <section class="help-section panel" id="help-start">
        <h2>🚀 Getting started: add your first book</h2>
        <ol class="help-steps">
          <li>Click the <span class="help-btn">＋ Add book</span> button at the top right.</li>
          <li>Got the book nearby? Type the <strong>ISBN</strong> (the number above the barcode) and press
            <span class="help-btn">Look up</span>. ✨ The title, author, cover and more <mark>fill themselves in</mark>.</li>
          <li>No ISBN? No problem. Just type the <strong>title</strong>. It's the only thing you <em>must</em> fill in.</li>
          <li>Tick <strong>“I own a copy”</strong> if it's on your shelf, then press <span class="help-btn">Save book</span>. 🎉</li>
        </ol>
        <p class="help-callout tip">💡 <strong>Tip:</strong> books you <em>don't</em> own yet go to your
          <mark>wishlist</mark> automatically. Perfect for “I want to buy this one”.</p>
      </section>

      <section class="help-section panel" id="help-reading">
        <h2>📖 Tracking your reading</h2>
        <p>Click any book to open its page. There you can:</p>
        <ul class="help-list">
          <li>🏷️ Pick a <strong>status</strong>:
            <span class="pill">To read</span> <span class="pill status-reading">Reading</span>
            <span class="pill status-read">Read</span> <span class="pill status-abandoned">Abandoned</span>.
            New books start with no status, and clicking the selected one again clears it.
            The <mark>start and finish dates are filled in for you</mark> when you start or finish a book.</li>
          <li>⭐ Give it a <strong>rating</strong> by clicking the stars. Half stars work too: click the left half of a star.</li>
          <li>❤️ Press <span class="help-btn">Add to favorites</span> to keep it on the <strong>Favorites</strong> tab.</li>
          <li>📝 Write a <strong>review or notes</strong>, then press <span class="help-btn">Save notes</span>.</li>
          <li>✏️ Fix any detail (title, tags, cover…) with <span class="help-btn">Edit</span>.</li>
        </ul>
      </section>

      <section class="help-section panel" id="help-copies">
        <h2>📚 Copies &amp; lending</h2>
        <p>Sharing a home library? Each book can have <mark>several copies, each with its own owner</mark>:
          your paperback, someone else's e-book, an audiobook…</p>
        <ul class="help-list">
          <li>➕ On a book's page, press <span class="help-btn">Add copy</span> and choose who owns it,
            the format and where it's kept (“living room shelf”, “Kindle”…).</li>
          <li>🤝 Lent a book to a friend? Edit the copy and fill in <strong>“Lent to”</strong>.
            It gets a <span class="pill lent">Lent</span> badge, so you'll <mark>never forget who has it</mark>.</li>
          <li>↩️ When it comes back, press the <strong>return</strong> arrow next to the copy.</li>
        </ul>
      </section>

      <section class="help-section panel" id="help-find">
        <h2>🔎 Finding books</h2>
        <ul class="help-list">
          <li>🔤 The <strong>search box</strong> looks through titles, authors, series, publishers and ISBNs.</li>
          <li>🎛️ Narrow the list by <strong>status</strong>, <strong>owned / wishlist / lent out</strong>,
            <strong>owner</strong> or <strong>tag</strong>.</li>
          <li>↕️ Change the <strong>order</strong>: title, author, rating, year, recently added…</li>
          <li>🖼️ Switch between <span class="help-btn">Covers</span> and <span class="help-btn">List</span> views.</li>
          <li>👤 Click an author's name or a tag to see <mark>all their books</mark>.</li>
        </ul>
        <p class="help-callout">🔖 Your filters are part of the page address, so you can <strong>bookmark</strong>
          a search (say, “unread fantasy”) and come back to it any time.</p>
      </section>

      <section class="help-section panel" id="help-stats">
        <h2>📊 Stats</h2>
        <p>The <strong>Stats</strong> tab shows your reading life at a glance: how many books you've read,
          <mark>books finished per year</mark>, how you rate them, your top authors and tags, and who owns what.</p>
      </section>

      <section class="help-section panel" id="help-backup">
        <h2>💾 Backups: keep your library safe</h2>
        <ul class="help-list">
          <li>📤 On the <strong>Backup</strong> tab, press <span class="help-btn">Download JSON</span>
            to save <mark>your whole library in one file</mark>. Keep it somewhere safe, like a USB drive or cloud folder.</li>
          <li>👥 Choose <strong>“Only &lt;name&gt;'s copies”</strong> to give someone just their part of a shared library.</li>
          <li>📥 To restore, drop that file on the <strong>Import</strong> box. Books you already have are updated, nothing is duplicated.</li>
        </ul>
        <p class="help-callout warn">⚠️ <strong>Do this every now and then!</strong> If the computer breaks,
          a recent backup is <mark>the only way to get your library back</mark>.</p>
        <p class="help-callout">📦 Coming from <strong>Goodreads</strong> or a spreadsheet? Those can be brought in too.
          Ask whoever set up Persephone for you to run the import.</p>
      </section>

      <section class="help-section panel" id="help-tips">
        <h2>✨ Handy tricks</h2>
        <ul class="help-list">
          <li>⌨️ Press <kbd>/</kbd> to jump straight to the search box.</li>
          <li>⌨️ Press <kbd>N</kbd> to add a new book.</li>
          <li>🌙 Prefer dark screens? The <strong>moon button</strong> at the top switches between light and dark.</li>
          <li>🏠 Click the <strong>Persephone</strong> logo at any time to go back to your library.</li>
        </ul>
      </section>

      <p class="help-outro">Happy reading! 📚🌸</p>
    </div>`;
}

// ------------------------------------------------------------------ backup --

async function renderBackup() {
  setNav("data");
  setTitle("Backup");
  view.dataset.view = "data";
  let owners = [];
  try { owners = await api("/owners"); } catch { /* optional */ }
  view.innerHTML = `
    <div class="page-head"><div><h1>Backup</h1><p class="lede">Take your library with you, or bring it back.</p><span class="ornament" aria-hidden="true"></span></div></div>
    <div class="backup-grid">
      <section class="panel">
        <h2>${icon("download")} Export</h2>
        <p>Download everything as a JSON file: books, ratings, favorites, notes, tags and copies.</p>
        <div class="field">
          <label for="export-owner">Copies to include</label>
          <select id="export-owner">
            <option value="">Everybody's copies</option>
            ${owners.map((o) => `<option value="${esc(o.name)}">Only ${esc(o.name)}'s copies</option>`).join("")}
          </select>
        </div>
        <div><a class="btn btn-primary" id="export-link" href="/api/export" download>${icon("download")} Download JSON</a></div>
        <p class="hint">From a terminal: <span class="code">make export-data</span> or <span class="code">make export-data OWNER=name</span>.</p>
      </section>
      <section class="panel">
        <h2>${icon("upload")} Import</h2>
        <p>Restore a Persephone export. Matching books are updated with the file's ratings, status and notes; new ones are added.</p>
        <label class="drop" id="drop">
          <input type="file" id="import-file" accept="application/json,.json" class="visually-hidden">
          <strong>Choose a .json file</strong> or drop it here
        </label>
        <div class="import-result" aria-live="polite"></div>
        <p class="hint">From a terminal: <span class="code">make import-data FILE=…</span>. To import Goodreads or CSV files, put them in <span class="code">info/</span> and run <span class="code">make import</span>.</p>
      </section>
    </div>`;

  const exportLink = $("#export-link", view);
  $("#export-owner", view).addEventListener("change", (e) => {
    exportLink.href = e.target.value ? `/api/export?owner=${encodeURIComponent(e.target.value)}` : "/api/export";
  });
  const drop = $("#drop", view);
  const input = $("#import-file", view);
  input.addEventListener("change", () => input.files[0] && importFile(input.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("is-over");
    if (e.dataTransfer.files[0]) importFile(e.dataTransfer.files[0]);
  });
}

async function importFile(file) {
  const result = $(".import-result", view);
  result.textContent = `Reading ${file.name}…`;
  let payload;
  try {
    payload = JSON.parse(await file.text());
  } catch {
    result.innerHTML = `<p class="form-error">${esc(file.name)} is not a valid JSON file.</p>`;
    return;
  }
  try {
    const s = await api("/import", { method: "POST", body: payload });
    result.innerHTML = `<p><strong>${s.created}</strong> added, <strong>${s.updated}</strong> updated, <strong>${s.unchanged}</strong> unchanged.</p>
      ${s.errors.length ? `<p>${plural(s.errors.length, "record")} could not be imported:</p><ul>${s.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}`;
    toast("Import finished");
  } catch (error) {
    result.innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
  }
}

// ------------------------------------------------------------------- theme --

function effectiveTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function syncThemeButton() {
  const button = $("#theme-toggle");
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  button.innerHTML = icon(next === "dark" ? "moon" : "sun");
  button.setAttribute("aria-label", `Switch to ${next} theme`);
  button.title = `Switch to ${next} theme`;
}

// ------------------------------------------------------------ save & exit --

async function saveAndExit() {
  const ok = await confirmDialog(
    "Save and exit Persephone? Everything you changed is already saved. This stops the app and closes this tab.",
    "Save and Exit",
  );
  if (!ok) return;
  try {
    await api("/shutdown", { method: "POST" });
  } catch (error) {
    toast(error.message, { error: true });
    return;
  }
  for (const button of document.querySelectorAll(".topbar button")) button.disabled = true;
  setTitle("Closed");
  view.dataset.view = "closed";
  view.innerHTML = `
    <div class="empty">
      <h2>See you soon</h2>
      <p>Persephone has stopped and your library is saved. If this tab stays open, you can close it.</p>
    </div>`;
  // Browsers only let a page close tabs that a script opened; otherwise this does nothing.
  window.close();
}

// ------------------------------------------------------------------ events --

document.addEventListener("click", async (event) => {
  const target = event.target;

  const fav = target.closest("[data-fav]");
  if (fav) { event.preventDefault(); toggleFavorite(fav); return; }

  const statusButton = target.closest("[data-set-status]");
  if (statusButton) {
    // Clicking the active status again clears it: no status is the default.
    const chosen = statusButton.dataset.setStatus;
    setStatus(chosen === state.book.status ? null : chosen);
    return;
  }

  if (target.closest("[data-close]")) { target.closest("dialog")?.close(); return; }

  const editCopy = target.closest("[data-edit-copy]");
  if (editCopy) { openCopyDialog(state.book.copies.find((c) => c.id === Number(editCopy.dataset.editCopy))); return; }

  const returnCopy = target.closest("[data-return-copy]");
  if (returnCopy) {
    try {
      await api(`/copies/${returnCopy.dataset.returnCopy}`, { method: "PATCH", body: { lent_to: null } });
      renderBookDetail(await api(`/books/${state.book.id}`));
      toast("Welcome home, little book");
    } catch (error) { toast(error.message, { error: true }); }
    return;
  }

  const deleteCopy = target.closest("[data-delete-copy]");
  if (deleteCopy) {
    if (!(await confirmDialog("Delete this copy? The book stays in your library."))) return;
    try {
      await api(`/copies/${deleteCopy.dataset.deleteCopy}`, { method: "DELETE" });
      renderBookDetail(await api(`/books/${state.book.id}`));
      toast("Copy deleted");
    } catch (error) { toast(error.message, { error: true }); }
    return;
  }

  const action = target.closest("[data-action]")?.dataset.action;
  if (action === "add-book") openBookDialog();
  else if (action === "edit-book") openBookDialog(state.book);
  else if (action === "add-copy") openCopyDialog();
  else if (action === "clear-rating") patchBook({ rating: null }, "Rating cleared");
  else if (action === "delete-book") {
    const book = state.book;
    if (!(await confirmDialog(`Delete “${book.title}” and its ${plural(book.copies.length, "copy", "copies")}? This cannot be undone.`))) return;
    try {
      await api(`/books/${book.id}`, { method: "DELETE" });
      toast(`“${book.title}” deleted`);
      state.library = null;
      location.hash = "#/";
    } catch (error) { toast(error.message, { error: true }); }
  }
});

document.addEventListener("change", (event) => {
  const dateInput = event.target.closest("[data-date]");
  if (dateInput) patchBook({ [dateInput.dataset.date]: dateInput.value || null }, "Date saved");
  if (event.target.id === "f-owned") syncOwnedFields();
});

document.addEventListener("submit", (event) => {
  if (event.target.id === "review-form") {
    event.preventDefault();
    patchBook({ review: event.target.elements.review.value.trim() || null }, "Notes saved");
  }
});

// Broken cover images fall back to the generated cover underneath.
document.addEventListener("error", (event) => {
  if (event.target instanceof HTMLImageElement && event.target.hasAttribute("data-cover")) event.target.remove();
}, true);

document.addEventListener("keydown", (event) => {
  const typing = event.target.closest("input, textarea, select, [contenteditable]");
  if (typing || event.metaKey || event.ctrlKey || event.altKey || $("dialog[open]")) return;
  if (event.key === "/") {
    const search = $('input[name="q"]', view);
    if (search) { event.preventDefault(); search.focus(); }
  } else if (event.key === "n") {
    event.preventDefault();
    openBookDialog();
  }
});

$("#add-book").addEventListener("click", () => openBookDialog());
$("#isbn-lookup").addEventListener("click", lookupIsbn);
$("#f-isbn").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); lookupIsbn(); } });
bookForm.addEventListener("submit", submitBook);
copyForm.addEventListener("submit", submitCopy);

$("#save-exit").addEventListener("click", saveAndExit);
$("#theme-toggle").addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  storage.set("persephone.theme", next);
  syncThemeButton();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncThemeButton);

// Fires for back/forward and for links to a new #hash (pushState calls route() itself).
window.addEventListener("popstate", route);

// ------------------------------------------------------------------- start --

(async function start() {
  syncThemeButton();
  try {
    state.config = await api("/config");
  } catch { /* keep the defaults */ }
  route();
})();
