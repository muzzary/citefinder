/* ===========================================================================
   CiteFinder SPA. Hash-routed: #/ (home) · #/chats (list) · #/chat/:id (chat).
   Talks only to app.py's JSON API; all grounding/citation logic lives server-side.
   =========================================================================== */

const API = {
  async listChats() { return get("/api/chats"); },
  async createChat(title) { return post("/api/chats", { title }); },
  async rename(id, title) { return patch(`/api/chats/${id}`, { title }); },
  async deleteChat(id) { return del(`/api/chats/${id}`); },
  async chatSources(id) { return get(`/api/chats/${id}/sources`); },
  async messages(id) { return get(`/api/chats/${id}/messages`); },
  async ask(id, question) { return post(`/api/chats/${id}/ask`, { question }); },
  async upload(id, files) {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    const r = await fetch(`/api/chats/${id}/upload`, { method: "POST", body: fd });
    if (!r.ok) throw new Error((await safeJson(r))?.detail || `Upload failed (${r.status})`);
    return r.json();
  },
  async source(id) { return get(`/api/sources/${id}`); },
  async confirm(id, body) { return post(`/api/sources/${id}/confirm`, body); },
  async cite(id, page, style) { return post(`/api/sources/${id}/cite`, { page, style }); },
};

async function get(url) { return handle(await fetch(url)); }
async function post(url, body) { return handle(await fetch(url, { method: "POST", headers: json(), body: JSON.stringify(body) })); }
async function patch(url, body) { return handle(await fetch(url, { method: "PATCH", headers: json(), body: JSON.stringify(body) })); }
async function del(url) { return handle(await fetch(url, { method: "DELETE" })); }
function json() { return { "Content-Type": "application/json" }; }
async function handle(r) {
  if (!r.ok) { const e = await safeJson(r); throw new Error(e?.detail || `Request failed (${r.status})`); }
  return r.json();
}
async function safeJson(r) { try { return await r.json(); } catch { return null; } }

/* ---- small helpers ------------------------------------------------------- */
const app = document.getElementById("app");
const esc = (s) => (s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id) => `<svg class="icon"><use href="#${id}"/></svg>`;
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " · " +
         d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/* ---- toasts -------------------------------------------------------------- */
const toastHost = document.getElementById("toast-host");
function toast(msg, kind = "ok", persist = false) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  const glyph = kind === "work" ? `<span class="spin"></span>`
              : kind === "err" ? icon("i-x") : icon("i-check");
  el.innerHTML = `${glyph}<span>${esc(msg)}</span>`;
  toastHost.appendChild(el);
  const remove = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 300); };
  if (!persist) setTimeout(remove, 3600);
  return remove;
}

/* ---- modal ---------------------------------------------------------------- */
function modal(title, bodyNode, opts = {}) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-head">
        <span class="modal-title">${esc(title)}</span>
        <button class="btn btn-quiet modal-x" aria-label="Close">${icon("i-x")}</button>
      </div>
      <div class="modal-body"></div>
    </div>`;
  overlay.querySelector(".modal-body").appendChild(bodyNode);
  document.body.appendChild(overlay);

  const close = () => { overlay.classList.add("leaving"); setTimeout(() => overlay.remove(), 200); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  overlay.querySelector(".modal-x").onclick = close;
  overlay.addEventListener("click", (e) => { if (e.target === overlay && opts.dismissable !== false) close(); });
  return { overlay, close };
}

/* ===========================================================================
   ROUTER
   =========================================================================== */
function router() {
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/chat\/(\d+)/);
  if (m) return renderChat(parseInt(m[1], 10));
  if (hash.startsWith("#/chats")) return renderList();
  return renderHome();
}
window.addEventListener("hashchange", router);
window.addEventListener("DOMContentLoaded", router);

function go(hash) { location.hash = hash; }
async function startNewChat() {
  try {
    const { id } = await API.createChat(null);
    go(`#/chat/${id}`);
  } catch (e) { toast(e.message, "err"); }
}

/* ===========================================================================
   HOME
   =========================================================================== */
