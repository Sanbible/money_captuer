# Money Captuer

เว็บ + บอท Discord สำหรับจดรายจ่ายส่วนตัวจากสลิปโอนเงินของธนาคารไทย — อัปโหลดรูปสลิป ระบบอ่านยอดเงิน วันที่ และผู้รับให้อัตโนมัติ
แล้วสรุปเป็นกราฟรายเดือน แต่ละคน login แล้วเห็นเฉพาะรายการของตัวเอง

- **Live URL:** _(กำลัง deploy — จะอัปเดตลิงก์ที่นี่)_
- **GitHub:** https://github.com/Sanbible/money_captuer

> เว็บใช้ Render แบบฟรี ถ้าไม่มีคนเข้าเกิน 15 นาที เซิร์ฟเวอร์จะหลับ การเปิดครั้งแรกอาจช้าประมาณ 30–50 วินาที

## Tech Stack

| ส่วน | เทคโนโลยี |
|---|---|
| Backend / API | Python 3.12, FastAPI, Uvicorn |
| หน้าเว็บ | HTML + CSS + JavaScript (ไม่ใช้ framework), Chart.js |
| Login | Supabase Auth (OAuth ด้วย Discord) ผ่าน supabase-js |
| ฐานข้อมูล | Supabase Postgres (PostgREST) เปิด Row Level Security |
| อ่านสลิป | OpenCV (อ่าน QR), Typhoon OCR + Typhoon LLM (ดึงยอด/วันที่/ผู้รับ) |
| บอท | discord.py |
| Deploy | Render (web service), GitHub |
| Test | pytest, FastAPI TestClient, httpx MockTransport |

## ทำอะไรได้บ้าง

- **Login ด้วย Discord** — ไม่ต้องสมัครหรือจำรหัสผ่าน
- **อัปโหลดสลิป** (PNG/JPEG) → อ่าน QR เพื่อได้เลขอ้างอิงธุรกรรม + OCR อ่านยอด → ตรวจ/แก้ → เลือกหมวด → บันทึก
- **กันบันทึกซ้ำ** ด้วยเลขอ้างอิงจาก QR (นับเฉพาะข้อมูลของคนเดียวกัน)
- **Dashboard** — ยอดรวมของเดือน, กราฟแยกตามหมวด, กราฟย้อนหลัง 6 เดือน, ตารางรายการ (ลบได้)
- **บอท Discord** — ส่งสลิปเข้าช่องที่กำหนดก็บันทึกได้เหมือนกัน และ `!สรุป` ดูยอดรายเดือน
  รายการจากบอทจะขึ้นในเว็บอัตโนมัติเมื่อ login ด้วยบัญชี Discord เดียวกัน

## สถาปัตยกรรม

```
Browser ──(supabase-js: login ด้วย Discord)──▶ Supabase Auth
   │  Authorization: Bearer <access token>
   ▼
FastAPI (Render) ──▶ slip_qr / slip_reader ──▶ Typhoon API
   │  ตรวจ token กับ Supabase Auth แล้วกรองทุก query ด้วย user_id
   ▼
Supabase Postgres (ตาราง expenses) ◀── bot.py (Discord)
```

- browser ได้แค่ Supabase URL + publishable key; **service-role key อยู่ฝั่ง server เท่านั้น**
- ตาราง `expenses` เปิด RLS โดยไม่มี policy ให้ browser — อ่านเขียนได้ผ่าน API ของเว็บเท่านั้น
- บอทบันทึกด้วย Discord ID ของคนส่ง เมื่อคนนั้น login เว็บด้วย Discord ระบบจะผูกรายการเหล่านั้นเข้าบัญชีให้ (`claim_discord_expenses` ใน [supabase/schema.sql](supabase/schema.sql))

## โครงสร้างโปรเจกต์

