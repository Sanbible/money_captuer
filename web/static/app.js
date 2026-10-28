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

  async function session() {
    const { supabase } = await init();
    const { data } = await supabase.auth.getSession(); // ต่ออายุ token ให้เองถ้าใกล้หมด
    return data.session;
  }

  async function requireSession() {
    const s = await session();
    if (!s) {
      location.replace("/");
      throw new Error("ยังไม่ได้เข้าสู่ระบบ");
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

  return { init, session, requireSession, signOut, api, baht, displayName, el };
})();
