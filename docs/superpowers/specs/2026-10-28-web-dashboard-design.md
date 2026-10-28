# Money Captuer Web — design

วันที่: 2026-10-28 · กำหนดส่ง: 2026-10-30 (ก่อนวันสัมภาษณ์)

## ทำไม

โจทย์สัมภาษณ์งาน Web Programming: สร้างและ deploy เว็บไซต์จริง 1 โปรเจกต์ ต้องมี
**Live URL ที่เปิดใช้ได้จริง**, **ลิงก์ GitHub**, และ **Tech Stack** ไปแสดง

เจ้าของมีบอท Discord อ่านสลิปโอนเงินอยู่แล้ว (repo นี้) — แทนที่จะเริ่มโปรเจกต์ใหม่ จะต่อยอดเป็นเว็บที่
login แล้วเห็นรายจ่ายของตัวเองเป็นกราฟ และอัปโหลดสลิปผ่านเว็บได้ ให้ระบบ "มีอะไรมากขึ้น" และเป็นของจริงที่คนอื่นใช้ได้

ความสำเร็จ = วันสัมภาษณ์ คนสัมภาษณ์เปิด URL → login ด้วยบัญชีของเขาเอง → อัปโหลดสลิป → เห็นรายการและกราฟของเขา
โดยไม่เห็นข้อมูลของคนอื่น และเจ้าของอธิบายได้ว่าระบบทำงานอย่างไร

## Goals

1. **Login** ด้วย Discord (หลัก), Google, และอีเมล
2. **แยกข้อมูลรายคน** — แต่ละคนเห็น/แก้ได้เฉพาะรายการของตัวเอง
3. **เชื่อมบัญชี** — บัญชีเว็บหนึ่งบัญชีผูกได้หลายวิธี login; ผู้ที่ login ด้วย Google/อีเมลกด "เชื่อม Discord"
   ในหน้าตั้งค่าได้ เพื่อให้สลิปที่ส่งผ่านบอทรวมเข้าบัญชีเดียวกัน
4. **อัปโหลดสลิปผ่านเว็บ** — อ่าน QR + OCR ด้วยโค้ดเดิม → แสดงค่าที่อ่านได้ให้แก้ → เลือกหมวด → บันทึก
   กันบันทึกสลิปซ้ำด้วยเลขอ้างอิง (transRef) เหมือนบอท
5. **Dashboard** — ยอดรวมเดือนที่เลือก, กราฟแยกตามหมวด, กราฟยอดรายเดือนย้อนหลัง, ตารางรายการ (ลบได้)
6. **บอท Discord ใช้ต่อได้** — บันทึกลงฐานข้อมูลเดียวกับเว็บ พร้อม Discord ID ของคนส่ง
7. **Deploy** ขึ้น URL จริงบน Render และ README มี Live URL + Tech Stack

## Non-goals (ไม่ทำในรอบนี้)

- ฟีเจอร์แบบฟอร์มภาษี / ค่าลดหย่อน
- ปุ่มหรือบัญชี "ทดลองใช้" แบบไม่ต้อง login (เจ้าของตัดสินใจไม่เอา)
- Google Sheet — เลิกใช้; ไม่ย้ายข้อมูลเก่าจาก Sheet (ถ้ามีเวลาเหลือค่อยทำสคริปต์ import)
- โฮสต์บอท Discord บน server — บอทยังรันบนเครื่องเจ้าของเหมือนเดิม
- ตรวจสลิปปลอมกับธนาคาร
- React / SPA framework

## Anti-goals (ถ้าเกิดขึ้น ถือว่าล้มเหลวแม้ระบบจะทำงาน)

- ผู้ใช้คนหนึ่งเห็น แก้ หรือลบรายการของอีกคนได้ — หรือรู้ได้ว่า "สลิปนี้มีคนอื่นบันทึกไว้แล้ว"
- secret (Supabase service key, Typhoon key, Discord token) หลุดไปที่ browser หรือเข้า git
- คนสัมภาษณ์เปิด URL แล้วเจอหน้าพัง / error / login ไม่ผ่าน

## Constraints

- เวลา 2 วัน คนทำหลักคือ AI agent กับเจ้าของที่เพิ่งเริ่มทำเว็บ — เลือกชิ้นส่วนน้อยที่สุด
- ต้องใช้โค้ดอ่านสลิปเดิม: `slip_qr.read_slip_qr()` และ `slip_reader.read_slip()` (blocking, มีเทสต์แล้ว)
  — ห้ามเขียนใหม่
