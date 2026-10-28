// หน้า dashboard: เลือกเดือน → ยอดรวม/กราฟ/ตาราง ; อัปโหลดสลิป → ตรวจ/แก้ → บันทึก
(async () => {
  const $ = (id) => document.getElementById(id);
  const { el, baht } = MC;
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

  let session, config;
  try {
    session = await MC.requireSession();
    ({ config } = await MC.init());
  } catch {
    return; // requireSession พากลับหน้าแรกแล้ว
  }

  const pad = (n) => String(n).padStart(2, "0");
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  };
  const shortDate = (iso) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("th-TH", { day: "numeric", month: "short" });
  };

  $("user").textContent = MC.displayName(session.user);
  $("logout").addEventListener("click", MC.signOut);
  $("month").value = today().slice(0, 7);
  $("month").addEventListener("change", load);

  const pageError = (text) => {
    $("page-error").textContent = text || "";
    $("page-error").classList.toggle("hidden", !text);
  };

  // ---------- โหลดข้อมูลเดือน ----------

  let loadSeq = 0; // กันผลของเดือนเก่ามาทับ ถ้าผู้ใช้เปลี่ยนเดือนเร็ว ๆ
  async function load() {
    const seq = ++loadSeq;
    const month = $("month").value || today().slice(0, 7);
    pageError("");
    try {
      const [rows, summary] = await Promise.all([
        MC.api(`/api/expenses?month=${month}`),
        MC.api(`/api/summary?month=${month}`),
      ]);
      if (seq !== loadSeq) return;
      renderRows(rows);
      $("total").textContent = baht(summary.total);
      $("count").textContent = rows.length.toLocaleString("th-TH");
      MCCharts.render(summary);
    } catch (e) {
      if (seq === loadSeq) pageError(`โหลดข้อมูลไม่สำเร็จ: ${e.message}`);
    }
  }

  function renderRows(rows) {
    const body = $("rows");
    body.replaceChildren(
      ...rows.map((r) => {
        const remove = el("button", { className: "btn btn-link", type: "button", textContent: "ลบ" });
        remove.addEventListener("click", () => removeExpense(r, remove));
        return el(
          "tr",
          {},
          el("td", {}, shortDate(r.date), r.time ? el("div", { className: "muted", textContent: r.time }) : null),
          el("td", {}, r.receiver || "–", r.note ? el("div", { className: "muted", textContent: r.note }) : null),
          el("td", {}, el("span", { className: "tag", textContent: r.category })),
          el("td", { className: "num", textContent: baht(r.amount) }),
          el("td", { className: "muted", textContent: r.source === "discord" ? "Discord" : "เว็บ" }),
          el("td", {}, remove),
        );
      }),
    );
    $("empty").classList.toggle("hidden", rows.length > 0);
  }

  async function removeExpense(row, button) {
    if (!confirm(`ลบรายการ ${baht(row.amount)} ${row.receiver || ""} ?`)) return;
    button.disabled = true;
    try {
      await MC.api(`/api/expenses/${row.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      button.disabled = false;
      pageError(`ลบไม่สำเร็จ: ${e.message}`);
    }
  }

  // ---------- อัปโหลดสลิป ----------

  const dialog = $("upload-dialog");
  let slipMeta = { bank: "", trans_ref: null }; // ค่าจาก QR ที่ไม่ได้ให้แก้ในฟอร์ม

  $("f-category").replaceChildren(
    el("option", { value: "", textContent: "เลือกหมวด", disabled: true, selected: true }),
    ...config.categories.map((c) => el("option", { value: c, textContent: c })),
  );

  function showStep(name) {
    for (const step of ["pick", "reading", "form"]) $(`step-${step}`).classList.toggle("hidden", step !== name);
  }

  function notice(kind, text) {
    const box = $("form-notice");
    box.className = `notice notice-${kind}`;
    box.textContent = text;
    box.classList.toggle("hidden", !text);
  }

  function fillForm(draft = {}) {
    $("f-amount").value = draft.amount ?? "";
    $("f-date").value = draft.date || today();
    $("f-time").value = draft.time || "";
    $("f-receiver").value = draft.receiver || "";
    $("f-note").value = "";
    $("f-category").selectedIndex = 0;
    $("save-error").classList.add("hidden");
    $("save").disabled = false;
    slipMeta = { bank: draft.bank || "", trans_ref: draft.trans_ref || null };
  }

  function openDialog() {
    $("slip-file").value = "";
    $("preview").classList.add("hidden");
    notice("ok", "");
    showStep("pick");
    dialog.showModal();
  }

  $("open-upload").addEventListener("click", openDialog);
  $("close-upload").addEventListener("click", () => dialog.close());
  $("cancel-upload").addEventListener("click", () => dialog.close());

  $("manual-entry").addEventListener("click", () => {
    fillForm();
    showStep("form");
    $("f-amount").focus();
  });

  $("slip-file").addEventListener("change", async () => {
    const file = $("slip-file").files[0];
    if (!file) return;
    fillForm();
    if (!["image/png", "image/jpeg"].includes(file.type)) {
      notice("error", "รองรับเฉพาะรูป PNG หรือ JPEG");
      return showStep("form");
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      notice("error", "ไฟล์ใหญ่เกิน 10 MB");
      return showStep("form");
    }
    $("preview").src = URL.createObjectURL(file);
    $("preview").classList.remove("hidden");
    showStep("reading");

    const form = new FormData();
    form.append("file", file);
    try {
      const draft = await MC.api("/api/slips/read", { method: "POST", body: form });
      fillForm(draft);
      if (draft.duplicate) {
        notice("warn", "สลิปนี้บันทึกไปแล้ว ไม่ต้องบันทึกซ้ำ");
        $("save").disabled = true;
      } else if (draft.amount == null) {
        notice("warn", "อ่านยอดเงินจากสลิปไม่ได้ กรุณากรอกจำนวนเงินเอง");
      } else {
        notice("ok", "อ่านสลิปแล้ว ตรวจข้อมูลแล้วเลือกหมวดเพื่อบันทึก");
      }
    } catch (e) {
      notice("error", `อ่านสลิปไม่สำเร็จ (${e.message}) — กรอกข้อมูลเองได้`);
    }
    showStep("form");
    $($("f-amount").value ? "f-category" : "f-amount").focus();
  });

  $("step-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = $("save");
    save.disabled = true;
    $("save-error").classList.add("hidden");
    const body = {
      amount: Number($("f-amount").value),
      category: $("f-category").value,
      date: $("f-date").value || null,
      time: $("f-time").value || null,
      receiver: $("f-receiver").value.trim(),
      note: $("f-note").value.trim(),
      bank: slipMeta.bank,
      trans_ref: slipMeta.trans_ref,
    };
    try {
      const saved = await MC.api("/api/expenses", { method: "POST", json: body });
      dialog.close();
      $("month").value = saved.date.slice(0, 7);
      await load();
    } catch (e) {
      $("save-error").textContent = `บันทึกไม่สำเร็จ: ${e.message}`;
      $("save-error").classList.remove("hidden");
      save.disabled = e.status === 409; // ซ้ำ → ไม่ให้กดซ้ำ
    }
  });

  load();
})();
