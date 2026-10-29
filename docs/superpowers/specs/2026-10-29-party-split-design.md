# Party (หารค่าใช้จ่ายกลุ่ม) — design

วันที่: 2026-10-29 · ต่อยอดบอท Discord ใน repo นี้ (`bot.py`) · ทำบน branch `party-group`

## ทำไม

กลุ่มเพื่อนไปทริปหรือกินข้าวด้วยกัน แต่ละคนจ่ายคนละรายการ ตอนจบต้องคิดว่าใครต้องโอนให้ใครเท่าไหร่ ซึ่งคิดเองยุ่งและผิดง่าย
บอทมีความสามารถอ่านสลิปอยู่แล้ว จึงต่อยอดเป็น "ปาร์ตี้": ทุกคนอัปสลิปใน thread ของปาร์ตี้ เลือกว่าใครมีส่วน
แล้วบอทสรุปรายการโอนที่จำนวนครั้งน้อยที่สุด

ความสำเร็จ = กลุ่ม ≤ 10 คนใช้จบทริปได้โดยไม่ต้องคิดเลขเอง ยอดถูกทุกสตางค์ ปุ่มยังกดได้แม้บอทรีสตาร์ทกลางทริป
และ flow บันทึกรายจ่ายส่วนตัวเดิมทำงานเหมือนเดิม

## Goals

1. `/party create <ชื่อ>` สร้างปาร์ตี้ + **thread ส่วนตัว** + โค้ดเชิญที่ระบบสร้าง + โพสต์ปุ่ม [เข้าร่วม] ใน**ช่องหลัก**
2. เข้าร่วมได้ 2 ทาง: กดปุ่ม [เข้าร่วม] ในช่องหลัก หรือ `/party join <code>` — รวม host ≤ 10 คน, ห้ามซ้ำ, ปาร์ตี้ที่ปิดแล้วเข้าไม่ได้
3. เพิ่มค่าใช้จ่ายจากสลิปใน thread หรือ `/party add <ยอด> <หมายเหตุ>` (เงินสด) ผ่านฟอร์มยืนยัน: ยอด (แก้ได้), คนจ่าย,
   ผู้มีส่วน (≥ 1 คน), หมายเหตุ — บันทึกเมื่อกด [ยืนยัน] แล้วโพสต์สรุปรายการใน thread
4. `/party status` — รายการทั้งหมด + ยอดสุทธิปัจจุบันของแต่ละคน; แก้/ลบรายการได้เฉพาะคนเพิ่มรายการนั้นหรือ host
5. host เตะสมาชิก / สมาชิกออกเอง — เฉพาะคนที่ไม่ได้เป็นคนจ่ายและไม่อยู่ในผู้มีส่วนของรายการไหน
6. `/party end` (host) → ยืนยัน → ล็อก → โพสต์สรุป: ยอดรวม, แต่ละคน จ่ายไป / ส่วนที่ต้องรับผิดชอบ / สุทธิ, รายการโอน
   พร้อมปุ่ม [โอนแล้ว] — ครบทุกรายการ → สถานะ settled + โพสต์แจ้ง
7. เงินเป็นสตางค์จำนวนเต็มทั้งหมด หารแบบไม่มีเศษหาย และรายการโอนไม่เกิน n−1 รายการ (greedy)
8. state ทั้งหมดอยู่ใน Supabase; ปุ่ม/เมนูทุกตัวใช้ได้หลังบอทรีสตาร์ท

## Non-goals

- **ไม่บันทึกส่วนแบ่งปาร์ตี้ลงรายจ่ายส่วนตัว** ของใครเลย (เจ้าของตัดสินใจ: ตอนปิดแค่สรุปการโอน) — ข้อ "Sheet ส่วนตัว" ในสเปกเดิมตัดออก
- หน้าเว็บสำหรับปาร์ตี้ (ข้อมูลอยู่ใน Supabase แล้ว ทำทีหลังได้)
- หารไม่เท่ากัน / กำหนดส่วนแบ่งเอง, หลายสกุลเงิน, ลบปาร์ตี้, เก็บไฟล์รูปสลิป
- ตรวจว่าโอนจริง (ปุ่ม [โอนแล้ว] เชื่อตามที่คนกด)

## Anti-goals (ถ้าเกิด = ล้มเหลวแม้ระบบจะทำงาน)