- Typhoon OCR rate limit 20 req/min (ร่วมกันทุกผู้ใช้) — `read_slip` มี backoff 429 อยู่แล้ว
- Python 3.12 (เครื่องเจ้าของ), Windows สำหรับพัฒนา
- Render free tier หลับหลังไม่มี request 15 นาที (cold start ~30–50 วินาที) — ยอมรับได้ เจ้าของปลุกก่อนสัมภาษณ์

## Approaches

| | วิธี | ข้อดี | ข้อเสีย |
|---|---|---|---|
| **A (เลือก)** | FastAPI ทำทั้งหน้าเว็บ + API, Chart.js, Supabase (Auth/DB), deploy Render ที่เดียว | ใช้โค้ด Python เดิมตรง ๆ, ภาษาเดียว, deploy เดียว, เสี่ยงน้อยสุด | หน้าเว็บเป็น HTML/JS ธรรมดา, Render free มี cold start |
| B | Next.js บน Vercel + Python OCR service บน Render | stack ดูทันสมัยกว่า | สองภาษา สอง deploy ต้องเชื่อมกัน — เสี่ยงไม่ทันใน 2 วัน |
| C | Next.js + Python function บน Vercel ที่เดียว | deploy เดียว | OpenCV เกือบชนขนาดสูงสุดของ function, OCR อาจเกิน timeout — ตัดทิ้ง |

เจ้าของเลือก **A** เพราะความเสี่ยงหลักคือเวลา และต้องเปิดใช้ได้จริงวันสัมภาษณ์
เหตุผลไว้ตอบสัมภาษณ์: นำโค้ด OCR ที่ทดสอบแล้วกลับมาใช้ แทนที่จะแยก service

ทั้งสามทางใช้ **Supabase** เพราะมี OAuth Discord/Google + อีเมล, การเชื่อม identity หลายแบบในบัญชีเดียว,
และ Postgres พร้อมในที่เดียว — ทำ auth + เชื่อมบัญชีเองไม่ทัน 2 วัน

## สิ่งที่ตัดสินใจแล้ว (โดยเจ้าของ)

- Stack แบบ A
- Login: Discord เป็นหลัก + Google + อีเมล ทำครบในรอบนี้
- ไม่มีปุ่มทดลองใช้
- ไม่ทำเรื่องภาษี

## การออกแบบ

### ภาพรวม

```
Browser ──(supabase-js: login)──▶ Supabase Auth
   │  Authorization: Bearer <access_token>
   ▼
FastAPI (Render) ──▶ slip_qr / slip_reader ──▶ Typhoon API
   │  service-role key, กรองด้วย user_id เสมอ
   ▼
Supabase Postgres ◀── bot.py (Discord, รันบนเครื่องเจ้าของ)
```

### ข้อมูล (Supabase Postgres)

ตาราง `expenses` (แทนแถวใน Google Sheet — ฟิลด์เดิมของ `ExpenseRecord` ครบ):

| คอลัมน์ | ชนิด | หมายเหตุ |
|---|---|---|
| id | uuid PK | |
| user_id | uuid null → `auth.users` | เจ้าของรายการ; null = มาจากบอทแต่ Discord ID นี้ยังไม่ผูกบัญชีเว็บ |
| discord_id | text null | Discord user id ของคนส่ง (เฉพาะรายการจากบอท) |
| source | text | `'web'` หรือ `'discord'` |
| date | date | วันที่บนสลิป (ไม่มี → วันนี้ เวลาไทย) |
| time | text null | "HH:MM" |
| amount | numeric(12,2) | > 0 |
| receiver | text | |
| category | text | ต้องอยู่ในรายการหมวดของ config |
| note | text | |
| bank | text | ชื่อธนาคาร |
| trans_ref | text null | normalize แบบ `sheets._normalize_ref` |
| discord_link | text null | |
| created_at | timestamptz default now() | (แทน "บันทึกเมื่อ") |

- กันซ้ำ: partial unique index `(user_id, trans_ref)` where both not null และ `(discord_id, trans_ref)` where both
  not null — **ซ้ำนับเฉพาะในข้อมูลของคนเดียวกัน** ไม่เปิดเผยข้อมูลข้ามคน
- รายการที่อัปโหลดผ่านเว็บโดยผู้ใช้ที่ผูก Discord แล้ว ใส่ `discord_id` ของเขาด้วย เพื่อให้สลิปเดียวกันที่ส่งซ้ำผ่านบอทถูกจับได้
- เปิด RLS ทุกตาราง และ **ไม่มี policy ให้ anon/authenticated** — เข้าถึงข้อมูลผ่าน FastAPI (service role) เท่านั้น
  เพื่อให้มีจุดตรวจสิทธิ์จุดเดียว