function renderHome() {
  app.innerHTML = `
    <div class="home">
      <header class="home-top">
        <div class="brand">
          <span class="brand-mark">${icon("i-quote")}</span>
          <span class="brand-name">CiteFinder</span>
        </div>
        <span class="home-tag">local · grounded · your material only</span>
      </header>

      <main class="home-center">
        <div class="home-eyebrow">Ask your own documents</div>
        <h1 class="home-h1">
          <span class="tw-line"><span id="tw1"></span><span class="tw-cursor" id="twc"></span></span>
          <span class="tw-line l2"><span id="tw2"></span></span>
        </h1>
        <p class="home-sub">Drop your readings into a chat and ask in plain words. Every answer points back to the file and page it came from, and turns into a formatted citation only when you confirm the details.</p>

        <div class="home-options">
          <button class="option-card is-primary" id="opt-new">
            <span class="option-glyph">${icon("i-plus")}</span>
            <span class="option-body">
              <span class="option-title">Start a new chat</span>
              <span class="option-desc">Open a fresh workspace, add a file or a folder, and start asking.</span>
              <span class="option-go">Begin ${icon("i-arrow")}</span>
            </span>
          </button>

          <button class="option-card" id="opt-prev">
            <span class="option-glyph">${icon("i-stack")}</span>
            <span class="option-body">
              <span class="option-title">See previous chats</span>
              <span class="option-desc">Return to a corpus you've already built and pick up where you left off.</span>
              <span class="option-go">Browse ${icon("i-arrow")}</span>
            </span>
          </button>
        </div>
      </main>

      <footer class="home-foot">
        <span><span class="dot"></span> pgvector hybrid retrieval</span>
        <span><span class="dot"></span> answers only from your files</span>
        <span><span class="dot"></span> APA · Harvard · IEEE on confirm</span>
      </footer>
    </div>`;
  document.getElementById("opt-new").onclick = startNewChat;
  document.getElementById("opt-prev").onclick = () => go("#/chats");
  typewriter();
}

// Types the hero headline, then moves the caret to the second (muted) line.
function typewriter() {
  const line1 = "Find where it's written.";
  const line2 = "Then cite it right.";
  const e1 = document.getElementById("tw1");
  const e2 = document.getElementById("tw2");
  const cur = document.getElementById("twc");
  if (!e1 || !e2 || !cur) return;
  const SPEED = 52;
  let i = 0, j = 0;
  const step1 = () => {
    e1.textContent = line1.slice(0, i);
    if (i++ < line1.length) return setTimeout(step1, SPEED);
    document.querySelector(".l2").appendChild(cur);   // caret jumps to line 2
    setTimeout(step2, 240);
  };
  const step2 = () => {
    e2.textContent = line2.slice(0, j);
    if (j++ < line2.length) setTimeout(step2, SPEED);
  };
  step1();
}

/* ===========================================================================
   PREVIOUS-CHATS LIST
   =========================================================================== */
async function renderList() {
  app.innerHTML = `
    <div class="list-page">
      <div class="list-top">
        <div class="list-head">
          <h1>Your chats</h1>
          <p>Each chat owns its own corpus. Open one to keep asking within it.</p>
        </div>
        <div style="display:flex;gap:10px">
          <button class="btn btn-ghost" id="l-home">${icon("i-back")} Home</button>
          <button class="btn btn-primary" id="l-new">${icon("i-plus")} New chat</button>
        </div>
      </div>
      <div id="l-body"></div>
    </div>`;
  document.getElementById("l-home").onclick = () => go("#/");
  document.getElementById("l-new").onclick = startNewChat;

  const body = document.getElementById("l-body");
  body.innerHTML = `<div class="skel" style="max-width:1180px">
      <div class="skel-line w1"></div><div class="skel-line w2"></div><div class="skel-line w3"></div></div>`;

  let chats;
  try { chats = await API.listChats(); }
  catch (e) { body.innerHTML = `<p class="panel-err">${esc(e.message)}</p>`; return; }

  if (!chats.length) {
    body.innerHTML = `
      <div class="list-empty">
        <div class="empty-glyph">${icon("i-stack")}</div>
        <div class="empty-title">No chats yet</div>
        <div class="empty-sub">Start one, add your readings, and it'll show up here.</div>
        <button class="btn btn-primary" id="l-new2">${icon("i-plus")} Start a new chat</button>
      </div>`;
    document.getElementById("l-new2").onclick = startNewChat;
    return;
  }

  body.innerHTML = `<div class="list-grid">${chats.map((c, i) => `
      <button class="list-card" data-id="${c.id}">
        <div class="list-card-top">
          <span class="list-num">${String(i + 1).padStart(2, "0")}</span>
          ${icon("i-quote")}
        </div>
        <div class="list-card-title">${esc(c.title || "Untitled chat")}</div>
        <div class="list-card-foot">
          <span>${esc(fmtDate(c.created_at))}</span>
          <span class="open">Open ${icon("i-arrow")}</span>
        </div>
      </button>`).join("")}</div>`;
  body.querySelectorAll(".list-card").forEach((el) =>
    (el.onclick = () => go(`#/chat/${el.dataset.id}`)));
}