- ผลรวมส่วนแบ่ง ≠ ยอดรายการ หรือผลรวม net ≠ 0 แม้แต่ 1 สตางค์
- สมาชิกเกิน 10 คน หรือมีรายการถูกเพิ่ม/แก้หลังปิดปาร์ตี้ (แม้กดพร้อมกัน)
- คนนอกปาร์ตี้เห็น thread / กดปุ่ม / ใช้คำสั่งของปาร์ตี้ได้
- flow รายจ่ายส่วนตัวในช่อง `DISCORD_CHANNEL_ID` เปลี่ยนพฤติกรรม หรือสลิปใน thread ปาร์ตี้ไปเข้า flow ส่วนตัว
- บอทรีสตาร์ทแล้วปุ่มเก่ากดไม่ได้ ("This interaction failed")

## Constraints

- discord.py 2.7 (มี `app_commands`, `DynamicItem`), Python 3.12, `.venv`
- อ่านสลิปด้วย `slip_qr.read_slip_qr(bytes)` + `slip_reader.read_slip(bytes, key, model, mime)` เดิม — ห้ามเขียนใหม่;
  ใช้ `SlipBot._ocr_slot` (Semaphore) ร่วมกับ flow ส่วนตัว (Typhoon 20 req/min)
- Supabase ผ่าน PostgREST ด้วย service-role key แบบเดียวกับ `store.py` (key `sb_secret_` ส่งใน `apikey` เท่านั้น)
- บอทรันบนเครื่องเจ้าของ ปิด/เปิดได้ตลอด
- ห้ามแตะเว็บ (`web/`) และตาราง `expenses` เดิม

## Approaches

| | วิธี | ข้อดี | ข้อเสีย |
|---|---|---|---|
| **A (เลือก)** | ตรรกะใน Python เรียก PostgREST + ฟังก์ชัน SQL atomic เฉพาะจุดเสี่ยง (เข้าร่วม, ยืนยัน/แก้/ลบรายการ, ปิด, ออก/เตะ, โอนแล้ว) | กฎ ≤10 คน / ปิดแล้วห้ามแก้ รับประกันได้จริง; ส่วนอื่นเทสต์ด้วย pytest; ตรงกับ `store.py` | ต้องรัน SQL ใหม่ใน Supabase |
| B | ตรรกะทั้งหมดใน Python | ไม่มี SQL function | กดพร้อมกันได้ 11 คน / รายการหลุดเข้าหลังปิด — ผิดสเปก ตัดทิ้ง |
| C | ตรรกะทั้งหมดเป็น SQL function | atomic ทั้งหมด | เทสต์ด้วย pytest ไม่ได้ แก้ยาก ไม่คุ้มกับกลุ่ม 10 คน |

เจ้าของเลือก **A**

## สิ่งที่เจ้าของตัดสินใจแล้ว

- ที่เก็บข้อมูล: **Supabase** (ไม่ใช่ SQLite)
- ปิดปาร์ตี้: **แค่สรุปการโอน** ไม่บันทึกลงรายจ่ายส่วนตัว
- thread: **ส่วนตัว** (private thread)
- โค้ดเชิญ: **ระบบสร้างให้**
- เข้าร่วม: **ทั้งปุ่ม [เข้าร่วม] ในช่องหลัก และ `/party join <code>`**
- **สลิปซ้ำห้ามบันทึก** (เลขอ้างอิงหรือรูปเดียวกันในปาร์ตี้เดียวกัน) — แจ้งเตือนและปุ่มยืนยันกดไม่ได้
- **ทุกคนในปาร์ตี้เห็นทุกรายการ** ว่าใครจ่ายอะไรเท่าไหร่ และ**ทุกคนอัปสลิปได้**
- branch ชื่อ `party-group`
- วิธีสร้าง: **A**

## การออกแบบ

### โมดูล

| ไฟล์ | หน้าที่ |
|---|---|
| `party_math.py` | pure: หาร, net, รายการโอน — ไม่ import discord / network |
| `party_store.py` | อ่าน/เขียนตารางปาร์ตี้ผ่าน PostgREST + เรียก SQL function (blocking, ห่อ `asyncio.to_thread`) |
| `party_bot.py` | slash commands `/party …`, DynamicItem (ปุ่ม/เมนู), ฟอร์มยืนยัน, ข้อความสรุป |
| `supabase/party_schema.sql` | ตาราง + index + RLS + SQL functions |
| `bot.py` | แก้น้อยที่สุด: ลงทะเบียน party (commands + dynamic items) ใน `setup_hook`, ส่งข้อความใน thread ปาร์ตี้ไป party handler |

