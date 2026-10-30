// helper กลางของทุกหน้า: ต่อ Supabase Auth, เรียก API พร้อม token, จัดรูปแบบตัวเลข
const MC = (() => {
  let ready = null;

  function init() {
    // โหลด /api/config ครั้งเดียว แล้วสร้าง supabase client (anon key ใช้ใน browser ได้)
    ready ??= fetch("/api/config")
      .then((r) => {
        if (!r.ok) throw new Error("โหลดการตั้งค่าไม่สำเร็จ");
        return r.json();
      })
      .then((config) => ({
        config,
        supabase: window.supabase.createClient(config.supabase_url, config.supabase_anon_key),
      }));
    return ready;
  }

  // วิธี login ที่เปิดไว้ใน Supabase ({discord: true, google: false, email: true, ...}) — ใช้ซ่อนปุ่มที่ยังไม่ได้ตั้งค่า
  let providersReady = null;
  function providers() {
    providersReady ??= init()
      .then(({ config }) =>
        fetch(`${config.supabase_url}/auth/v1/settings`, { headers: { apikey: config.supabase_anon_key } }),
      )
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((s) => s.external || {})
      .catch(() => ({ discord: true }));
    return providersReady;
  }

  async function session() {
    const { supabase } = await init();
    const { data } = await supabase.auth.getSession(); // ต่ออายุ token ให้เองถ้าใกล้หมด
    return data.session;
  }

  async function requireSession() {
    const s = await session();
    if (!s) {
      location.replace("/");
      throw Object.assign(new Error("ยังไม่ได้เข้าสู่ระบบ"), { redirecting: true });
    }
    return s;
  }

  async function signOut() {
    const { supabase } = await init();
    await supabase.auth.signOut();
    location.replace("/");
  }

  function detailText(data) {
    const detail = data && data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "ข้อมูลไม่ถูกต้อง กรุณาตรวจอีกครั้ง";
    return null;
  }

  // fetch + Bearer token; options.json → ส่งเป็น JSON ; 401 → ออกจากระบบแล้วกลับหน้าแรก
  async function api(path, options = {}) {
    const s = await session();
    const headers = new Headers(options.headers || {});
    if (s) headers.set("Authorization", `Bearer ${s.access_token}`);
    const init = { ...options, headers };
    if (options.json !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.json);
      delete init.json;
    }
    let resp;
    try {
      resp = await fetch(path, init);
    } catch {
      throw new Error("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง");
    }
    if (resp.status === 401) {
      await signOut();
      throw new Error("กรุณาเข้าสู่ระบบ");
    }
    if (resp.status === 204) return null;
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      const err = new Error(detailText(data) || `เกิดข้อผิดพลาด (${resp.status})`);
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  const bahtFormat = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const baht = (n) => `฿${bahtFormat.format(Number(n) || 0)}`;

  function displayName(user) {
    const meta = user.user_metadata || {};
    return meta.full_name || meta.name || meta.user_name || user.email || "ผู้ใช้";
  }

  // สร้าง element แบบปลอดภัย (ข้อความจาก OCR / ผู้ใช้ ไม่ถูกตีความเป็น HTML)
  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children.filter((c) => c !== null && c !== undefined));
    return node;
  }

  // ปลายทางหลัง login (เช่นลิงก์เชิญปาร์ตี้) — รับเฉพาะ path ภายในเว็บนี้ กัน redirect ไปเว็บอื่น
  function safeNext(value) {
    return typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\")
      ? value : null;
  }

  async function requireSessionOrLogin() {
    const s = await session();
    if (!s) {
      location.replace(`/?next=${encodeURIComponent(location.pathname)}`);
      throw Object.assign(new Error("ยังไม่ได้เข้าสู่ระบบ"), { redirecting: true });
    }
    return s;
  }

  // ---------- ไอคอน (Lucide) / avatar / หน้าต่างยืนยัน ----------

  const ICONS = {
    home: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    user: '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 8h8"/><path d="M7 12h10"/><path d="M7 16h6"/>',
    receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    pencil: '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "arrow-up-right": '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
    "arrow-down-left": '<path d="M17 7 7 17"/><path d="M17 17H7V7"/>',
    calendar: '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    cash: '<rect width="20" height="12" x="2" y="6" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
    crown: '<path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
    "user-minus": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11h-6"/>',
    utensils: '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 3 2h2Zm0 0v7"/>',
    coffee: '<path d="M10 2v2"/><path d="M14 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/><path d="M6 2v2"/>',
    car: '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>',
    bag: '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
    zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    ticket: '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2"/><path d="M13 17v2"/><path d="M13 11v2"/>',
    heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    dots: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    hash: '<path d="M4 9h16"/><path d="M4 15h16"/><path d="M10 3 8 21"/><path d="M16 3l-2 18"/>',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-.84c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  };

  function icon(name, size = 20) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [k, v] of Object.entries({
      viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 2,
      "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "icon",
    })) svg.setAttribute(k, v);
    svg.innerHTML = ICONS[name] || ""; // path คงที่ในไฟล์นี้ ไม่ใช่ข้อมูลผู้ใช้
    return svg;
  }

  // ไอคอนในไฟล์ HTML เขียนเป็น <i data-icon="home" data-size="20"></i>
  function hydrateIcons(root = document) {
    for (const node of root.querySelectorAll("i[data-icon]")) {
      node.replaceWith(icon(node.dataset.icon, Number(node.dataset.size) || 20));
    }
  }

  const CATEGORY_ICONS = {
    "อาหาร": "utensils", "เครื่องดื่ม/กาแฟ": "coffee", "เดินทาง": "car", "ช้อปปิ้ง": "bag", "ของใช้": "box",
    "บิล/ค่าบริการ": "zap", "สุขภาพ": "heart", "บันเทิง": "ticket", "อื่นๆ": "dots",
  };
  const categoryIcon = (category) => CATEGORY_ICONS[category] || "receipt";

  // ตัวอักษรแรกของชื่อ (ข้ามสระหน้าภาษาไทย เช่น "เจ" → "จ")
  function initial(name) {
    const ch = Array.from((name || "").trim()).find((c) => !"เแโใไ".includes(c));
    return (ch || "?").toUpperCase();
  }

  const TONES = ["tone-coral", "tone-dark", "tone-green", "tone-amber", ""];
  function avatar(name, seed = name, size = "") {
    let h = 0;
    for (const c of String(seed || "")) h = (h * 31 + c.codePointAt(0)) >>> 0;
    return el("span", { className: `avatar ${size} ${TONES[h % TONES.length]}`.trim(), textContent: initial(name),
                        ariaHidden: "true" });
  }

  // หน้าต่างยืนยันแบบ bottom sheet แทน confirm() — resolve true เมื่อกดยืนยัน
  function confirmSheet({ title, body = "", ok = "ยืนยัน", danger = false, iconName = "alert" }) {
    return new Promise((resolve) => {
      const okBtn = el("button", { className: `btn ${danger ? "btn-danger" : "btn-dark"}`, type: "button", textContent: ok });
      const cancel = el("button", { className: "btn btn-secondary", type: "button", textContent: "ยกเลิก" });
      const titleId = `confirm-${Date.now()}`;
      const dialog = el("dialog", { className: "sheet confirm" },
        el("div", { className: "sheet-handle" }),
        el("div", { className: "sheet-body" },
          el("span", { className: `tile big round ${danger ? "danger" : "coral"}` }, icon(iconName, 28)),
          el("h2", { id: titleId, textContent: title }),
          body ? el("p", { className: "muted", textContent: body }) : null),
        el("div", { className: "sheet-actions" }, cancel, okBtn));
      dialog.setAttribute("aria-labelledby", titleId);
      const done = (value) => {
        dialog.close();
        dialog.remove();
        resolve(value);
      };
      okBtn.addEventListener("click", () => done(true));
      cancel.addEventListener("click", () => done(false));
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        done(false);
      });
      dialog.addEventListener("click", (event) => event.target === dialog && done(false)); // แตะพื้นหลัง
      document.body.append(dialog);
      dialog.showModal();
      cancel.focus();
    });
  }

  hydrateIcons();

  return { init, providers, session, requireSession, requireSessionOrLogin, signOut, api, baht, displayName, el,
           safeNext, icon, categoryIcon, initial, avatar, confirm: confirmSheet };
})();
