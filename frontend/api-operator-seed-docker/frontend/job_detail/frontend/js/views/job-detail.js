// Chi tiết job: thông tin, kết quả PDF, lịch sử xử lý, nút thử lại và tải PDF.
import { h } from "../dom.js";
import { api } from "../api.js";
import { statusBadge, fmtSize, fmtFull, fmtRelative, topbar } from "../ui.js";

const POLL_MS = 3000;
const OUTCOME = { running: "Đang chạy", succeeded: "Thành công", failed: "Thất bại", lease_expired: "Hết hạn lease" };
const ERROR_INFO = {
  INVALID_FILE: "File không đọc được hoặc nội dung không khớp với đuôi file. Thử lại sẽ vẫn lỗi, hãy kiểm tra file rồi tải lên lại.",
  UNSUPPORTED_FORMAT: "Định dạng file không được hỗ trợ.",
  FILE_TOO_LARGE: "File vượt quá giới hạn dung lượng.",
  CONVERSION_TIMEOUT: "Chuyển đổi quá thời gian cho phép. Có thể thử lại.",
  CONVERSION_ERROR: "Converter gặp lỗi khi chuyển đổi. Có thể thử lại.",
  OUTPUT_INVALID: "PDF tạo ra không hợp lệ. Có thể thử lại.",
  LEASE_EXPIRED: "Worker xử lý job này đã dừng giữa chừng. Có thể thử lại.",
  INTERNAL_ERROR: "Lỗi hệ thống. Có thể thử lại sau.",
};

function expiryText(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "Đã hết hạn";
  const m = Math.floor(ms / 60000);
  return m >= 60 ? `Còn ${Math.floor(m / 60)} giờ ${m % 60} phút` : `Còn ${m} phút`;
}

// Mở link tải. Link ký sẵn chỉ dùng ngay, không lưu và không ghi log.
function startDownload(url, filename) {
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.rel = "noopener";
  document.body.append(a); a.click(); a.remove();
}