/* ===========================================================================
   CHAT VIEW
   =========================================================================== */
async function renderChat(chatId) {
  app.innerHTML = `
    <div class="chat-shell">
      <aside class="sidebar">
        <div class="side-head">
          <div class="side-brand" id="c-brand">
            <span class="brand-mark">${icon("i-quote")}</span>
            <span class="brand-name">CiteFinder</span>
          </div>
          <button class="btn btn-primary side-new" id="c-new">${icon("i-plus")} New chat</button>
        </div>
        <div class="side-label">Previous chats</div>
        <nav class="side-list" id="c-side"></nav>
      </aside>

      <section class="main">
        <div class="main-top">
          <div class="main-top-l">
            <button class="btn btn-quiet main-back" id="c-back">${icon("i-back")}</button>
            <span class="main-title" id="c-title">Chat</span>
          </div>
          <div class="main-actions">
            <button class="btn btn-ghost" id="c-files">${icon("i-folder")} <span id="c-files-n">Files</span></button>
            <button class="btn btn-quiet" id="c-rename" title="Rename chat">${icon("i-pencil")}</button>
            <button class="btn btn-quiet" id="c-delete" title="Delete chat">${icon("i-trash")}</button>
          </div>
        </div>

        <div class="thread" id="c-thread"><div class="thread-inner" id="c-inner"></div></div>

        <div class="composer">
          <div class="composer-inner">
            <div class="add-row">
              <button class="btn btn-ghost" id="c-addfiles">${icon("i-file")} Add files</button>
              <button class="btn btn-ghost" id="c-addfolder">${icon("i-folder")} Add folder</button>
              <span class="hint">PDFs · text-based</span>
            </div>
            <div class="ask-box">
              <textarea id="c-input" rows="1" placeholder="Ask a question about your material…"></textarea>
              <button class="send-btn" id="c-send" title="Send">${icon("i-send")}</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <input type="file" id="c-file-files" accept="application/pdf,.pdf" multiple class="hidden" />
    <input type="file" id="c-file-folder" webkitdirectory directory multiple class="hidden" />`;

  // wiring
  document.getElementById("c-brand").onclick = () => go("#/");
  document.getElementById("c-back").onclick = () => go("#/chats");
  document.getElementById("c-new").onclick = startNewChat;
  document.getElementById("c-files").onclick = () => openFilesModal(chatId);
  document.getElementById("c-rename").onclick = () => startRename(chatId);
  document.getElementById("c-delete").onclick = () => confirmDeleteChat(chatId);

  const filesInput = document.getElementById("c-file-files");
  const folderInput = document.getElementById("c-file-folder");
  document.getElementById("c-addfiles").onclick = () => filesInput.click();
  document.getElementById("c-addfolder").onclick = () => folderInput.click();
  filesInput.onchange = () => handleUpload(chatId, filesInput.files, "files");
  folderInput.onchange = () => handleUpload(chatId, folderInput.files, "folder");

  // composer behaviour
  const input = document.getElementById("c-input");
  const send = document.getElementById("c-send");
  const grow = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; };
  input.addEventListener("input", grow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitAsk(chatId); }
  });
  send.onclick = () => submitAsk(chatId);

  await refreshSidebar(chatId);
  await loadThread(chatId);
  refreshFilesCount(chatId);
}

async function refreshFilesCount(chatId) {
  const el = document.getElementById("c-files-n");
  if (!el) return;
  try {
    const src = await API.chatSources(chatId);
    el.textContent = src.length ? `Files · ${src.length}` : "Files";
  } catch { /* non-critical */ }
}

