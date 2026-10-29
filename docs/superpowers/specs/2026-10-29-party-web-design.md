# Party บนเว็บ (แทนบอท Discord) — design

วันที่: 2026-10-29 · branch `party-group` · แทนที่ `2026-10-29-party-split-design.md` ในส่วน Discord ทั้งหมด

## ทำไม

เจ้าของต้องการให้ระบบทั้งหมดอยู่บนเว็บ — ปาร์ตี้หารค่าใช้จ่ายต้องใช้ได้จากเว็บ ไม่ต้องพึ่ง Discord
และเพื่อนที่ login ด้วย Google / อีเมล (ไม่มี Discord) ต้องอยู่ในปาร์ตี้เดียวกันได้

## Goals

1. หน้า **ปาร์ตี้ของฉัน**: รายการปาร์ตี้ที่เป็นสมาชิก, สร้างปาร์ตี้, เข้าร่วมด้วยโค้ด
2. **ลิงก์เชิญ** `/join/<CODE>` — ยังไม่ login → login ก่อนแล้วเข้าร่วมให้อัตโนมัติ
3. หน้า **ปาร์ตี้**: สมาชิก, ยอดสุทธิทุกคน, รายการทั้งหมด (ทุกสมาชิกเห็น), เพิ่มรายการจากสลิปหรือเงินสด
   พร้อมเลือกคนจ่าย / ผู้มีส่วน / หมายเหตุ, แก้/ลบ (คนเพิ่มหรือ host), ออก/เตะ, ปิดปาร์ตี้ (host),
   สรุปการโอน + ปุ่ม **โอนแล้ว** (คนโอนหรือคนรับ) → ครบ = เคลียร์แล้ว
4. กฎเดิมทั้งหมด: ≤ 10 คนรวม host, เข้าซ้ำไม่ได้, ปิดแล้วเข้า/เพิ่ม/แก้ไม่ได้, สลิปซ้ำ (เลขอ้างอิงหรือรูปเดียวกัน) บันทึกไม่ได้,
   ออก/เตะได้เฉพาะคนที่ไม่มีรายการเกี่ยวข้อง, host ออก/ถูกเตะไม่ได้, เงินเป็นสตางค์ หารแจกเศษทีละสตางค์, โอน greedy ≤ n−1
5. **ลบบอท Discord ทั้งหมด** (`bot.py`, `party_bot.py`, dependency `discord.py`, ตัวแปร Discord ใน config)

## Non-goals

- อัปเดตอัตโนมัติ (เจ้าของเลือก: กดรีเฟรชเอง; หน้าอัปเดตทันทีหลังการกระทำของตัวเอง)
- บันทึกส่วนแบ่งปาร์ตี้ลงรายจ่ายส่วนตัว (ตัดสินไว้แล้ว: ปิดปาร์ตี้ = แค่สรุปการโอน)
- แจ้งเตือน (อีเมล/push), เก็บไฟล์รูปสลิป, หารไม่เท่ากัน

## Anti-goals

- เห็น/แก้ปาร์ตี้ที่ตัวเองไม่ได้เป็นสมาชิก (ต้องตอบ 404) หรือรู้ว่ามีปาร์ตี้นั้นอยู่
- ผลรวมส่วนแบ่ง ≠ ยอด, ผลรวม net ≠ 0
- ส่วนรายจ่ายส่วนตัวที่ใช้สัมภาษณ์เปลี่ยนพฤติกรรม

## Constraints

- branch `party-group` เท่านั้น จน merge (เจ้าของสั่ง)
- เก็บปุ่ม login ด้วย Discord (เจ้าของตัดสิน) — Discord เป็นแค่วิธี login
- ตารางปาร์ตี้แบบ Discord ที่รันไปแล้วว่างอยู่ → ลบแล้วสร้างใหม่ได้ (เจ้าของยอมรับ)

## Approaches

