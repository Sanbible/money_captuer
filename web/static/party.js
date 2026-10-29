// หน้าปาร์ตี้: สมาชิก, ยอดสุทธิ, รายการ, เพิ่ม/แก้/ลบ, ปิดปาร์ตี้, สรุปการโอน + โอนแล้ว
// ทุกการเปลี่ยนแปลงตรวจซ้ำที่ server (ฐานข้อมูลบังคับกฎทั้งหมด) — หน้านี้แค่ช่วยให้กรอกง่าย
(async () => {
  const $ = (id) => document.getElementById(id);
  const { el } = MC;
  const STATUS = { open: "เปิดอยู่", closed: "รอโอนเงิน", settled: "เคลียร์แล้ว" };
  const partyId = decodeURIComponent(location.pathname.split("/").pop() || "");

  try {
    await MC.requireSessionOrLogin();
  } catch (e) {
    if (!e.redirecting) { // ไม่ใช่การพาไปหน้า login → แสดงให้เห็น อย่าปล่อยหน้าว่าง
      $("page-error").textContent = `เปิดหน้านี้ไม่สำเร็จ (${e.message}) — กด Ctrl+F5 เพื่อโหลดใหม่`;
      $("page-error").classList.remove("hidden");
    }
    return;
  }

  let data = null; // รายละเอียดล่าสุดจาก server
  let names = {};

  const sb = (satang) => MC.baht(satang / 100);
  const signed = (satang) => (satang > 0 ? `+${sb(satang)}` : satang < 0 ? `-${sb(-satang)}` : sb(0));
  const nameOf = (id) => names[id] || "สมาชิก";
  const pageError = (text) => {
    $("page-error").textContent = text || "";
    $("page-error").classList.toggle("hidden", !text);
  };

  // ---------- เงิน (สตางค์) — กติกาเดียวกับ party_math.py ----------

  function parseSatang(text) {
    const s = (text || "").trim().replace(/^฿/, "").replace(/บาท$/, "").trim();
    if (!/^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?$/.test(s)) return null;
    const [whole, frac = ""] = s.replace(/,/g, "").split(".");
    const satang = Number(whole) * 100 + Number((frac + "00").slice(0, 2));
    return satang > 0 && satang <= 1_000_000_000 ? satang : null;
  }

  function split(amount, ids) {
    const ordered = [...ids].sort();
    const base = Math.floor(amount / ordered.length);
    const rem = amount - base * ordered.length;
    return Object.fromEntries(ordered.map((id, i) => [id, base + (i < rem ? 1 : 0)]));
  }

  // ---------- โหลด / วาด ----------

  async function load() {
    pageError("");
    try {
      render(await MC.api(`/api/parties/${encodeURIComponent(partyId)}`));
    } catch (e) {
      pageError(e.status === 404 || e.status === 422 ? "ไม่พบปาร์ตี้นี้ หรือคุณยังไม่ได้เป็นสมาชิก" : `โหลดไม่สำเร็จ: ${e.message}`);
    }
  }

  function render(d) {
    data = d;
    names = Object.fromEntries(d.members.map((m) => [m.user_id, m.name]));
    const p = d.party;
    const open = p.status === "open";
    document.title = `${p.name} · Money Capture`;
    $("party-name").textContent = p.name;
    $("party-status").textContent = STATUS[p.status] || p.status;
    $("party-status").className = `badge badge-${p.status}`;
    $("party-code").textContent = p.code;
    $("copy-link").classList.toggle("hidden", !open);
    $("add-expense").classList.toggle("hidden", !open);
    $("end-party").classList.toggle("hidden", !(open && d.me.is_host));
    for (const id of ["head", "balance-card", "expense-card", "member-card"]) $(id).classList.remove("hidden");
    renderSettlement(d);
    renderBalances(d);
    renderExpenses(d);
    renderMembers(d);
  }

  function renderSettlement(d) {
    const status = d.party.status;
    $("settle-card").classList.toggle("hidden", status === "open");
    if (status === "open") return;
    $("settle-title").textContent = status === "settled" ? "✅ เคลียร์ครบแล้ว" : "สรุปการโอน — กดเมื่อโอนแล้ว";
    if (!d.transfers.length) {
      $("transfers").replaceChildren(el("p", { className: "muted", textContent: "ไม่มีใครต้องโอน 🎉" }));
      return;
    }
    $("transfers").replaceChildren(...d.transfers.map((t) => {
      const line = el("div", { className: "transfer-line" },
        el("span", { textContent: `${nameOf(t.from_id)} → ${nameOf(t.to_id)}` }),
        el("strong", { textContent: sb(t.amount_satang) }));
      const btn = el("button", {
        className: `btn btn-small ${t.paid ? "btn-success" : ""}`, type: "button",
        textContent: t.paid ? "✅ โอนแล้ว" : "กดเมื่อโอนแล้ว", disabled: !t.can_mark,
        title: t.can_mark ? "" : "เฉพาะคนโอนหรือคนรับเท่านั้น",
      });
      btn.addEventListener("click", () => act(btn, () =>
        MC.api(`/api/parties/${partyId}/transfers/${t.id}/paid`, { method: "POST" }).then(render)));
      return el("div", { className: "transfer" }, line, btn);
    }));
  }

  function renderBalances(d) {
    const rows = [...d.balances].sort((a, b) => b.net - a.net);
    $("balances").replaceChildren(...rows.map((b) => el("tr", {},
      el("td", { textContent: nameOf(b.user_id) + (b.user_id === d.me.user_id ? " (คุณ)" : "") }),
      el("td", { className: "num", textContent: sb(b.paid) }),
      el("td", { className: "num", textContent: sb(b.owed) }),
      el("td", { className: `num ${b.net > 0 ? "net-pos" : b.net < 0 ? "net-neg" : ""}`, textContent: signed(b.net) }),
    )));
  }

  function renderExpenses(d) {
    $("total").textContent = `· รวม ${sb(d.total_satang)} (${d.expenses.length} รายการ)`;
    $("no-expenses").classList.toggle("hidden", d.expenses.length > 0);
    const items = [...d.expenses].reverse(); // ใหม่สุดก่อน
    $("expenses").replaceChildren(...items.map((e) => {
      const who = e.shares.map((s) => `${nameOf(s.user_id)} ${sb(s.share_satang)}`).join(" · ");
      const actions = el("div", { className: "row-actions" });
      if (e.can_edit) {
        const edit = el("button", { className: "btn btn-small", type: "button", textContent: "แก้" });
        edit.addEventListener("click", () => openForm(e));
        const del = el("button", { className: "btn btn-link", type: "button", textContent: "ลบ" });
        del.addEventListener("click", () => {
          if (!confirm(`ลบรายการ ${sb(e.amount_satang)} ${e.note || ""} ?`)) return;
          act(del, () => MC.api(`/api/parties/${partyId}/expenses/${e.id}`, { method: "DELETE" }).then(load));
        });
        actions.append(edit, del);
      }
      return el("div", { className: "expense" },
        el("div", { className: "expense-head" },
          el("strong", { textContent: sb(e.amount_satang) }),
          el("span", { textContent: e.note || (e.source === "cash" ? "เงินสด" : "ค่าใช้จ่าย") }),
          el("span", { className: "muted", textContent: `จ่ายโดย ${nameOf(e.payer_id)}` }),
          actions),
        el("div", { className: "muted small", textContent: `หาร ${e.shares.length} คน: ${who}` }));
    }));
  }

  function renderMembers(d) {
    $("member-count").textContent = `(${d.members.length}/10)`;
    const open = d.party.status === "open";
    $("members").replaceChildren(...d.members.map((m) => {
      const row = el("div", { className: "member" },
        el("span", { textContent: m.name + (m.user_id === d.me.user_id ? " (คุณ)" : "") }),
        m.is_host ? el("span", { className: "badge", textContent: "host" }) : null);
      const canKick = open && d.me.is_host && !m.is_host;
      const canLeave = open && !m.is_host && m.user_id === d.me.user_id;
      if (canKick || canLeave) {
        const btn = el("button", { className: "btn btn-link", type: "button", textContent: canLeave ? "ออกจากปาร์ตี้" : "เตะออก" });
        btn.addEventListener("click", () => {
          if (!confirm(canLeave ? "ออกจากปาร์ตี้นี้?" : `เตะ ${m.name} ออกจากปาร์ตี้?`)) return;
          act(btn, async () => {
            await MC.api(`/api/parties/${partyId}/members/${m.user_id}`, { method: "DELETE" });
            if (canLeave) location.replace("/parties");
            else await load();
          });
        });
        row.append(btn);
      }
      return row;
    }));
  }

  // ปุ่มที่เรียก API: ปิดปุ่มระหว่างรอ, error แสดงบนหน้า
  async function act(button, fn) {
    button.disabled = true;
    pageError("");
    try {
      await fn();
    } catch (e) {
      pageError(e.message);
      button.disabled = false;
    }
  }

  // ---------- ปุ่มบนหัว ----------

  $("refresh").addEventListener("click", load);
  $("copy-link").addEventListener("click", async () => {
    const link = `${location.origin}/join/${data.party.code}`;
    try {
      await navigator.clipboard.writeText(link);
      $("copy-note").textContent = `คัดลอกแล้ว: ${link}`;
    } catch {
      $("copy-note").textContent = `ส่งลิงก์นี้ให้เพื่อน: ${link}`;
    }
    $("copy-note").classList.remove("hidden");
  });
  $("end-party").addEventListener("click", (event) => {
    if (!confirm("ปิดปาร์ตี้แล้วจะเพิ่ม/แก้รายการไม่ได้อีก และระบบจะสรุปว่าใครต้องโอนให้ใคร — ยืนยัน?")) return;
    act(event.currentTarget, () => MC.api(`/api/parties/${partyId}/end`, { method: "POST" }).then(render));
  });

  // ---------- ฟอร์มเพิ่ม/แก้รายการ ----------

  const dialog = $("expense-dialog");
  let editing = null; // รายการที่กำลังแก้ (null = เพิ่มใหม่)
  let slipMeta = {}; // ค่าจากสลิป: trans_ref, image_sha256

  function checkedIds() {
    return [...$("f-participants").querySelectorAll("input:checked")].map((c) => c.value);
  }

  function updatePreview() {
    const amount = parseSatang($("f-amount").value);
    const ids = checkedIds();
    if (!amount || !ids.length) {
      $("share-preview").textContent = ids.length ? "" : "เลือกผู้มีส่วนอย่างน้อย 1 คน";
      return;
    }
    const shares = split(amount, ids);
    $("share-preview").textContent = "ส่วนแบ่ง: " + ids.map((id) => `${nameOf(id)} ${sb(shares[id])}`).join(" · ");
  }

  function formNotice(kind, text) {
    $("form-notice").className = `notice notice-${kind}`;
    $("form-notice").textContent = text;
    $("form-notice").classList.toggle("hidden", !text);
  }

  function openForm(expense = null) {
    editing = expense;
    slipMeta = {};
    $("expense-title").textContent = expense ? "แก้ไขรายการ" : "เพิ่มรายการ";
    $("slip-field").classList.toggle("hidden", Boolean(expense));
    $("slip-file").value = "";
    $("reading").classList.add("hidden");
    formNotice("ok", "");
    $("save-error").classList.add("hidden");
    $("save-expense").disabled = false;
    $("f-amount").value = expense ? (expense.amount_satang / 100).toFixed(2) : "";
    $("f-note").value = expense ? expense.note : "";
    const payer = expense ? expense.payer_id : data.me.user_id;
    $("f-payer").replaceChildren(...data.members.map((m) =>
      el("option", { value: m.user_id, textContent: m.name, selected: m.user_id === payer })));
    const selected = new Set(expense ? expense.shares.map((s) => s.user_id) : data.members.map((m) => m.user_id));
    $("f-participants").replaceChildren(...data.members.map((m) => {
      const box = el("input", { type: "checkbox", value: m.user_id, checked: selected.has(m.user_id) });
      box.addEventListener("change", updatePreview);
      return el("label", { className: "checkbox" }, box, m.name);
    }));
    updatePreview();
    dialog.showModal();
  }

  $("add-expense").addEventListener("click", () => openForm());
  $("close-dialog").addEventListener("click", () => dialog.close());
  $("cancel-dialog").addEventListener("click", () => dialog.close());
  $("f-amount").addEventListener("input", updatePreview);
  $("select-all").addEventListener("click", () => {
    for (const box of $("f-participants").querySelectorAll("input")) box.checked = true;
    updatePreview();
  });

  $("slip-file").addEventListener("change", async () => {
    const file = $("slip-file").files[0];
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 10 * 1024 * 1024) {
      formNotice("error", "รองรับเฉพาะรูป PNG/JPEG ไม่เกิน 10 MB");
      return;
    }
    $("reading").classList.remove("hidden");
    $("save-expense").disabled = true;
    formNotice("ok", "");
    const form = new FormData();
    form.append("file", file);
    try {
      const draft = await MC.api("/api/slips/read", { method: "POST", body: form });
      slipMeta = { trans_ref: draft.trans_ref || null, image_sha256: draft.image_sha256 || null };
      if (draft.amount != null) $("f-amount").value = Number(draft.amount).toFixed(2);
      if (!$("f-note").value && draft.receiver) $("f-note").value = `โอนให้ ${draft.receiver}`.slice(0, 100);
      formNotice(draft.amount != null ? "ok" : "warn",
        draft.amount != null ? "อ่านสลิปแล้ว ตรวจยอดและเลือกผู้มีส่วน" : "อ่านยอดจากสลิปไม่ได้ กรุณากรอกยอดเอง");
    } catch (e) {
      formNotice("error", `อ่านสลิปไม่สำเร็จ (${e.message}) — กรอกยอดเองได้`);
    }
    $("reading").classList.add("hidden");
    $("save-expense").disabled = false;
    updatePreview();
  });

  $("expense-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = $("save-expense");
    $("save-error").classList.add("hidden");
    const participants = checkedIds();
    if (!parseSatang($("f-amount").value)) {
      $("save-error").textContent = "ยอดไม่ถูกต้อง ใส่ตัวเลขบวก ทศนิยมไม่เกิน 2 ตำแหน่ง";
      $("save-error").classList.remove("hidden");
      return;
    }
    if (!participants.length) {
      $("save-error").textContent = "เลือกผู้มีส่วนอย่างน้อย 1 คน";
      $("save-error").classList.remove("hidden");
      return;
    }
    const body = { amount: $("f-amount").value.trim(), note: $("f-note").value.trim(),
                   payer_id: $("f-payer").value, participants };
    save.disabled = true;
    try {
      if (editing) {
        await MC.api(`/api/parties/${partyId}/expenses/${editing.id}`, { method: "PUT", json: body });
      } else {
        const source = slipMeta.trans_ref || slipMeta.image_sha256 ? "slip" : "cash";
        await MC.api(`/api/parties/${partyId}/expenses`, { method: "POST", json: { ...body, source, ...slipMeta } });
      }
      dialog.close();
      await load();
    } catch (e) {
      $("save-error").textContent = `บันทึกไม่สำเร็จ: ${e.message}`;
      $("save-error").classList.remove("hidden");
      save.disabled = e.status === 409 && /สลิป/.test(e.message);
    }
  });

  load();
})();