### คำนวณ (`party_math.py`) — สำคัญที่สุด

- เงินเป็น `int` สตางค์ทุกที่; แปลงเป็นบาทตอนแสดงผลเท่านั้น (`format_satang(12345) == "123.45"`)
- `split(amount: int, participants: Sequence[str]) -> dict[str, int]` — ผู้มีส่วนเรียงตาม `int(discord_id)`;
  ทุกคนได้ `amount // n` แล้วแจกเศษทีละ 1 สตางค์ให้คนแรก ๆ ตามลำดับนั้น; `sum == amount` เสมอ; participants ว่าง / ซ้ำ / amount ≤ 0 → `ValueError`
- `balances(members, expenses) -> dict[str, Balance(paid, owed, net)]` — `net = paid − owed`; `assert sum(net) == 0`;
  สมาชิกที่ไม่มีส่วนเลยได้ 0 ทั้งสามค่า
- `settle(net: dict[str, int]) -> list[Transfer(from_id, to_id, amount)]` — greedy: ลูกหนี้ติดลบมากสุด → เจ้าหนี้บวกมากสุด
  จำนวน `min(|ลูกหนี้|, เจ้าหนี้)` วนจนหมด; เสมอกันตัดสินด้วย `int(discord_id)` น้อยกว่าก่อน (ผลลัพธ์แน่นอน); ได้ ≤ n−1 รายการ
- เทสต์ที่ต้องผ่าน (จากสเปก):
  - A จ่าย 900 (A,B,C), B จ่าย 300 (A,B,C) → net A +500, B −100, C −400 → โอน C→A 400, B→A 100
  - 100 บาท 3 คน → 33.34 / 33.33 / 33.33
  - A จ่าย 300 ให้ B,C → A +300, B −150, C −150
  - สมาชิกที่ไม่มีส่วนเลย → net 0 และไม่อยู่ในรายการโอน
  - ทุกคนจ่ายเท่ากันพอดี → ไม่มีรายการโอน
  - เพิ่ม: property test สุ่ม 200 ชุด (สมาชิก 1–10, รายการ 0–30) → `sum(split)==amount`, `sum(net)==0`,
    ใช้รายการโอนแล้วทุกคนเป็น 0, จำนวนโอน ≤ n−1

### ข้อมูล (Supabase)

| ตาราง | คอลัมน์หลัก |
|---|---|
| `parties` | `id uuid`, `code text unique` (6 ตัว), `name text` (≤ 100), `host_id text`, `guild_id`, `channel_id` (ช่องหลัก), `thread_id text unique`, `invite_message_id`, `status` (`open`/`closed`/`settled`), `created_at`, `closed_at`, `settled_at` |
| `party_members` | `party_id`, `discord_id`, `joined_at` — PK (`party_id`, `discord_id`) |
| `party_expenses` | `id uuid`, `party_id`, `status` (`draft`/`confirmed`), `amount_satang bigint` (> 0 เมื่อ confirmed), `payer_id`, `created_by`, `participants text[]` (ใช้ตอน draft), `note text` (≤ 100), `source` (`slip`/`cash`), `trans_ref`, `image_sha256`, `slip_message_url`, `summary_message_id`, `created_at`, `updated_at` |
| `party_expense_shares` | `expense_id`, `discord_id`, `share_satang bigint` — PK (`expense_id`, `discord_id`); มีเฉพาะรายการ confirmed |
| `party_transfers` | `id uuid`, `party_id`, `from_id`, `to_id`, `amount_satang`, `paid bool`, `paid_by`, `paid_at`, `message_id` |

- RLS เปิด ไม่มี policy (เหมือน `expenses`) — เข้าถึงผ่าน service role เท่านั้น
- **กันสลิปซ้ำในฐานข้อมูล:** partial unique index `(party_id, trans_ref)` และ `(party_id, image_sha256)` where `status = 'confirmed'`
  และค่านั้นไม่ null — กดยืนยันพร้อมกันสองคนก็บันทึกได้แค่ใบเดียว (`party_save_expense` คืน `duplicate`)