/* ---- rename (inline title edit) ----------------------------------------- */
function startRename(chatId) {
  const titleEl = document.getElementById("c-title");
  if (!titleEl || titleEl.dataset.editing === "1") return;
  const current = titleEl.textContent;
  titleEl.dataset.editing = "1";
  titleEl.innerHTML = `<input class="title-input" id="c-title-input" value="${esc(current === "New chat" ? "" : current)}" placeholder="Chat name" />`;
  const input = document.getElementById("c-title-input");
  input.focus(); input.select();

  const commit = async () => {
    const v = input.value.trim();
    titleEl.dataset.editing = "0";
    if (!v || v === current) { titleEl.textContent = current; return; }
    titleEl.textContent = v;
    try { await API.rename(chatId, v); refreshSidebar(chatId); toast("Chat renamed.", "ok"); }
    catch (e) { titleEl.textContent = current; toast(e.message, "err"); }
  };
  const cancel = () => { titleEl.dataset.editing = "0"; titleEl.textContent = current; };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    else if (e.key === "Escape") { input.value = current; cancel(); }
  });
  input.addEventListener("blur", commit, { once: true });
}

/* ---- delete (confirm modal) --------------------------------------------- */
function confirmDeleteChat(chatId) {
  const body = document.createElement("div");
  body.className = "confirm-box";
  body.innerHTML = `
    <p class="confirm-text">Delete this chat and everything in it: its files, embeddings, and history? This can't be undone.</p>
    <div class="confirm-actions">
      <button class="btn btn-quiet" id="d-cancel">Cancel</button>
      <button class="btn btn-danger" id="d-go">${icon("i-trash")} Delete chat</button>
    </div>`;
  const m = modal("Delete chat", body);
  body.querySelector("#d-cancel").onclick = m.close;
  body.querySelector("#d-go").onclick = async () => {
    const go2 = body.querySelector("#d-go");
    go2.disabled = true;
    try {
      await API.deleteChat(chatId);
      m.close();
      toast("Chat deleted.", "ok");
      go("#/chats");
    } catch (e) { toast(e.message, "err"); go2.disabled = false; }
  };
}

/* ---- files in this chat (modal) ----------------------------------------- */
async function openFilesModal(chatId) {
  const body = document.createElement("div");
  body.className = "files-box";
  body.innerHTML = `<div class="thinking"><span class="spin"></span> loading files…</div>`;
  const m = modal("Files in this chat", body);

  let sources;
  try { sources = await API.chatSources(chatId); }
  catch (e) { body.innerHTML = `<p class="panel-err">${esc(e.message)}</p>`; return; }

  if (!sources.length) {
    body.innerHTML = `<div class="files-empty">${icon("i-file")}<span>No files yet. Close this and use “Add files” or “Add folder”.</span></div>`;
    return;
  }
  body.innerHTML = `<div class="files-list">${sources.map((s) => fileRow(s)).join("")}</div>`;
  body.querySelectorAll(".file-confirm").forEach((btn) => {
    btn.onclick = () => openFileConfirm(body, parseInt(btn.dataset.id, 10), sources);
  });
}

function fileRow(s) {
  const citable = s.kind === "work";
  const badge = !citable ? `<span class="fbadge notes">notes (locator only)</span>`
    : s.confirmed ? `<span class="fbadge ok">${icon("i-check")} citable</span>`
    : `<span class="fbadge pending">confirm to cite</span>`;
  const action = (citable && !s.confirmed)
    ? `<button class="btn btn-ghost file-confirm" data-id="${s.id}">${icon("i-quote")} Confirm details</button>` : "";
  return `
    <div class="file-row" id="file-${s.id}">
      <div class="file-main">
        <span class="file-ico">${icon("i-file")}</span>
        <div class="file-meta">
          <span class="file-name">${esc(s.title || s.filename)}</span>
          <span class="file-sub">${esc(s.filename)} · ${s.n_chunks} chunks</span>
        </div>
      </div>
      <div class="file-right">${badge}${action}</div>
    </div>`;
}

function openFileConfirm(body, sourceId, sources) {
  const row = body.querySelector(`#file-${sourceId}`);
  const src = sources.find((s) => s.id === sourceId) || {};
  if (row.querySelector(".cite-panel")) { row.querySelector(".cite-panel").remove(); return; }
  const form = confirmForm({
    source: { id: sourceId, author: src.author, title: src.title, year: src.year },
    onConfirmed: (updated) => {
      const right = row.querySelector(".file-right");
      right.innerHTML = `<span class="fbadge ok">${icon("i-check")} citable</span>`;
      src.confirmed = true;
      toast(`"${updated.title}" confirmed. Citable in any style.`, "ok");
    },
    onCancel: () => row.querySelector(".cite-panel")?.remove(),
  });
  row.appendChild(form);
}