### Auth และการเชื่อมบัญชี

- หน้าเว็บใช้ `supabase-js` (anon key, ใช้ใน browser ได้) ทำ login: `signInWithOAuth({provider:'discord'|'google'})`
  และอีเมลแบบ magic link (`signInWithOtp`) — ไม่ต้องจัดการรหัสผ่าน
  (ระบบส่งอีเมลในตัวของ Supabase ส่งได้แค่ไม่กี่ฉบับต่อชั่วโมง — พอสำหรับเดโม ถ้าไม่พอค่อยต่อ SMTP)
- Supabase ผูก identity ที่อีเมล verified ตรงกันเข้าบัญชีเดียวกันให้อัตโนมัติ (เช่น Google กับ Discord อีเมลเดียวกัน)
- ทุก request ไป FastAPI แนบ access token; FastAPI ตรวจด้วย `supabase.auth.get_user(token)` → ได้ user id
  และ identities
- **Discord ID ของผู้ใช้** = `provider_id` ของ identity `discord` ใน user นั้น
- เชื่อม Discord ทีหลัง: หน้าตั้งค่ามีปุ่ม "เชื่อม Discord" → `supabase.auth.linkIdentity({provider:'discord'})`
  (ต้องเปิด Manual Linking ใน Supabase)
- **รับรายการค้างจากบอท:** ทุกครั้งที่ API ยืนยันตัวผู้ใช้ที่มี Discord identity ให้รัน
  `update expenses set user_id = <me> where discord_id = <my discord id> and user_id is null`
  (idempotent) — รายการที่ส่งผ่านบอทก่อนสมัครเว็บจึงตามเข้าบัญชีอัตโนมัติ ถ้าชนกับ trans_ref ที่บัญชีมีอยู่แล้ว
  ให้ข้ามแถวนั้น (ไม่ error)

### API (FastAPI)

ทุก endpoint ยกเว้นหน้า HTML และ `/health` ต้องมี token ที่ถูกต้อง ไม่งั้น 401

| Method | Path | ทำอะไร |
|---|---|---|
| GET | `/` , `/dashboard`, `/settings` | หน้า HTML (static + JS) |
| GET | `/api/config` | Supabase URL + anon key + รายการหมวด (ให้หน้าเว็บใช้) |
| POST | `/api/slips/read` | รับรูป (PNG/JPEG, ≤ 10MB) → QR + OCR → คืนร่าง `{amount,date,time,receiver,bank,trans_ref,duplicate,error}` ไม่บันทึก |
| POST | `/api/expenses` | บันทึกรายการ (ตรวจหมวด, amount > 0, กันซ้ำ → 409) |
| GET | `/api/expenses?month=YYYY-MM` | รายการของฉันในเดือนนั้น เรียงวันที่ใหม่สุดก่อน |
| DELETE | `/api/expenses/{id}` | ลบ — ไม่ใช่ของฉัน → 404 (ไม่บอกว่ามีอยู่) |
| GET | `/api/summary?month=YYYY-MM` | ยอดรวม + ยอดแยกหมวดของเดือน + ยอดรายเดือนย้อนหลัง 6 เดือน |
| GET | `/health` | สำหรับ Render health check |

- เรียก `read_slip_qr` / `read_slip` ผ่าน `asyncio.to_thread` (blocking เหมือนในบอท)
- logic ที่ใช้ร่วมกับบอท (normalize วันที่/เวลา, parse ยอด, build record) ย้ายเป็นฟังก์ชันกลางที่ทั้งสองฝั่ง import
  ได้ แทนที่จะคัดลอก

### หน้าเว็บ

HTML + JS ธรรมดา เสิร์ฟจาก FastAPI, กราฟใช้ Chart.js (CDN, pin เวอร์ชัน), ภาษาไทย, ใช้บนมือถือได้

1. **หน้าแรก / login** — อธิบายสั้น ๆ ว่าเว็บทำอะไร + ปุ่ม Login ด้วย Discord (เด่นสุด), Google, อีเมล
2. **Dashboard** — เลือกเดือน, ยอดรวม, กราฟโดนัทตามหมวด, กราฟแท่งรายเดือน, ปุ่ม "อัปโหลดสลิป",
   ตารางรายการ (ลบได้), สถานะว่างที่บอกให้อัปโหลดสลิปแรก