- Discord id เก็บเป็น `text`
- **draft อยู่ใน DB** (ไม่ใช่ใน memory) เพื่อให้ฟอร์มยืนยันใช้ต่อได้หลังรีสตาร์ท; draft ไม่ถูกนับในการคำนวณใด ๆ
- draft ที่ค้างเกิน 24 ชั่วโมงถือว่าหมดอายุ (กดแล้วแจ้งให้อัปใหม่) และถูกลบตอนปิดปาร์ตี้
- ตอนวาดฟอร์ม ตัดผู้มีส่วน/คนจ่ายที่ไม่ใช่สมาชิกแล้ว (เช่นถูกเตะ) ออกจาก draft อัตโนมัติ

### SQL functions (atomic, ล็อกแถว `parties` ด้วย `for update`)

ทุกตัวคืนข้อความสถานะสั้น ๆ ให้ Python แปลเป็นข้อความไทย; execute ได้เฉพาะ `service_role`

| ฟังก์ชัน | ทำอะไร / สถานะที่คืน |
|---|---|
| `party_join(party, member)` | `ok` / `full` (≥ 10) / `already` / `closed` / `not_found` |
| `party_remove_member(party, member, actor)` | ออกเอง (actor = member) หรือเตะ (actor = host); host ออก/ถูกเตะไม่ได้; `ok` / `involved` (เป็นคนจ่ายหรืออยู่ใน share ของรายการ confirmed ใด) / `not_member` / `forbidden` / `closed` |
| `party_save_expense(expense, actor, amount, payer, note, shares jsonb)` | ยืนยัน draft หรือแก้รายการ confirmed: ตรวจปาร์ตี้ `open`, actor เป็นผู้สร้างหรือ host, payer และทุกคนใน shares เป็นสมาชิก, ผลรวม shares = amount, สลิปไม่ซ้ำ → เขียน shares ใหม่ทั้งชุด, status = confirmed; `ok` / `closed` / `forbidden` / `invalid` / `duplicate` |
| `party_delete_expense(expense, actor)` | `ok` / `closed` / `forbidden` |
| `party_close(party, actor)` | open → closed (host เท่านั้น); `ok` / `already_closed` / `forbidden` |
| `party_toggle_paid(transfer, actor)` | actor ต้องเป็น from หรือ to; สลับ paid; ครบทุกรายการ → settled; คืน `paid` / `unpaid` / `settled` / `forbidden` / `locked` (settled แล้ว) |

shares คำนวณใน Python ด้วย `party_math.split` แล้วส่งให้ฟังก์ชันตรวจผลรวมอีกชั้น

**การปิด (สองจังหวะ):** `party_close` ล็อกสถานะก่อน → Python อ่านรายการ confirmed ทั้งหมด → `balances` + `settle` → insert
`party_transfers` (เฉพาะถ้ายังไม่มี) → โพสต์สรุป. ถ้าบอทล้มหลังล็อกแต่ก่อนโพสต์ host สั่ง `/party end` ซ้ำเพื่อสร้างสรุปได้
(idempotent). ไม่มีรายการโอนเลย (ไม่มีรายการ หรือทุกคนเท่ากัน) → settled ทันที

### Discord

**คำสั่ง** (`app_commands.Group("party")`, sync เฉพาะ guild ของ `DISCORD_CHANNEL_ID` ตอน `on_ready` ให้ขึ้นทันที):

| คำสั่ง | ที่ใช้ | สิทธิ์ |
|---|---|---|
| `create <ชื่อ>` | ช่องข้อความใดก็ได้ที่บอทสร้าง private thread ได้ | ทุกคน |
| `join <code>` | ที่ไหนก็ได้ | ทุกคน (ตามกติกาเข้าร่วม) |
| `add <ยอด> <หมายเหตุ>` | ใน thread ปาร์ตี้ | สมาชิก |
| `status` | ใน thread ปาร์ตี้ | สมาชิก |
| `end` | ใน thread ปาร์ตี้ | host |
| `kick <สมาชิก>` | ใน thread ปาร์ตี้ | host |
| `leave` | ใน thread ปาร์ตี้ | สมาชิก |

คำสั่งใน thread หาปาร์ตี้จาก `thread_id`; ใช้นอก thread ปาร์ตี้ → ข้อความ ephemeral บอกให้ใช้ใน thread