async function refreshSidebar(activeId) {
  const side = document.getElementById("c-side");
  if (!side) return;
  let chats = [];
  try { chats = await API.listChats(); } catch { /* sidebar is non-critical */ }

  if (!chats.length) { side.innerHTML = `<div class="side-empty">No previous chats yet.</div>`; return; }
  side.innerHTML = chats.map((c) => `
    <button class="chat-item ${c.id === activeId ? "active" : ""}" data-id="${c.id}">
      <span class="chat-item-title">${esc(c.title || "Untitled chat")}</span>
      <span class="chat-item-meta">${esc(fmtDate(c.created_at))}</span>
    </button>`).join("");
  side.querySelectorAll(".chat-item").forEach((el) =>
    (el.onclick = () => go(`#/chat/${el.dataset.id}`)));

  // title in the header
  const active = chats.find((c) => c.id === activeId);
  const titleEl = document.getElementById("c-title");
  if (titleEl) titleEl.textContent = active?.title || "New chat";
}

async function loadThread(chatId) {
  const inner = document.getElementById("c-inner");
  if (!inner) return;
  let msgs;
  try { msgs = await API.messages(chatId); }
  catch (e) { inner.innerHTML = `<p class="panel-err">${esc(e.message)}</p>`; return; }

  if (!msgs.length) { renderEmptyThread(chatId); return; }

  inner.innerHTML = "";
  for (const m of msgs) inner.appendChild(renderMessage(m));
  scrollThread();
}

function renderEmptyThread(chatId) {
  const thread = document.getElementById("c-thread");
  thread.innerHTML = `
    <div class="empty">
      <div class="empty-glyph">${icon("i-spark")}</div>
      <div class="empty-title">This chat is empty</div>
      <div class="empty-sub">Add a file or a folder of PDFs, then ask anything. Answers stay grounded in just what you add here.</div>
      <div class="empty-actions">
        <button class="btn btn-primary" id="e-files">${icon("i-file")} Add files</button>
        <button class="btn btn-ghost" id="e-folder">${icon("i-folder")} Add folder</button>
      </div>
    </div>`;
  document.getElementById("e-files").onclick = () => document.getElementById("c-file-files").click();
  document.getElementById("e-folder").onclick = () => document.getElementById("c-file-folder").click();
}

/* ---- rendering a single message ----------------------------------------- */
function renderMessage(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  if (m.role === "user") {
    el.innerHTML = `<div class="msg-role">You</div>
      <div class="bubble-user">${esc(m.content)}</div>`;
    return el;
  }
  // Refusal styling comes from the server's decision, not a re-derived string
  // match: live answers carry res.refused; replayed turns are refusals exactly
  // when they have no attribution (an answered turn always has >=1 Locator).
  const hasAttrib = Array.isArray(m.attribution) && m.attribution.length;
  const refused = (m.refused !== undefined && m.refused !== null) ? m.refused : !hasAttrib;
  el.innerHTML = `
    <div class="msg-role">${icon("i-spark")} CiteFinder</div>
    <div class="answer-text ${refused ? "refused" : ""}">${esc(m.content)}</div>`;
  if (hasAttrib) {
    el.appendChild(renderAttribution(m.attribution));
  }
  return el;
}

function renderAttribution(items) {
  const wrap = document.createElement("div");
  wrap.className = "attrib";
  wrap.innerHTML = `<div class="attrib-label">Where this comes from</div>`;
  for (const a of items) wrap.appendChild(renderLocator(a));
  return wrap;
}

function renderLocator(a) {
  const el = document.createElement("div");
  el.className = "locator";
  const citable = a.kind === "work";
  // Slim by design: just the honest attribution (file name + page) and the
  // cite action. Summary/context were too bulky for a list of sources.
  el.innerHTML = `
    <div class="loc-head">
      <span class="loc-pin">${icon("i-pin")}</span>
      <span class="loc-file">${esc(a.title || a.filename)}</span>
      <span class="loc-page">p. ${esc(String(a.page))}</span>
      <div class="loc-actions"></div>
    </div>
    <div class="cite-slot"></div>`;

  const actions = el.querySelector(".loc-actions");
  if (citable && a.source_id != null) {
    const btn = document.createElement("button");
    btn.className = "cite-btn";
    btn.innerHTML = `${icon("i-quote")} Cite this source`;
    btn.onclick = () => openCitePanel(el, a);
    actions.appendChild(btn);
  } else {
    actions.innerHTML = `<span class="cite-tag">notes (locator only)</span>`;
  }
  return el;
}