export function renderJobDetail(root, { user, onLogout, id }) {
  let job = null, attempts = [], busy = null, timer = null, disposed = false;
  const flashBox = h("div", { class: "flash", role: "status", "aria-live": "polite", hidden: true });
  const body = h("div", { class: "detail-body" }, h("p", { class: "empty" }, "Đang tải…"));

  const flash = (kind, ...content) => {
    flashBox.className = `flash ${kind ?? ""}`;
    flashBox.replaceChildren(...content);
    flashBox.hidden = content.length === 0;
  };

  root.replaceChildren(topbar(user, onLogout),
    h("main", { class: "page" }, h("a", { class: "back", href: "#/jobs" }, "← Quay lại danh sách"), flashBox, body));

  async function load({ quiet = false } = {}) {
    try {
      const [j, a] = await Promise.all([api.getJob(id), api.getAttempts(id).catch(() => null)]);
      if (disposed) return;
      job = j; if (a) attempts = a;
      draw(); schedule();
    } catch (err) {
      if (disposed || err.code === "UNAUTHENTICATED") return;
      if (quiet && job) { flash("bad", `Không cập nhật được trạng thái: ${err.message}. Hệ thống sẽ thử lại.`); schedule(); return; }
      body.replaceChildren(h("div", { class: "card" }, h("p", { class: "empty" },
        err.code === "JOB_NOT_FOUND" ? "Không tìm thấy job này." : `Không tải được job: ${err.message}`),
        err.code !== "JOB_NOT_FOUND" ? h("button", { class: "ghost", onclick: () => load() }, "Thử tải lại") : null));
    }
  }

  function schedule() {
    clearTimeout(timer);
    if (disposed || !job || !["queued", "running"].includes(job.status)) return;
    timer = setTimeout(() => (document.hidden ? schedule() : load({ quiet: true })), POLL_MS);
  }

  async function doRetry() {
    busy = "retry"; flash(null); draw();
    try {
      job = await api.retryJob(id);
      attempts = (await api.getAttempts(id).catch(() => null)) ?? attempts;
      flash("ok", "Đã đưa job vào hàng đợi để thử lại.");
    } catch (err) {
      if (err.code === "UNAUTHENTICATED") return;
      flash("bad", err.code === "INVALID_STATE" ? "Job không còn ở trạng thái thất bại nên không thử lại được." : `Không thử lại được: ${err.message}`);
      busy = null; await load(); return;
    }
    busy = null; draw(); schedule();
  }

  async function doDownload() {
    busy = "download"; flash(null); draw();
    try {
      const d = await api.download(id);
      if (!d.download_url || !/^https?:\/\//i.test(d.download_url)) throw new Error("API không trả URL tải PDF hợp lệ");
      startDownload(d.download_url, d.filename);
      flash("ok", `Đang tải ${d.filename}. Link tải chỉ dùng được trong ${Math.round(d.url_expires_in / 60)} phút, cần tải lại thì bấm nút một lần nữa.`);
    } catch (err) {
      if (err.code === "UNAUTHENTICATED") return;
      if (err.code === "RESULT_EXPIRED") flash("bad", "PDF đã hết hạn tải. Hãy ", h("a", { href: "#/jobs" }, "tải file lên lại"), " để tạo job mới.");
      else if (err.code === "RESULT_NOT_READY") flash("bad", "PDF chưa sẵn sàng, job chưa chạy xong.");
      else flash("bad", `Không tải được PDF: ${err.message}`);
      busy = null; await load(); return;
    }
    busy = null; draw();
  }

  const kv = (rows) => h("dl", { class: "kv tight" }, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "–")]));

  function actions() {
    const r = job.result;
    const buttons = [];
    if (job.status === "failed")
      buttons.push(h("button", { class: "primary", id: "btn-retry", disabled: busy !== null, onclick: doRetry },
        busy === "retry" ? "Đang gửi…" : "Thử lại"));
    if (job.status === "succeeded") {
      const ok = job.download_available;
      buttons.push(h("button", { class: "primary", id: "btn-download", disabled: !ok || busy !== null, onclick: doDownload,
        title: ok ? "" : "PDF đã hết hạn tải" }, busy === "download" ? "Đang lấy link…" : "Tải PDF"));
    }
    return h("div", { class: "actions" }, buttons);
  }

  function resultCard() {
    const r = job.result;
    if (job.status === "succeeded" && r) {
      return h("section", { class: "card" }, h("h2", {}, "Kết quả PDF"),
        kv([["Kích thước", fmtSize(r.size_bytes)], ["Số trang", String(r.page_count)],
          ["Hạn tải", `${fmtFull(r.expires_at)} (${expiryText(r.expires_at)})`],
          ["Checksum", h("span", { class: "mono", title: r.checksum }, `${r.checksum.slice(0, 16)}…`)]]),
        r.expired ? h("p", { class: "note warn" }, "PDF đã hết hạn nên không tải được nữa. Hãy tải file nguồn lên lại.") : null);
    }
    const msg = job.status === "failed" ? "Job thất bại nên chưa có PDF."
      : job.status === "running" ? "Đang chuyển đổi, trang sẽ tự cập nhật khi xong."
      : "Đang chờ worker nhận job, trang sẽ tự cập nhật.";
    return h("section", { class: "card" }, h("h2", {}, "Kết quả PDF"), h("p", { class: "note" }, msg));
  }

  function attemptsCard() {
    if (!attempts.length) return h("section", { class: "card" }, h("h2", {}, "Lịch sử xử lý"), h("p", { class: "note" }, "Chưa có lần xử lý nào."));
    return h("section", { class: "card" }, h("h2", {}, "Lịch sử xử lý"),
      h("table", { class: "jobs attempts" },
        h("thead", {}, h("tr", {}, ["Lần", "Worker", "Kết quả", "Bắt đầu", "Thời lượng", "Lỗi"].map((t) => h("th", {}, t)))),
        h("tbody", {}, attempts.map((a) => h("tr", {},
          h("td", { "data-label": "Lần" }, String(a.attempt_no)),
          h("td", { "data-label": "Worker" }, a.worker_id),
          h("td", { "data-label": "Kết quả" }, h("span", { class: `oc ${a.outcome}` }, OUTCOME[a.outcome] ?? a.outcome)),
          h("td", { "data-label": "Bắt đầu" }, fmtFull(a.started_at)),
          h("td", { "data-label": "Thời lượng" }, a.duration_ms == null ? "–" : `${(a.duration_ms / 1000).toFixed(1)} s`),
          h("td", { "data-label": "Lỗi" }, a.error_code ?? "–"))))));
  }

  function draw() {
    const o = job.options ?? {};
    const copyBtn = h("button", { class: "ghost small copy", type: "button", onclick: async (e) => {
      try { await navigator.clipboard.writeText(job.id); e.target.textContent = "Đã chép"; } catch { e.target.textContent = "Không chép được"; }
    } }, "Sao chép");
    body.replaceChildren(...[
      h("section", { class: "card job-head" },
        h("div", { class: "job-title" }, h("h1", {}, job.original_filename),
          h("div", {}, statusBadge(job.status), " ", h("span", { class: "chip dark" }, job.source_format))),
        actions()),
      job.status === "failed" ? h("div", { class: "banner", role: "alert" },
        h("strong", {}, job.error_code ?? "Lỗi"),
        h("p", {}, ERROR_INFO[job.error_code] ?? job.error_message ?? "Job thất bại."),
        job.error_message ? h("p", {}, `Chi tiết: ${job.error_message}`) : null) : null,
      h("div", { class: "grid2" },
        h("section", { class: "card" }, h("h2", {}, "Thông tin job"),
          kv([["Mã job", h("span", {}, h("span", { class: "mono" }, job.id), copyBtn)],
            ["Chủ sở hữu", job.owner_id], ["Kích thước file", fmtSize(job.source?.size_bytes)],
            ["Lần thử", `${job.attempts}/${job.max_attempts}`], ["Converter", job.converter_version],
            ["Tạo lúc", `${fmtFull(job.created_at)} (${fmtRelative(job.created_at)})`],
            ["Bắt đầu", fmtFull(job.started_at)], ["Kết thúc", fmtFull(job.finished_at)],
            ["Mã truy vết", h("span", { class: "mono" }, job.request_id)],
            ["Checksum file gốc", h("span", { class: "mono", title: job.source_checksum }, `${job.source_checksum.slice(0, 16)}…`)]])),
        h("div", { style: "display:flex;flex-direction:column;gap:18px" }, resultCard(),
          h("section", { class: "card" }, h("h2", {}, "Tuỳ chọn chuyển đổi"),
            kv([["Khổ giấy", o.page_size], ["Hướng giấy", o.orientation === "landscape" ? "Ngang" : o.orientation === "portrait" ? "Dọc" : o.orientation],
              ["Cỡ chữ", o.font_size != null ? `${o.font_size} pt` : null], ["Lề", o.margin_mm != null ? `${o.margin_mm} mm` : null]])))),
      attemptsCard()].filter(Boolean));
  }

  load();
  return () => { disposed = true; clearTimeout(timer); };
}
