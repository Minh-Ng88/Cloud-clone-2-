import { h, svg } from "../dom.js";
import { api } from "../api.js";
import { setSession } from "../session.js";
import { CONFIG } from "../config.js";

const DEMO = [
  { username: "alice", password: "alice123", label: "Người dùng", role: "user" },
  { username: "bob", password: "bob123", label: "Người dùng", role: "user" },
  { username: "operator", password: "operator123", label: "Vận hành", role: "operator" },
];

const MESSAGES = {
  INVALID_CREDENTIALS: "Sai tên đăng nhập hoặc mật khẩu.",
  VALIDATION_ERROR: "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.",
  NETWORK_ERROR: CONFIG.USE_MOCK
    ? "Không đọc được dữ liệu mock. Hãy mở trang bằng máy chủ (xem README), không mở trực tiếp file."
    : "Không kết nối được backend. Kiểm tra `docker compose up -d` đã chạy chưa, hoặc thêm ?mock=1 để chạy dữ liệu giả.",
};

function brandPanel() {
  const chip = (t) => h("span", { class: "chip" }, t);
  return h("aside", { class: "brand", "aria-hidden": "true" },
    h("div", { class: "logo" },
      svg('<rect width="32" height="32" rx="9" fill="currentColor" opacity=".18"/><path d="M10 7h8l5 5v13H10z" fill="currentColor"/>'),
      h("span", {}, "Doc2PDF")),
    h("div", { class: "brand-body" },
      h("h2", {}, "Chuyển tài liệu sang PDF, chạy nền trên cloud."),
      h("p", {}, "Tải file lên, theo dõi tiến độ và tải PDF về khi xong."),
      h("div", { class: "flow" },
        h("div", { class: "flow-card" }, h("strong", {}, "Tải lên"), h("div", { class: "chips" }, chip(".txt"), chip(".md"), chip(".png"), chip(".jpg"))),
        h("div", { class: "flow-arrow" }, "→"),
        h("div", { class: "flow-card" }, h("strong", {}, "Chuyển đổi"), h("small", {}, "queued → running")),
        h("div", { class: "flow-arrow" }, "→"),
        h("div", { class: "flow-card pdf" }, h("strong", {}, "PDF"), h("small", {}, "tải về, có hạn dùng")))),
    h("small", { class: "brand-foot" }, "Đồ án Điện toán đám mây · Nhóm 08"));
}

export function renderLogin(root, { onSuccess }) {
  const username = h("input", { id: "username", name: "username", type: "text", autocomplete: "username",
    autocapitalize: "none", spellcheck: "false", required: true, placeholder: "alice" });
  const password = h("input", { id: "password", name: "password", type: "password",
    autocomplete: "current-password", required: true, placeholder: "••••••••" });
  const toggle = h("button", { type: "button", class: "ghost small", "aria-label": "Hiện hoặc ẩn mật khẩu" }, "Hiện");
  const error = h("p", { class: "error", role: "alert", "aria-live": "assertive", hidden: true });
  const submit = h("button", { type: "submit", class: "primary" }, "Đăng nhập");

  toggle.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    toggle.textContent = show ? "Ẩn" : "Hiện";
  });

  const showError = (msg) => { error.textContent = msg; error.hidden = !msg; };
  const setBusy = (busy) => {
    submit.disabled = busy; username.disabled = busy; password.disabled = busy;
    submit.textContent = busy ? "Đang đăng nhập…" : "Đăng nhập";
  };

  const form = h("form", { class: "form", novalidate: true },
    h("h1", {}, "Đăng nhập"),
    h("p", { class: "sub" }, "Dùng tài khoản demo của nhóm."),
    h("label", { for: "username" }, "Tên đăng nhập"), username,
    h("label", { for: "password" }, "Mật khẩu"),
    h("div", { class: "pw" }, password, toggle),
    error, submit);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    if (!username.value.trim() || !password.value) { showError(MESSAGES.VALIDATION_ERROR); return; }
    setBusy(true);
    try {
      const res = await api.login(username.value.trim(), password.value);
      setSession({ token: res.access_token, user: res.user, expiresAt: Date.now() + res.expires_in * 1000 });
      onSuccess(res.user);
    } catch (err) {
      showError(MESSAGES[err.code] ?? err.message);
      setBusy(false);
      password.focus();
      password.select();
    }
  });

  const demo = h("div", { class: "demo" },
    h("p", { class: "demo-title" }, "Tài khoản demo (bấm để điền)"),
    h("div", { class: "demo-list" }, DEMO.map((d) =>
      h("button", { type: "button", class: "demo-item", onclick: () => {
        username.value = d.username; password.value = d.password; showError(""); submit.focus();
      } }, h("strong", {}, d.username), h("span", {}, d.label)))));

  const mode = CONFIG.USE_MOCK
    ? h("p", { class: "mode mock" }, "Chế độ mock: dữ liệu giả, không cần backend")
    : h("p", { class: "mode live" }, `Đang gọi backend ${CONFIG.API_BASE}`);

  root.replaceChildren(h("main", { class: "login-page" }, brandPanel(),
    h("section", { class: "panel" }, form, demo, mode)));
  username.focus();
}