/* ---- the cite flow: confirm (if needed) -> style picker -> citation ------ */
async function openCitePanel(locEl, a) {
  const slot = locEl.querySelector(".cite-slot");
  if (slot.dataset.open === "1") { slot.innerHTML = ""; slot.dataset.open = "0"; return; }
  slot.dataset.open = "1";
  slot.innerHTML = `<div class="cite-panel"><div class="thinking"><span class="spin"></span> checking source…</div></div>`;

  let src;
  try { src = await API.source(a.source_id); }
  catch (e) { slot.innerHTML = `<div class="cite-panel"><span class="panel-err">${esc(e.message)}</span></div>`; return; }

  if (src.confirmed) buildStylePicker(slot, a.source_id, a.page, src);
  else buildConfirmForm(slot, a, src);
}

// Reusable confirm form (author/title/year). Used by the locator cite flow and
// the files modal. Returns a .cite-panel node; callers handle what comes next.
function confirmForm({ source, onConfirmed, onCancel }) {
  const panel = document.createElement("div");
  panel.className = "cite-panel";
  panel.innerHTML = `
    <div class="cite-panel-title">Confirm details to cite. Locked once saved.</div>
    <div class="field"><label>Author</label><input class="f-author" placeholder="e.g. Khan, H. M. H." value="${esc(source.author || "")}" /></div>
    <div class="field"><label>Title</label><input class="f-title" placeholder="Work title" value="${esc(source.title || "")}" /></div>
    <div class="field-row"><div class="field"><label>Year</label><input class="f-year" placeholder="2025" value="${esc(source.year || "")}" /></div></div>
    <div class="cite-panel-actions">
      <button class="btn btn-primary f-save">${icon("i-check")} Confirm</button>
      <button class="btn btn-quiet f-cancel">Cancel</button>
      <span class="panel-err hidden f-err"></span>
    </div>`;
  panel.querySelector(".f-cancel").onclick = () => onCancel && onCancel();
  panel.querySelector(".f-save").onclick = async () => {
    const author = panel.querySelector(".f-author").value.trim();
    const title = panel.querySelector(".f-title").value.trim();
    const year = panel.querySelector(".f-year").value.trim();
    const err = panel.querySelector(".f-err");
    if (!author || !year || !title) {
      err.textContent = "Author, year, and a real title are all required to cite.";
      err.classList.remove("hidden"); return;
    }
    const save = panel.querySelector(".f-save");
    save.disabled = true; save.innerHTML = `<span class="mini-spin"></span> Saving`;
    try {
      const updated = await API.confirm(source.id, { author, title, year });
      if (!updated.confirmed) throw new Error("Still locator-only. Give a real title (not the file name), author, and year.");
      onConfirmed && onConfirmed(updated);
    } catch (e) {
      err.textContent = e.message; err.classList.remove("hidden");
      save.disabled = false; save.innerHTML = `${icon("i-check")} Confirm`;
    }
  };
  return panel;
}

function buildConfirmForm(slot, a, src) {
  const form = confirmForm({
    source: { id: a.source_id, author: src.author, title: src.title || a.title, year: src.year },
    onConfirmed: (updated) => {
      a.confirmed = true;
      toast(`"${updated.title}" confirmed. Now citable in any style.`, "ok");
      buildStylePicker(slot, a.source_id, a.page, updated);
    },
    onCancel: () => { slot.innerHTML = ""; slot.dataset.open = "0"; },
  });
  slot.innerHTML = "";
  slot.appendChild(form);
}

