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
  const isMe = (id) => data && id === data.me.user_id;
  const whoOf = (id) => (isMe(id) ? "คุณ" : nameOf(id));
  const avatarOf = (id, size = "") => MC.avatar(nameOf(id), id, size);
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
    $("top-title").textContent = p.name;
    $("party-name").textContent = p.name;
    $("party-status").textContent = STATUS[p.status] || p.status;
    $("party-status").className = `badge badge-${p.status}`;
    $("party-meta").textContent = `${open ? "" : "ปิดแล้ว · "}รวม ${sb(d.total_satang)} · ${d.expenses.length} รายการ · สมาชิก ${d.members.length}/10`;
    $("host-chip").classList.toggle("hidden", !d.me.is_host);
    $("party-code").textContent = p.code;
    $("invite").classList.toggle("hidden", !open);
    $("add-expense").classList.toggle("hidden", !open);
    $("end-party").classList.toggle("hidden", !(open && d.me.is_host));
    $("head").classList.toggle("hero-calm", p.status === "closed");
    for (const id of ["head", "balance-card", "expense-card", "member-card"]) $(id).classList.remove("hidden");
    renderSettlement(d);
    renderBalances(d);
    renderExpenses(d);
    renderMembers(d);
  }

  function renderSettlement(d) {
    const status = d.party.status;
    const paid = d.transfers.filter((t) => t.paid).length;
    const showProgress = status === "closed" && d.transfers.length > 0;
    $("progress").classList.toggle("hidden", !showProgress);
    if (showProgress) {
      const pct = Math.round((paid / d.transfers.length) * 100);
      $("progress-text").textContent = `ยืนยันแล้ว ${paid}/${d.transfers.length} รายการ`;
      $("progress-pct").textContent = `${pct}%`;
      $("progress-bar").style.width = `${pct}%`;
    }
    $("settle-card").classList.toggle("hidden", status === "open");
    if (status === "open") return;
    $("settled-hero").classList.toggle("hidden", status !== "settled");
    $("settle-title").textContent = status === "settled" ? "รายการโอน" : "สรุปการโอน";
    $("settle-sub").textContent = status === "settled" ? ""
      : "คนโอนแนบสลิป ระบบอ่านยอดเอง — ยอดต้องตรงเป๊ะ สลิป 1 ใบใช้ยืนยันได้รายการเดียว";
    if (!d.transfers.length) {
      $("transfers").replaceChildren(el("div", { className: "card empty", textContent: "ไม่มีใครต้องโอน" }));
      return;
    }
    $("transfers").replaceChildren(...d.transfers.map(transferRow));
  }

  const PAID_TEXT = { slip: "ตรวจสลิปแล้ว", receiver: "ผู้รับยืนยันแล้ว" };

  function transferRow(t) {
    const role = isMe(t.from_id) ? "คุณต้องโอน" : isMe(t.to_id) ? "โอนให้คุณ" : "ระหว่างสมาชิก";
    const top = el("div", { className: "transfer-top" },
      avatarOf(t.from_id, "md"),
      el("span", { className: "arrow" }, MC.icon("arrow-right", 14)),
      avatarOf(t.to_id, "md"),
      el("div", { className: "who" },
        el("strong", { textContent: `${whoOf(t.from_id)} → ${whoOf(t.to_id)}` }),
        el("span", { className: "muted small", textContent: role })),
      el("span", { className: "amount", textContent: sb(t.amount_satang) }));
    const actions = el("div", { className: "transfer-actions" });
    const note = el("p", { className: "notice hidden" });
    const say = (kind, text) => {
      note.className = `notice notice-${kind}`;
      note.textContent = text;
    };
    if (t.paid) actions.append(el("span", { className: "badge badge-ok", textContent: PAID_TEXT[t.paid_method] || "โอนแล้ว" }));
    if (!t.paid && !t.can_upload) {
      actions.append(el("span", { className: "waiting" }, MC.icon("clock", 16), `รอ ${whoOf(t.from_id)} แนบสลิป`));
    }
    if (t.can_upload) {
      const file = el("input", { type: "file", accept: "image/png,image/jpeg", className: "hidden" });
      const btn = el("button", { className: "btn btn-primary btn-block", type: "button" }, MC.icon("upload"), "แนบสลิปการโอน");
      btn.addEventListener("click", () => file.click());
      file.addEventListener("change", async () => {
        const picked = file.files[0];
        file.value = "";
        if (!picked) return;
        if (!["image/png", "image/jpeg"].includes(picked.type) || picked.size > 10 * 1024 * 1024) {
          say("error", "รองรับเฉพาะรูป PNG/JPEG ไม่เกิน 10 MB");
          return;
        }
        const form = new FormData();
        form.append("file", picked);
        btn.disabled = true;
        say("warn", "กำลังตรวจยอดในสลิป… อาจใช้เวลาสักครู่");
        try {
          render(await MC.api(`/api/parties/${partyId}/transfers/${t.id}/slip`, { method: "POST", body: form }));
        } catch (e) {
          say("error", e.message);
          btn.disabled = false;
        }
      });
      actions.append(btn, file);
    }
    if (t.can_confirm) {
      const btn = t.paid
        ? el("button", { className: "btn btn-ghost btn-small", type: "button", textContent: "ยกเลิกการยืนยัน" })
        : el("button", { className: "btn btn-secondary btn-block", type: "button" }, MC.icon("cash"), "ได้รับเงินแล้ว (เช่น เงินสด)");
      btn.addEventListener("click", () => act(btn, () =>
        MC.api(`/api/parties/${partyId}/transfers/${t.id}/paid`, { method: "POST" }).then(render)));
      actions.append(btn);
    }
    return el("div", { className: `card transfer${t.can_upload ? " is-mine" : ""}` }, top, actions, note);
  }

  function renderBalances(d) {
    const rows = [...d.balances].sort((a, b) => b.net - a.net);
    $("balances").replaceChildren(...rows.map((b) => el("li", {},
      avatarOf(b.user_id),
      el("div", { className: "main" },
        el("div", { className: "title", textContent: nameOf(b.user_id) + (isMe(b.user_id) ? " (คุณ)" : "") }),
        el("div", { className: "sub wrap", textContent: `จ่ายไป ${sb(b.paid)} · ส่วนที่ต้องจ่าย ${sb(b.owed)}` })),
      el("span", { className: `amount ${b.net > 0 ? "net-pos" : b.net < 0 ? "net-neg" : "muted"}`, textContent: signed(b.net) }),
    )));
  }

  function renderExpenses(d) {
    $("total").textContent = `รวม ${sb(d.total_satang)} (${d.expenses.length} รายการ)`;
    $("expenses").classList.toggle("hidden", d.expenses.length === 0);
    $("no-expenses").classList.toggle("hidden", d.expenses.length > 0);
    const items = [...d.expenses].reverse(); // ใหม่สุดก่อน
    $("expenses").replaceChildren(...items.map((e) => {
      const cash = e.source === "cash";
      const who = e.shares.map((s) => `${nameOf(s.user_id)} ${sb(s.share_satang)}`).join(" · ");
      const actions = el("div", { className: "actions" });
      if (e.can_edit) {
        const edit = el("button", { className: "icon-btn mini", type: "button", ariaLabel: "แก้ไขรายการ" }, MC.icon("pencil", 16));
        edit.addEventListener("click", () => openForm(e));
        const del = el("button", { className: "icon-btn mini danger", type: "button", ariaLabel: "ลบรายการ" }, MC.icon("trash", 16));
        del.addEventListener("click", async () => {
          const ok = await MC.confirm({ title: "ลบรายการนี้?", body: `${sb(e.amount_satang)} ${e.note || ""}`.trim(),
                                        ok: "ลบรายการ", danger: true, iconName: "trash" });
          if (ok) act(del, () => MC.api(`/api/parties/${partyId}/expenses/${e.id}`, { method: "DELETE" }).then(load));
        });
        actions.append(edit, del);
      }
      return el("li", { style: "align-items: flex-start" },
        el("span", { className: "tile", style: "width: 42px; height: 42px; border-radius: 14px" },
          MC.icon(cash ? "cash" : "receipt", 20)),
        el("div", { className: "main" },
          el("div", { className: "title-row" },
            el("span", { className: "title", textContent: e.note || (cash ? "เงินสด" : "ค่าใช้จ่าย") }),
            el("span", { className: `badge no-dot ${cash ? "badge-warn" : ""}`, textContent: cash ? "เงินสด" : "สลิป" })),
          el("div", { className: "sub", textContent: `จ่ายโดย ${nameOf(e.payer_id)}` }),
          el("div", { className: "sub wrap", textContent: `หาร ${e.shares.length} คน: ${who}` })),
        el("div", { className: "end" }, el("div", { className: "amount", textContent: sb(e.amount_satang) }), actions));
    }));
  }

  function renderMembers(d) {
    $("member-count").textContent = `${d.members.length}/10`;
    const open = d.party.status === "open";
    $("members").replaceChildren(...d.members.map((m) => {
      const row = el("li", {},
        avatarOf(m.user_id, "md"),
        el("div", { className: "main" }, el("div", { className: "title", textContent: m.name + (isMe(m.user_id) ? " (คุณ)" : "") })),
        m.is_host ? el("span", { className: "badge badge-coral no-dot" }, MC.icon("crown", 12), "host") : null);
      const canKick = open && d.me.is_host && !m.is_host;
      const canLeave = open && !m.is_host && isMe(m.user_id);
      if (canKick || canLeave) {
        const btn = el("button", { className: "btn btn-ghost btn-small btn-text-danger", type: "button" },
          MC.icon(canLeave ? "logout" : "user-minus", 16), canLeave ? "ออกจากปาร์ตี้" : "เตะออก");
        btn.addEventListener("click", async () => {
          const ok = await MC.confirm(canLeave
            ? { title: "ออกจากปาร์ตี้นี้?", body: "ออกแล้วจะไม่เห็นปาร์ตี้นี้อีก จนกว่าจะเข้าร่วมใหม่ด้วยโค้ด",
                ok: "ออกจากปาร์ตี้", danger: true, iconName: "logout" }
            : { title: `เตะ ${m.name} ออก?`, body: "เตะได้เฉพาะคนที่ยังไม่มีรายการเกี่ยวข้อง",
                ok: "เตะออก", danger: true, iconName: "user-minus" });
          if (!ok) return;
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
  $("end-party").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const ok = await MC.confirm({
      title: "ปิดปาร์ตี้และสรุปยอด?",
      body: "ปิดแล้วจะเพิ่มหรือแก้รายการไม่ได้อีก ระบบจะสรุปว่าใครต้องโอนให้ใครเท่าไหร่ โดยโอนน้อยครั้งที่สุด",
      ok: "ปิดปาร์ตี้", iconName: "flag",
    });
    if (ok) act(button, () => MC.api(`/api/parties/${partyId}/end`, { method: "POST" }).then(render));
  });

  // ---------- ฟอร์มเพิ่ม/แก้รายการ ----------

  const dialog = $("expense-dialog");
  let editing = null; // รายการที่กำลังแก้ (null = เพิ่มใหม่)
  let slipMeta = {}; // ค่าจากสลิป: trans_ref, image_sha256

  const participantBoxes = () => [...$("f-participants").querySelectorAll("input")];
  const checkedIds = () => participantBoxes().filter((c) => c.checked).map((c) => c.value);
  const payerId = () => $("f-payer").querySelector("input:checked")?.value;

  function updatePreview() {
    const amount = parseSatang($("f-amount").value);
    const ids = checkedIds();
    const shares = amount && ids.length ? split(amount, ids) : {};
    for (const box of participantBoxes()) {
      box.closest("label").querySelector(".share").textContent = box.value in shares ? sb(shares[box.value]) : "—";
    }
    if (!ids.length) $("share-preview").textContent = "เลือกผู้มีส่วนอย่างน้อย 1 คน";
    else if (amount && amount % ids.length) $("share-preview").textContent = "หารไม่ลงตัว ระบบแจกเศษทีละสตางค์";
    else $("share-preview").textContent = "";
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
    $("slip-label").textContent = "แตะเพื่อแนบรูปสลิป";
    $("reading").classList.add("hidden");
    formNotice("ok", "");
    $("save-error").classList.add("hidden");
    $("save-expense").disabled = false;
    $("f-amount").value = expense ? (expense.amount_satang / 100).toFixed(2) : "";
    $("f-note").value = expense ? expense.note : "";
    const payer = expense ? expense.payer_id : data.me.user_id;
    $("f-payer").replaceChildren(...data.members.map((m) => el("label", { className: "chip-choice with-avatar" },
      el("input", { type: "radio", name: "payer", value: m.user_id, checked: m.user_id === payer, required: true }),
      el("span", {}, MC.avatar(m.name, m.user_id, "sm"), m.name))));
    const selected = new Set(expense ? expense.shares.map((s) => s.user_id) : data.members.map((m) => m.user_id));
    $("f-participants").replaceChildren(...data.members.map((m) => {
      const box = el("input", { type: "checkbox", value: m.user_id, checked: selected.has(m.user_id) });
      box.addEventListener("change", updatePreview);
      return el("label", { className: "check-row" }, box, MC.avatar(m.name, m.user_id, "md"),
        el("span", { className: "name", textContent: m.name + (isMe(m.user_id) ? " (คุณ)" : "") }),
        el("span", { className: "share" }));
    }));
    updatePreview();
    dialog.showModal();
  }

  $("add-expense").addEventListener("click", () => openForm());
  $("close-dialog").addEventListener("click", () => dialog.close());
  $("cancel-dialog").addEventListener("click", () => dialog.close());
  $("f-amount").addEventListener("input", updatePreview);
  $("select-all").addEventListener("click", () => {
    for (const box of participantBoxes()) box.checked = true;
    updatePreview();
  });

  $("slip-file").addEventListener("change", async () => {
    const file = $("slip-file").files[0];
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 10 * 1024 * 1024) {
      formNotice("error", "รองรับเฉพาะรูป PNG/JPEG ไม่เกิน 10 MB");
      return;
    }
    $("slip-label").textContent = file.name;
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
                   payer_id: payerId(), participants };
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