| | วิธี | ผล |
|---|---|---|
| **A (เลือก)** | ต่อยอดเว็บเดิม: FastAPI `/api/parties/...` ตรวจสิทธิ์ที่ server แล้วเรียก SQL functions; ใช้ `/api/slips/read`, auth, `party_math` เดิม | ตรงกับเว็บที่มี, ความปลอดภัยจุดเดียว |
| B | browser คุย Supabase ตรงด้วย RLS | เขียนกฎความปลอดภัยใหม่ทั้งหมด ไม่ตรงกับเว็บเดิม — ตัดทิ้ง |

เจ้าของเลือก **A** และให้ใช้ของเดิมให้มากที่สุด

## การออกแบบ

### ข้อมูล (`supabase/party_schema.sql` เขียนใหม่ — ต้นไฟล์ drop ตาราง/ฟังก์ชันปาร์ตี้แบบ Discord)

- `parties(id, code unique, name, host_id uuid, status open|closed|settled, created_at, closed_at, settled_at)`
- `party_members(party_id, user_id uuid, display_name text, joined_at)` PK (party_id, user_id) — uuid ของบัญชีเว็บ ไม่ผูก FK กับ `auth.users` (id มาจาก token ที่ server ตรวจแล้วเท่านั้น และให้ smoke test ใช้ uuid สุ่มได้)
- `party_expenses(id, party_id, amount_satang > 0, payer_id uuid, created_by uuid, note ≤ 100, source slip|cash,
  trans_ref, image_sha256, created_at, updated_at)` — **ไม่มี draft** (ฟอร์มอยู่ใน browser) ;
  unique `(party_id, trans_ref)` และ `(party_id, image_sha256)` เมื่อไม่ null
- `party_expense_shares(expense_id, user_id, share_satang ≥ 0)`
- `party_transfers(id, party_id, seq, from_id, to_id, amount_satang, paid, paid_by, paid_at)`
- RLS เปิดทุกตาราง ไม่มี policy (เหมือน `expenses`)
- functions (ล็อกแถว parties `for update`, execute เฉพาะ service_role):
  `party_create(p_code, p_name, p_host, p_host_name)` → party id (ใส่ host เป็นสมาชิก, atomic) ·
  `party_join(p_party, p_user, p_name)` → ok|full|already|closed|not_found ·
  `party_remove_member(p_party, p_member, p_actor)` → ok|involved|not_member|forbidden|host|closed|not_found ·
  `party_add_expense(p_party, p_actor, p_amount, p_payer, p_note, p_source, p_trans_ref, p_sha, p_shares)` → `{status, id}` ·
  `party_update_expense(p_expense, p_actor, p_amount, p_payer, p_note, p_shares)` → status ·
  `party_delete_expense(p_expense, p_actor)` · `party_close(p_party, p_actor)` · `party_create_transfers(p_party, p_transfers)` ·
  `party_toggle_paid(p_transfer, p_actor)` — สถานะเหมือนรอบ Discord (+ add/update คืน `duplicate`, `invalid`, `forbidden`, `closed`, `not_member`)

### API (เพิ่มใน `web/app.py` หรือ `web/parties.py` แล้ว include)

ใช้ `current_user` เดิม; ทุก endpoint ที่มี `{party_id}` → ไม่ใช่สมาชิก = 404

| Method | Path | |
|---|---|---|
| GET | `/api/parties` | ปาร์ตี้ของฉัน (ชื่อ, สถานะ, จำนวนสมาชิก, ยอดสุทธิของฉัน) |
| POST | `/api/parties` | `{name}` → สร้าง (โค้ดสุ่ม 6 ตัว) |
| POST | `/api/parties/join` | `{code}` → เข้าร่วม (ข้อความตามสถานะ) |
| GET | `/api/parties/{id}` | party, me, members (+ชื่อ, host), expenses (+shares), balances, transfers |
| POST | `/api/parties/{id}/expenses` | `{amount: "1,234.50", note, payer_id, participants[], source, trans_ref?, image_sha256?}` |
| PUT | `/api/parties/{id}/expenses/{eid}` | `{amount, note, payer_id, participants[]}` |
| DELETE | `/api/parties/{id}/expenses/{eid}` | |
| DELETE | `/api/parties/{id}/members/{uid}` | ตัวเอง = ออก, คนอื่น = เตะ (host) |
| POST | `/api/parties/{id}/end` | host → ล็อก → คำนวณ → บันทึกรายการโอน → คืนรายละเอียด |
| POST | `/api/parties/{id}/transfers/{tid}/paid` | สลับโอนแล้ว |

