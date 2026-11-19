// Màn hình danh sách job + upload. Tự cập nhật khi còn job queued/running.
import { h } from "../dom.js";
import { api } from "../api.js";
import { STATUS_LABEL, STATUS_ORDER, statusBadge, fmtSize, fmtRelative, fmtFull, topbar, uploadError } from "../ui.js";

const ALLOWED = ["txt", "md", "png", "jpg", "jpeg"];
const MAX_BYTES = 10 * 1024 * 1024;
const PAGE = 10;
const POLL_MS = 3000;

export function renderJobs(root, { user, onLogout }) {
  const isOp = user.role === "operator";
  const state = { status: "", offset: 0, items: [], total: 0, counts: null, loading: true, error: null };
  let timer = null, disposed = false;

  // ---------------- Upload ----------------
  const fileInput = h("input", { type: "file", multiple: true, hidden: true,
    accept: ".txt,.md,.png,.jpg,.jpeg", "aria-label": "Chọn file để chuyển đổi" });
  const drop = h("div", { class: "drop", role: "button", tabindex: "0",
    "aria-label": "Chọn hoặc kéo thả file vào đây" },
    h("strong", {}, "Kéo thả file vào đây hoặc bấm để chọn"),
    h("span", { class: "muted" }, "Hỗ trợ .txt, .md, .png, .jpg · tối đa 10 MB mỗi file"));

  const optPage = h("select", { id: "o-page" }, h("option", { value: "A4" }, "A4"), h("option", { value: "LETTER" }, "Letter"));
  const optOri = h("select", { id: "o-ori" }, h("option", { value: "portrait" }, "Dọc"), h("option", { value: "landscape" }, "Ngang"));
  const optFont = h("input", { id: "o-font", type: "number", min: "8", max: "24", value: "11" });
  const optMargin = h("input", { id: "o-margin", type: "number", min: "5", max: "40", value: "20" });
  const field = (label, id, el) => h("div", { class: "field" }, h("label", { for: id }, label), el);
  const options = h("details", { class: "options" },
    h("summary", {}, "Tuỳ chọn chuyển đổi"),
    h("div", { class: "opt-grid" },
      field("Khổ giấy", "o-page", optPage), field("Hướng giấy", "o-ori", optOri),
      field("Cỡ chữ (8–24)", "o-font", optFont), field("Lề mm (5–40)", "o-margin", optMargin)),
    h("p", { class: "muted small" }, "Tuỳ chọn không áp dụng cho ảnh sẽ được bỏ qua."));

  const results = h("ul", { class: "results", "aria-live": "polite" });

  function readOptions() {
    const font = Number(optFont.value), margin = Number(optMargin.value);
    if (!Number.isInteger(font) || font < 8 || font > 24) return { error: "Cỡ chữ phải là số nguyên từ 8 đến 24." };
    if (!Number.isInteger(margin) || margin < 5 || margin > 40) return { error: "Lề phải là số nguyên từ 5 đến 40 mm." };
    return { value: { page_size: optPage.value, orientation: optOri.value, font_size: font, margin_mm: margin } };
  }

  function precheck(file) {
    const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!ALLOWED.includes(ext)) return "Chỉ hỗ trợ .txt, .md, .png, .jpg/.jpeg.";
    if (file.size === 0) return "File rỗng.";
    if (file.size > MAX_BYTES) return `File vượt quá 10 MB (${fmtSize(file.size)}).`;
    return null;
  }

  function showResults(items) {
    results.replaceChildren(...items.map((r) =>
      h("li", { class: r.ok ? "ok" : "bad" }, h("strong", {}, r.name), h("span", {}, r.text))));
  }

  async function handleFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const opts = readOptions();
    if (opts.error) { showResults([{ ok: false, name: "Tuỳ chọn", text: opts.error }]); options.open = true; return; }
    drop.setAttribute("aria-busy", "true"); drop.classList.add("busy");
    const out = [];
    for (const f of files) {
      const pre = precheck(f);
      if (pre) { out.push({ ok: false, name: f.name, text: pre }); continue; }
      try {
        const job = await api.createJob(f, opts.value);
        out.push({ ok: true, name: f.name, text: `Đã tạo job ${job.id.slice(0, 8)}, đang chờ xử lý` });
      } catch (err) {
        out.push({ ok: false, name: f.name, text: uploadError(err) });
        if (err.code === "UNAUTHENTICATED") break;
      }
    }
    fileInput.value = "";
    drop.removeAttribute("aria-busy"); drop.classList.remove("busy");
    showResults(out);
    if (disposed) return;
    state.status = ""; state.offset = 0;
    refresh();
  }

  drop.addEventListener("click", () => fileInput.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); handleFiles(e.dataTransfer.files); });
  fileInput.addEventListener("change", () => handleFiles(fileInput.files));

  // ---------------- Danh sách ----------------
  const tabsBox = h("div", { class: "tabs", role: "tablist", "aria-label": "Lọc theo trạng thái" });
  const listBox = h("div", { class: "list-box" });
  const pagerBox = h("div", { class: "pager" });
  const stuckBox = h("div", { class: "list-box" });
  async function showStuck() {
    stuckBox.replaceChildren(h("p", { class: "muted" }, "Đang kiểm tra job bị kẹt…"));
    try {
      const found = [];
      let offset = 0;
      // Backend chưa có endpoint stuck riêng: lọc job running có lease đã quá hạn.
      while (offset < 1000) {
        const page = await api.operatorJobs({status: "running", limit: 100, offset});
        const now = Date.now();
        found.push(...page.items.filter(j => j.lease_expires_at && Date.parse(j.lease_expires_at) < now));
        offset += page.items.length;
        if (!page.items.length || offset >= page.total) break;
      }
      stuckBox.replaceChildren(found.length ? h("ul", {}, found.map(j => h("li", {},
        h("a", {href: `#/jobs/${j.id}`}, j.original_filename),
        ` — ${j.owner_id} — lease hết hạn ${fmtFull(j.lease_expires_at)}`))) :
        h("p", {class: "muted"}, "Không phát hiện job running quá hạn lease trong tối đa 1.000 job."));
    } catch(e) { stuckBox.replaceChildren(h("p", {role:"alert"}, `Không kiểm tra được job kẹt: ${e.message}`)); }
  }

  function drawTabs() {
    const c = state.counts;
    const all = c ? STATUS_ORDER.reduce((s, k) => s + (c[k] ?? 0), 0) : null;
    const tab = (key, label) => h("button", { class: `tab ${state.status === key ? "on" : ""}`, role: "tab",
      "aria-selected": String(state.status === key), "data-status": key || "all",
      onclick: () => { state.status = key; state.offset = 0; refresh(); } },
      label, h("span", { class: "count" }, c ? String(key ? (c[key] ?? 0) : all) : "–"));
    tabsBox.replaceChildren(tab("", "Tất cả"), ...STATUS_ORDER.map((k) => tab(k, STATUS_LABEL[k])));
  }

  function row(j) {
    const failed = j.status === "failed";
    return h("tr", { "data-id": j.id },
      h("td", { "data-label": "Tên file" },
        h("a", { class: "fname", href: `#/jobs/${j.id}` }, j.original_filename),
        h("span", { class: "chip dark" }, j.source_format)),
      isOp ? h("td", { "data-label": "Chủ sở hữu" }, j.owner_id) : null,
      h("td", { "data-label": "Trạng thái" }, statusBadge(j.status),
        failed && j.error_code ? h("small", { class: "err-code", title: j.error_message ?? "" }, j.error_code) : null,
        j.status === "succeeded" && j.result?.expired ? h("small", { class: "err-code" }, "PDF đã hết hạn") : null),
      h("td", { "data-label": "Kích thước" }, fmtSize(j.source?.size_bytes)),
      h("td", { "data-label": "Tạo lúc" }, h("time", { datetime: j.created_at, title: fmtFull(j.created_at) }, fmtRelative(j.created_at))),
      h("td", { "data-label": "Lần thử" }, `${j.attempts}/${j.max_attempts}`),
      h("td", { class: "act" }, h("a", { href: `#/jobs/${j.id}` }, "Xem")));
  }

  function drawList() {
    if (state.loading && !state.items.length) { listBox.replaceChildren(h("p", { class: "empty" }, "Đang tải…")); return; }
    if (state.error) {
      listBox.replaceChildren(h("div", { class: "empty" }, h("p", {}, `Không tải được danh sách: ${state.error}`),
        h("button", { class: "ghost", onclick: () => refresh() }, "Thử lại")));
      return;
    }
    if (!state.items.length) {
      listBox.replaceChildren(h("p", { class: "empty" },
        state.status ? `Không có job nào ở trạng thái "${STATUS_LABEL[state.status]}".` : "Chưa có job nào. Hãy tải một file lên ở trên."));
      return;
    }
    listBox.replaceChildren(h("table", { class: "jobs" },
      h("thead", {}, h("tr", {}, h("th", {}, "Tên file"), isOp ? h("th", {}, "Chủ sở hữu") : null,
        h("th", {}, "Trạng thái"), h("th", {}, "Kích thước"), h("th", {}, "Tạo lúc"), h("th", {}, "Lần thử"), h("th", {}, ""))),
      h("tbody", {}, state.items.map(row))));
  }

  function drawPager() {
    const pages = Math.max(1, Math.ceil(state.total / PAGE));
    const cur = Math.floor(state.offset / PAGE) + 1;
    const from = state.total ? state.offset + 1 : 0, to = Math.min(state.offset + PAGE, state.total);
    pagerBox.replaceChildren(
      h("span", { class: "muted" }, `Hiển thị ${from}–${to} / ${state.total}`),
      h("div", {},
        h("button", { class: "ghost", disabled: cur <= 1, onclick: () => { state.offset -= PAGE; refresh(); } }, "← Trước"),
        h("span", { class: "page-no" }, `Trang ${cur}/${pages}`),
        h("button", { class: "ghost", disabled: cur >= pages, onclick: () => { state.offset += PAGE; refresh(); } }, "Sau →")));
    pagerBox.hidden = state.total <= PAGE;
  }

  const draw = () => { drawTabs(); drawList(); drawPager(); };

  async function loadCounts() {
    if (isOp) return (await api.operatorStats()).jobs_by_status;
    const totals = await Promise.all(STATUS_ORDER.map((s) => api.listJobs({ status: s, limit: 1 })));
    return Object.fromEntries(STATUS_ORDER.map((s, i) => [s, totals[i].total]));
  }

  function schedule() {
    clearTimeout(timer);
    const c = state.counts;
    if (disposed || !c || (c.queued ?? 0) + (c.running ?? 0) === 0) return;
    timer = setTimeout(() => (document.hidden ? schedule() : refresh({ quiet: true })), POLL_MS);
  }

  async function refresh({ quiet = false } = {}) {
    clearTimeout(timer);
    if (!quiet) { state.loading = true; draw(); }
    try {
      const params = { limit: PAGE, offset: state.offset, status: state.status || undefined };
      const [list, counts] = await Promise.all([isOp ? api.operatorJobs(params) : api.listJobs(params), loadCounts()]);
      if (disposed) return;
      if (!list.items.length && state.offset > 0) { state.offset = 0; return refresh(); }
      Object.assign(state, { items: list.items, total: list.total, counts, error: null, loading: false });
    } catch (err) {
      if (disposed || err.code === "UNAUTHENTICATED") return;
      state.error = err.message; state.loading = false;
    }
    draw();
    schedule();
  }

  root.replaceChildren(
    topbar(user, onLogout),
    h("main", { class: "page" },
      h("h1", {}, isOp ? "Tất cả job" : "Job của bạn"),
      h("section", { class: "card upload", "aria-label": "Tải file lên" }, drop, fileInput, options, results),
      isOp ? h("section", {class:"card"}, h("h2", {}, "Job bị kẹt"),
        h("p", {class:"muted"}, "Job running đã quá hạn lease (chỉ kiểm tra, không tự retry job đang chạy)."),
        h("button", {class:"ghost", onclick:showStuck}, "Kiểm tra job kẹt"), stuckBox) : null,
      h("section", { class: "card", "aria-label": "Danh sách job" }, tabsBox, listBox, pagerBox)));
  draw();
  refresh();

  return () => { disposed = true; clearTimeout(timer); }; // dọn khi rời màn hình
}