```
money_captuer/
├── web/
│   ├── app.py           # FastAPI: หน้าเว็บ + /api/* (อัปโหลด/บันทึก/ลบ/สรุป)
│   ├── auth.py          # ตรวจ access token กับ Supabase Auth
│   ├── slips.py         # อ่านสลิปที่อัปโหลด (ใช้ slip_qr + slip_reader)
│   └── static/          # index / dashboard / settings + JS + CSS
├── store.py             # อ่าน/เขียนตาราง expenses ผ่าน PostgREST (ใช้ทั้งเว็บและบอท)
├── bot.py               # บอท Discord
├── slip_qr.py           # อ่าน QR + แปลง EMVCo TLV
├── slip_reader.py       # Typhoon OCR + Typhoon LLM + regex สำรอง
├── slip_common.py       # helper กลาง (วันที่ เวลา ยอดเงิน เขตเวลาไทย)
├── config.py            # โหลดค่าตั้งค่าจาก .env
├── supabase/schema.sql  # ตาราง + index + RLS + ฟังก์ชันผูกรายการ
├── render.yaml          # ตั้งค่า deploy บน Render
└── tests/               # unit tests
```

---

## รันในเครื่อง

### 1) ติดตั้ง

ต้องมี **Python 3.11+** (แนะนำ 3.12) เปิด PowerShell ในโฟลเดอร์โปรเจกต์:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # ถ้าติด "running scripts is disabled" ให้รัน Set-ExecutionPolicy -Scope CurrentUser RemoteSigned ก่อน
pip install -r requirements.txt
```

ถ้าไม่ activate venv ให้ใช้ `.\.venv\Scripts\python.exe` แทน `python` ในทุกคำสั่ง

### 2) เตรียมบัญชี

**Supabase (ฐานข้อมูล + login)**
1. สร้างโปรเจกต์ที่ https://supabase.com
2. **SQL Editor → New query** → วางเนื้อหาไฟล์ [supabase/schema.sql](supabase/schema.sql) → **Run**
3. **Project Settings → API Keys** → คัดลอก Project URL, Publishable key และ Secret key
4. **Authentication → Sign In / Providers → Discord** → เปิด แล้วใส่ Client ID / Client Secret ของแอป Discord
   (Discord Developer Portal → แอป → OAuth2) และนำ **Callback URL** ที่ Supabase แสดงไปใส่ใน **Redirects** ของแอป Discord
5. **Authentication → URL Configuration** → Site URL = URL ของเว็บ, Redirect URLs เพิ่ม `https://<เว็บ>/**` และ `http://127.0.0.1:8000/**`

**Typhoon (อ่านสลิป)** — สมัครที่ https://opentyphoon.ai แล้วสร้าง API key
แผนฟรีมี rate limit 20 request/นาที

**บอท Discord (ถ้าจะใช้บอท)**
1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application → เมนู **Bot** → Reset Token (ใช้เป็น `DISCORD_TOKEN`)
2. หน้า Bot → เปิด **Message Content Intent**
3. **OAuth2 → URL Generator** → scope `bot` + สิทธิ์ View Channels, Send Messages, Read Message History, Add Reactions, Embed Links → เชิญเข้าเซิร์ฟเวอร์
4. เปิด Developer Mode ใน Discord → คลิกขวาที่ช่อง → Copy Channel ID (ใช้เป็น `DISCORD_CHANNEL_ID`)

### 3) ตั้งค่า `.env`

```powershell
Copy-Item .env.example .env
```

| ตัวแปร | ใช้กับ | คำอธิบาย |
|---|---|---|
| `SUPABASE_URL` | เว็บ + บอท | Project URL ของ Supabase |
| `SUPABASE_ANON_KEY` | เว็บ | Publishable key (`sb_publishable_...`) — ส่งให้ browser ได้ |
| `SUPABASE_SERVICE_ROLE_KEY` | เว็บ + บอท | Secret key (`sb_secret_...`) — **ห้ามแชร์ ห้าม commit** |
| `PUBLIC_BASE_URL` | บอท | URL ของเว็บ (บอทแนบลิงก์ไป dashboard หลังบันทึก) ไม่ใส่ก็ได้ |
| `TYPHOON_API_KEY` | เว็บ + บอท | API key จาก opentyphoon.ai |
| `TYPHOON_LLM_MODEL` | เว็บ + บอท | ค่าเริ่มต้น `typhoon-v2.5-30b-a3b-instruct` |
| `DISCORD_TOKEN` | บอท | token ของบอท — **ห้ามแชร์** |
| `DISCORD_CHANNEL_ID` | บอท | ช่องที่บอทอ่านสลิป |
| `CATEGORIES` | เว็บ + บอท | หมวดรายจ่าย คั่นด้วย `,` (ไม่เกิน 25 หมวด) |