- ยอดรับเป็นสตริงแล้ว `parse_baht` (ไม่ผ่าน float) ; shares คำนวณ server ด้วย `party_math.split`
- `/api/slips/read` เดิม **เพิ่ม `image_sha256`** ในผลลัพธ์ (ฟิลด์ใหม่ ไม่เปลี่ยนของเดิม) เพื่อใช้กันสลิปซ้ำในปาร์ตี้
- `AuthUser` เพิ่ม `name` (full_name / name / user_name ใน user_metadata → อีเมล) ใช้เป็นชื่อในปาร์ตี้
- `party_math` เรียงผู้มีส่วนตอนแจกเศษด้วยสตริง id (uuid) แทน `int(id)`

### หน้าเว็บ (HTML/JS แบบเดิม, ใช้ `app.js` / `style.css`)

- dashboard + settings: เพิ่มลิงก์ **ปาร์ตี้**
- `/parties`: รายการปาร์ตี้ (การ์ด: ชื่อ, สถานะ, สมาชิก n/10, ยอดสุทธิของฉัน) + ฟอร์มสร้าง + ฟอร์มใส่โค้ด
- `/party/<id>`: หัว (ชื่อ, สถานะ, โค้ด + ปุ่มคัดลอกลิงก์เชิญ, รีเฟรช), สมาชิก (ปุ่มเตะ/ออก), ยอดสุทธิทุกคน,
  รายการ (แก้/ลบ), ปุ่ม **+ เพิ่มรายการ** (dialog: อัปสลิป → `/api/slips/read` → ฟอร์ม หรือกรอกเงินสด;
  เลือกคนจ่าย, ติ๊กผู้มีส่วน default ทุกคน, แสดงส่วนแบ่งสด), ปุ่มปิดปาร์ตี้ (host, ยืนยัน),
  หลังปิด: สรุปโอน + ปุ่มโอนแล้ว, เคลียร์แล้ว = แบนเนอร์
- `/join/<code>`: ยังไม่ login → ไปหน้าแรกพร้อม `?next=/join/<code>` → login แล้วกลับมา → join → ไปหน้าปาร์ตี้
- หน้าแรก: รองรับ `?next=` (ต้องขึ้นต้นด้วย `/` และไม่ใช่ `//`) ใช้เป็น `redirectTo` ของทุกวิธี login

### ลบ Discord bot

ลบ `bot.py`, `party_bot.py`, `tests/test_bot_helpers.py`, `tests/test_party_bot.py`, `discord.py` ใน requirements,
`DISCORD_TOKEN` / `DISCORD_CHANNEL_ID` / `BOT_REQUIRED` ใน config (+ เทสต์), ส่วนบอทใน README;
`store.claim_discord_expenses` และคอลัมน์ `expenses.discord_id` **คงไว้** (ส่วนรายจ่ายส่วนตัวไม่ถูกแตะ)

### การทดสอบ

- `party_math` เดิม (ปรับเทสต์ลำดับเศษ) · `party_store` MockTransport ใหม่ · API ด้วย FakePartyStore:
  ไม่ใช่สมาชิก → 404, ยอดผิด → 422, ผลรวมส่วนแบ่ง, สิทธิ์แก้/ลบ/ปิด, สถานะ RPC → HTTP code
- smoke test SQL กับ Supabase จริง (`python party_store.py`) หลังเจ้าของรัน SQL
- ทดสอบมือ: 2 บัญชี (Discord + Google/อีเมล) สร้าง → ลิงก์เชิญ → สลิป + เงินสด → แก้/ลบ → ปิด → โอนแล้วครบ

## ปล่อยให้คนสร้างตัดสินใจ

หน้าตา/ข้อความไทยที่ไม่ได้ระบุ, แยกไฟล์ JS/route ย่อย, รูปแบบ JSON ภายในที่ไม่ได้ระบุ