3. **อัปโหลดสลิป** (modal หรือหน้าแยก) — เลือกรูป → สถานะกำลังอ่าน (OCR ใช้หลายวินาที) → ฟอร์มที่เติมค่าที่อ่านได้
   ให้แก้ยอด/วันที่/ผู้รับ/โน้ต + เลือกหมวด → บันทึก; อ่านยอดไม่ได้ → ให้กรอกเอง; สลิปซ้ำ → แจ้งก่อนบันทึก
4. **ตั้งค่า** — แสดงวิธี login ที่ผูกแล้ว, ปุ่มเชื่อม Discord, ปุ่ม logout

### บอท Discord

- แทน `sheets.ExpenseSheet` ด้วย storage ที่เขียนลง Supabase (service role) โดยคง interface เดิม
  (`is_duplicate`, `append_expense`, `month_summary`) ให้แก้ `bot.py` น้อยที่สุด
- บันทึก `discord_id = message.author.id`, `source='discord'`, `user_id = null` เสมอ — บอทไม่ต้องรู้เรื่องบัญชีเว็บ
  (identity ของ Supabase อยู่ใน schema `auth` ที่อ่านผ่าน API ปกติไม่ได้) ฝั่งเว็บจะ "รับรายการค้าง" ให้ตอนผู้ใช้เปิดเว็บ
- กันซ้ำ/สรุปเดือนคิดตาม `discord_id` ของคนส่ง (ไม่ใช่ทั้งช่องรวมกัน)
- ข้อความ "บันทึกแล้ว" แทนชื่อแท็บ Sheet ด้วยลิงก์ไปหน้า dashboard
- `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_FILE` และ `gspread` ไม่ต้องใช้แล้ว

### Config / secrets

เพิ่มใน `.env` และ Render env: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `PUBLIC_BASE_URL`
— service role key อยู่ฝั่ง server เท่านั้น; อัปเดต `.env.example`
เว็บไม่ต้องมี `DISCORD_TOKEN` (`load_config(required=...)` รองรับอยู่แล้ว)

### Deploy

Render Web Service จาก GitHub repo, start: `uvicorn web.app:app --host 0.0.0.0 --port $PORT`, health check `/health`
ตั้ง Redirect URL ของ Supabase Auth และ OAuth (Discord, Google) ให้ชี้ URL ของ Render

### สิ่งที่เจ้าของต้องทำเอง (agent ทำแทนไม่ได้)

1. สร้างโปรเจกต์ Supabase → ให้ URL / anon key / service role key
2. Discord Developer Portal (ใช้แอปของบอทเดิม) → OAuth2 Client ID/Secret + redirect URL ของ Supabase
3. Google Cloud → OAuth client สำหรับ Google login
4. สมัคร Render และเชื่อม GitHub repo

### ลำดับงาน (2 วัน)

ต้องมี — **วันที่ 1:** schema Supabase → login Discord → อัปโหลด/อ่าน/บันทึกสลิป → ตารางรายการ → deploy ขึ้น Render
ให้ได้ URL จริงภายในวันแรก
ต้องมี — **วันที่ 2 ช่วงแรก:** dashboard + กราฟ → บอทเขียนลง Supabase → README (Live URL, Tech Stack, วิธีใช้)
ทำต่อถ้าทัน: Google + อีเมล login → เชื่อม Discord ในหน้าตั้งค่า → แก้ไขรายการ → เก็บรูปสลิป → import ข้อมูลจาก Sheet

เหตุผลของลำดับ: ได้ URL ที่ใช้งานได้เร็วที่สุด ส่วนที่ "เผื่อไว้" ถ้าไม่ทันก็ยังส่งงานได้

### การทดสอบ

- เทสต์เดิมใน `tests/` ต้องผ่าน (ปรับ test_sheets ตาม storage ใหม่)
- เทสต์ API ด้วย FastAPI TestClient + storage ปลอม: ไม่มี token → 401, ผู้ใช้ A ลบ/ดูของ B ไม่ได้, กันซ้ำ → 409,
  หมวดไม่ถูกต้อง → 422, การรับรายการค้างจากบอท
- ทดสอบด้วยมือบน URL จริง: login แต่ละแบบ, อัปโหลดสลิปจริง, สองบัญชีไม่เห็นข้อมูลกัน

## ปล่อยให้คนสร้างตัดสินใจ

- โครงสร้างโฟลเดอร์ (เสนอ `web/` สำหรับแอป + `web/static/`), ชื่อฟังก์ชัน, หน้าตา/สี
- จะใช้ supabase-py หรือเรียก REST ตรง
- การจัดการ error/ข้อความภาษาไทยในหน้าเว็บ