### 4) รัน

```powershell
python -m pytest -q                                                   # unit tests (ไม่ต้องมี .env)
python store.py                                                       # ทดสอบต่อ Supabase จริง (เพิ่มแถวทดสอบ discord_id=smoke)
python slip_reader.py path\to\slip.jpg                                # ทดสอบอ่านสลิปด้วย Typhoon
python -m uvicorn web.app:create_default_app --factory --reload      # เว็บ → http://127.0.0.1:8000
python bot.py                                                         # บอท Discord
```

## Deploy บน Render

1. push repo ขึ้น GitHub
2. Render → **New → Blueprint** → เลือก repo นี้ (ใช้ [render.yaml](render.yaml))
3. กรอก env vars ที่ขึ้นว่า `sync: false` (ค่าเดียวกับ `.env`, `PUBLIC_BASE_URL` = URL ที่ Render ให้)
4. เพิ่ม URL ของ Render ใน Supabase → Authentication → URL Configuration (ดูข้อ 2)

## วิธีใช้บอท Discord

1. ส่งรูปสลิปเข้าช่อง `DISCORD_CHANNEL_ID` → บอทขึ้น ⏳ แล้ว reply ยอด/วันที่/ผู้รับ
2. เลือกหมวดจากเมนู (หรือกด **แก้ไข** / **ยกเลิก**) — กดได้เฉพาะคนที่ส่งสลิป และหมดอายุใน 24 ชั่วโมง
3. อ่านยอดไม่ได้ → บอทเปิดหน้าต่างให้พิมพ์ยอดเอง
4. `!สรุป` หรือ `!สรุป 2026-10` → ยอดรวมแยกหมวดของเดือนนั้น (เฉพาะสลิปของคนที่สั่ง)

## ความปลอดภัย / ความเป็นส่วนตัว

- `.env` อยู่ใน `.gitignore` — ตรวจก่อน push ทุกครั้งว่าไม่มีคีย์หลุด
- **รูปสลิปถูกส่งไป Typhoon API** (บริษัทภายนอก) เพื่อทำ OCR
- ระบบ**ไม่ได้ตรวจสลิปปลอมกับธนาคาร** เป็นเครื่องมือจดรายจ่ายเท่านั้น

## แก้ปัญหาที่พบบ่อย

**Login แล้วเด้งกลับหน้าแรก / ขึ้น redirect error** — ตรวจ Redirect URLs ใน Supabase (ต้องมี URL ของเว็บตามด้วย `/**`) และ Redirects ในแอป Discord ต้องเป็น Callback URL ของ Supabase

**บอทไม่ตอบเมื่อส่งรูป** — เปิด Message Content Intent แล้วหรือยัง, ส่งเข้าช่องที่ตรงกับ `DISCORD_CHANNEL_ID` หรือไม่, บอทมีสิทธิ์ในช่องนั้นครบหรือไม่

**Error 429 จาก Typhoon** — ส่งถี่เกิน rate limit ฟรี รอสักพักแล้วลองใหม่

**ไม่กันบันทึกซ้ำ** — สลิปบางประเภท (เช่นจากแอปเป๋าตัง) ไม่มี QR จึงไม่มีเลขอ้างอิงให้เทียบ

**วันที่คลาดไป 543 ปี** — สลิปไทยพิมพ์ปี พ.ศ. ระบบแปลงให้แล้ว ถ้ายังผิดให้แก้วันที่ในฟอร์มก่อนบันทึก