function buildStylePicker(slot, sourceId, page, src) {
  const panel = document.createElement("div");
  panel.className = "cite-panel";
  panel.innerHTML = `
    <div class="cite-panel-title">Cite ${esc(src.title || "source")} · choose a style</div>
    <div class="style-chips">
      <button class="chip sel" data-style="APA">APA</button>
      <button class="chip" data-style="Harvard">Harvard</button>
      <button class="chip" data-style="IEEE">IEEE</button>
    </div>
    <div class="citation-out hidden" id="cite-out"></div>
    <div class="cite-panel-actions">
      <button class="btn btn-primary" id="c-make">${icon("i-quote")} Make citation</button>
      <button class="btn btn-quiet" id="c-close">Close</button>
      <span class="panel-err hidden" id="c-err"></span>
    </div>`;
  slot.innerHTML = "";
  slot.appendChild(panel);

  let style = "APA";
  panel.querySelectorAll(".chip").forEach((chip) => {
    chip.onclick = () => {
      panel.querySelectorAll(".chip").forEach((c) => c.classList.remove("sel"));
      chip.classList.add("sel");
      style = chip.dataset.style;
    };
  });
  panel.querySelector("#c-close").onclick = () => { slot.innerHTML = ""; slot.dataset.open = "0"; };
  panel.querySelector("#c-make").onclick = async () => {
    const out = panel.querySelector("#cite-out");
    const err = panel.querySelector("#c-err");
    err.classList.add("hidden");
    try {
      const { citation, style: st } = await API.cite(sourceId, page, style);
      out.innerHTML = `<div class="citation-style">${esc(st)} · p. ${esc(String(page))}</div>
        <div class="citation-text">${esc(citation)}</div>`;
      out.classList.remove("hidden");
    } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
  };
}

/* ---- asking -------------------------------------------------------------- */
let asking = false;
async function submitAsk(chatId) {
  if (asking) return;
  const input = document.getElementById("c-input");
  const q = input.value.trim();
  if (!q) return;

  asking = true;
  document.getElementById("c-send").disabled = true;

  // ensure we have a thread container (replace empty state if present)
  let inner = document.getElementById("c-inner");
  if (!inner) {
    const thread = document.getElementById("c-thread");
    thread.innerHTML = `<div class="thread-inner" id="c-inner"></div>`;
    inner = document.getElementById("c-inner");
  }

  inner.appendChild(renderMessage({ role: "user", content: q }));
  input.value = ""; input.style.height = "auto";

  const pending = document.createElement("div");
  pending.className = "msg assistant";
  pending.innerHTML = `<div class="msg-role"><span class="thinking"><span class="spin"></span> reading your material</span></div>
    <div class="skel"><div class="skel-line w1"></div><div class="skel-line w2"></div><div class="skel-line w3"></div></div>`;
  inner.appendChild(pending);
  scrollThread();

  try {
    const res = await API.ask(chatId, q);
    pending.replaceWith(renderMessage({ role: "assistant", content: res.answer, attribution: res.attribution, refused: res.refused }));
    refreshSidebar(chatId);   // first question may have set the title
  } catch (e) {
    pending.innerHTML = `<div class="msg-role">${icon("i-x")} CiteFinder</div>
      <div class="answer-text refused">Something went wrong: ${esc(e.message)}</div>`;
  } finally {
    asking = false;
    document.getElementById("c-send").disabled = false;
    scrollThread();
  }
}

function scrollThread() {
  const t = document.getElementById("c-thread");
  if (t) t.scrollTop = t.scrollHeight;
}

/* ---- uploading files / a folder ----------------------------------------- */
async function handleUpload(chatId, fileList, kind) {
  const pdfs = Array.from(fileList || []).filter((f) => /\.pdf$/i.test(f.name));
  if (!pdfs.length) {
    toast(kind === "folder" ? "No PDFs found in that folder." : "Please choose PDF files.", "err");
    return;
  }
  const dismiss = toast(`Ingesting ${pdfs.length} ${pdfs.length === 1 ? "file" : "files"}… embeddings run locally.`, "work", true);
  try {
    const res = await API.upload(chatId, pdfs);
    dismiss();
    const skipped = res.total - res.stored;
    let msg = `Added ${res.stored} ${res.stored === 1 ? "file" : "files"} · ${res.chunks} chunks`;
    if (skipped > 0) msg += ` · ${skipped} skipped`;
    toast(msg, "ok");
    await loadThread(chatId);   // empty state -> ready, ask immediately
    refreshFilesCount(chatId);
  } catch (e) {
    dismiss();
    toast(e.message, "err");
  } finally {
    // reset inputs so re-selecting the same folder fires change again
    const fi = document.getElementById("c-file-files");
    const fo = document.getElementById("c-file-folder");
    if (fi) fi.value = ""; if (fo) fo.value = "";
  }
}