**โค้ดเชิญ:** 6 ตัวจาก `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (ตัด 0 O 1 I L) สุ่มด้วย `secrets`, ชนกับที่มี → สุ่มใหม่ (unique index)

**create:** สร้าง private thread ชื่อ "🎉 <ชื่อ>" ในช่องที่พิมพ์ → เพิ่ม host → insert party + member → โพสต์ในช่องหลัก:
ชื่อปาร์ตี้, host, โค้ด, จำนวนสมาชิก, ปุ่ม [เข้าร่วม] → ตอบ host แบบ ephemeral พร้อมลิงก์ thread และโค้ด
**join (ทั้งปุ่มและคำสั่ง):** `party_join` → `ok` ⇒ `thread.add_user` + แก้ข้อความเชิญ (จำนวนสมาชิก) + โพสต์ใน thread "<@x> เข้าร่วมแล้ว";
อื่น ๆ ⇒ ephemeral ภาษาไทย. ปิดปาร์ตี้ ⇒ แก้ข้อความเชิญให้ปุ่มเป็น disabled "ปิดแล้ว"

**สลิปใน thread:** `on_message` ตรวจก่อน flow ส่วนตัว: ถ้าเป็น thread ของปาร์ตี้ที่ `open` และผู้ส่งเป็นสมาชิก →
อ่าน QR + OCR (ใช้ `_ocr_slot`) → สร้าง draft (amount, payer = ผู้ส่ง, participants = ทุกคน, trans_ref, sha256 ของรูป, ลิงก์ข้อความ)
→ reply ในthread: "พบสลิป ฿X — <@ผู้ส่ง> กดเพื่อตรวจและยืนยัน" + ปุ่ม [ตรวจ/ยืนยัน] (กดได้เฉพาะผู้ส่ง)
→ กดแล้วเปิดฟอร์มแบบ ephemeral. (Discord ส่ง ephemeral ได้เฉพาะตอบ interaction — ข้อความอัปรูปไม่ใช่ interaction จึงต้องมีปุ่มคั่น 1 ครั้ง)
**สมาชิกทุกคนอัปสลิปได้** (ไม่ใช่แค่ host). ข้อความอื่นใน thread ปาร์ตี้ (ไม่ใช่รูป) → ไม่ทำอะไร. ปาร์ตี้ปิดแล้ว/ผู้ส่งไม่ใช่สมาชิก → reply สั้น ๆ ว่าเพิ่มไม่ได้
สลิปใน thread **ไม่เข้า** flow ส่วนตัวเด็ดขาด (thread id ≠ `DISCORD_CHANNEL_ID` อยู่แล้ว แต่ตรวจปาร์ตี้ก่อนเพื่อความชัด)

**ฟอร์มยืนยัน (ephemeral):** embed แสดง ยอด, คนจ่าย, ผู้มีส่วน + ส่วนแบ่งคนละเท่าไร (คำนวณสด), หมายเหตุ, คำเตือนสลิปซ้ำ
(trans_ref หรือ sha256 ซ้ำกับรายการ confirmed ในปาร์ตี้เดียวกัน — **"สลิปนี้ถูกบันทึกในปาร์ตี้แล้ว" และปุ่มยืนยันกดไม่ได้**; ตรวจตั้งแต่ตอนอัป:
ถ้าซ้ำ บอท reply ใน thread ว่าซ้ำเลยโดยไม่สร้าง draft), คำเตือน "อ่านยอดไม่ได้ กรุณากรอกยอด" (ปุ่มยืนยันปิดจนกว่าจะมียอด)
- เมนูคนจ่าย (StringSelect สมาชิก, ค่าเริ่มต้น = ผู้อัป)
- เมนูผู้มีส่วน (StringSelect multi, min 1, ค่าเริ่มต้น = ทุกคน)
- ปุ่ม [แก้ยอด/หมายเหตุ] → modal; [ยืนยัน]; [ยกเลิก] (ลบ draft)
- ทุกการเปลี่ยนแปลงเขียนลง draft ใน DB ทันทีแล้ววาดฟอร์มใหม่
- [ยืนยัน] → `party_save_expense` → แก้ข้อความ "พบสลิป" ใน thread ให้เป็นสรุปรายการ (หรือโพสต์ใหม่สำหรับ `/party add`)
- `/party add <ยอด> <หมายเหตุ>` ข้ามขั้นสลิป: สร้าง draft (source = cash) แล้วเปิดฟอร์มเดียวกันทันที; ยอดผิดรูปแบบ → ephemeral แจ้ง

**status:** โพสต์ใน thread ให้**ทุกคนในปาร์ตี้เห็น** — embed แสดงรายการ confirmed (ล่าสุด 25 รายการ: วันที่, ยอด, คนจ่าย, ผู้มีส่วน, หมายเหตุ)
+ ตาราง จ่ายไป / รับผิดชอบ / สุทธิ ของทุกคน + ปุ่ม [แก้/ลบรายการ] → คนกดได้เมนูแบบ ephemeral เฉพาะรายการที่ตัวเองมีสิทธิ์
(ผู้เพิ่มรายการ หรือ host) → เปิดฟอร์มเดียวกับตอนยืนยัน (โหมดแก้ไข) มีปุ่ม [ลบ] เพิ่ม
นอกจากนี้ทุกรายการที่ยืนยันแล้วถูกโพสต์สรุปใน thread อยู่แล้ว (ยอด, คนจ่าย, ผู้มีส่วน + ส่วนแบ่งแต่ละคน)

**end:** ephemeral ยืนยัน [ปิดปาร์ตี้] / [ยกเลิก] → ปิดตามขั้นสองจังหวะ → โพสต์สรุปใน thread: ยอดรวม, ตารางรายคน,
รายการโอน "<@A> → <@B> : 250.00 บาท" แต่ละรายการมีปุ่ม [โอนแล้ว] (กดได้เฉพาะคนโอน/คนรับ, กดซ้ำเพื่อยกเลิกได้จนกว่าจะ settled)
→ ครบ → แก้สรุปเป็น "✅ เคลียร์ครบแล้ว" + โพสต์แจ้ง

**ปุ่มหลังรีสตาร์ท:** ทุกปุ่ม/เมนูเป็น `discord.ui.DynamicItem` ลงทะเบียนใน `setup_hook` ด้วย custom_id รูปแบบ
`party:<action>:<uuid>` (เช่น `party:join:<party_id>`, `party:draft:payer:<expense_id>`, `party:paid:<transfer_id>`) — ทุกครั้งที่กดอ่าน state จาก DB
และตรวจสิทธิ์ใหม่ (สมาชิก / ผู้สร้าง / host / ผู้โอน-รับ)

**สิทธิ์บอทที่ต้องเพิ่ม:** Create Private Threads, Send Messages in Threads, Manage Threads (เพิ่มสมาชิกเข้า private thread),
และ scope `applications.commands` (ถ้าคำสั่ง `/party` ไม่ขึ้น ให้เชิญบอทใหม่ด้วย scope `bot applications.commands`)

### Config

ไม่ต้องเพิ่มตัวแปรใหม่ — ใช้ `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DISCORD_CHANNEL_ID` (หา guild สำหรับ sync คำสั่ง)

### การทดสอบ

- `tests/test_party_math.py` — กรณีในสเปกทั้งหมด + property test
- `tests/test_party_store.py` — httpx MockTransport: header, query กรองตามปาร์ตี้, เรียก RPC ถูกชื่อ/พารามิเตอร์, แปลสถานะ
- helper ที่ไม่ต้องต่อ Discord (สร้างโค้ด, แปลยอด "1,234.50" → สตางค์, จัดรูปข้อความสรุป, parse/สร้าง custom_id) มี unit test
- เทสต์เดิมทั้งหมดต้องผ่าน (flow ส่วนตัวไม่เปลี่ยน)
- SQL functions: สคริปต์ smoke test กับ Supabase จริง (`python party_store.py`) — join ครบ 10 แล้วคนที่ 11 ได้ `full`, ปิดแล้ว save ได้ `closed`, ลบข้อมูลทดสอบท้ายสคริปต์
- ทดสอบมือใน Discord: 2–3 บัญชี สร้าง → เข้าร่วม (ปุ่ม + โค้ด) → อัปสลิป + เงินสด → แก้/ลบ → เตะ/ออก → **รีสตาร์ทบอทแล้วกดปุ่มเก่า** → ปิด → โอนแล้วครบ → settled

### ลำดับงาน (ตามสเปก)

1. `party_math.py` + เทสต์
2. schema + `party_store.py` + create / join / leave / kick
3. อัปสลิป + ฟอร์มยืนยัน + `/party add`
4. status + แก้ / ลบ
5. end + สรุป + [โอนแล้ว]

## ปล่อยให้คนสร้างตัดสินใจ

- หน้าตา embed / emoji / สี, ข้อความไทยที่ไม่ได้ระบุ
- แยก `party_bot.py` ย่อยเพิ่มหรือไม่ ถ้าไฟล์ใหญ่
- ใช้ `_request` ร่วมกับ `store.py` (เช่น base class) หรือเขียนแยก
