# Money Capture

เว็บจดรายจ่ายจากสลิปโอนเงินของธนาคารไทย — อัปโหลดรูปสลิป ระบบอ่านยอดเงิน วันที่ และผู้รับให้อัตโนมัติ
แล้วสรุปเป็นกราฟรายเดือน และมี **ปาร์ตี้หารค่าใช้จ่าย** สำหรับทริป/มื้ออาหารกับเพื่อน แต่ละคน login แล้วเห็นเฉพาะข้อมูลของตัวเอง

- **Live URL:** https://money-capture.onrender.com
- **GitHub:** https://github.com/Sanbible/money_capture

> เว็บใช้ Render แบบฟรี ถ้าไม่มีคนเข้าเกิน 15 นาที เซิร์ฟเวอร์จะหลับ การเปิดครั้งแรกอาจช้าประมาณ 30–50 วินาที

## Tech Stack

| ส่วน | เทคโนโลยี |
|---|---|
| Backend / API | Python 3.12, FastAPI, Uvicorn |
| หน้าเว็บ | HTML + CSS + JavaScript (ไม่ใช้ framework) ออกแบบสำหรับมือถือ ธีม Cashly — กราฟวาดด้วย SVG/HTML เอง |
| Login | Supabase Auth (Discord, Google, อีเมล) ผ่าน supabase-js |
| ฐานข้อมูล | Supabase Postgres (PostgREST + SQL functions) เปิด Row Level Security |
| อ่านสลิป | OpenCV (อ่าน QR), Gemini Flash-Lite อ่านรูปเป็น JSON (ตัวหลัก ~2.5 วิ) → สำรองด้วย Typhoon OCR + Typhoon LLM |
| Deploy | Render (web service), GitHub |
| Test | pytest, FastAPI TestClient, httpx MockTransport |

## ทำอะไรได้บ้าง

**รายจ่ายส่วนตัว**
- **Login** ด้วย Discord / Google / อีเมล — ไม่ต้องจำรหัสผ่าน
- **อัปโหลดสลิป** (PNG/JPEG) → อ่าน QR เพื่อได้เลขอ้างอิงธุรกรรม + OCR อ่านยอด → ตรวจ/แก้ → เลือกหมวด → บันทึก
- **กันบันทึกซ้ำ** ด้วยเลขอ้างอิงจาก QR (นับเฉพาะข้อมูลของคนเดียวกัน)
- **Dashboard** — ยอดรวมของเดือน, โดนัทแยกตามหมวด, กราฟย้อนหลัง 6 เดือน, รายการ (ลบได้)

**ปาร์ตี้หารค่าใช้จ่าย** (หน้า "ปาร์ตี้")
- สร้างปาร์ตี้ แล้วชวนเพื่อนด้วย **ลิงก์เชิญ** หรือ **โค้ด 6 ตัว** (สูงสุด 10 คนรวม host)
- ใครจ่ายอะไรก็อัปสลิปหรือใส่ยอดเงินสด แล้วเลือก **คนจ่าย** และ **ผู้มีส่วน** — ทุกคนในปาร์ตี้เห็นทุกรายการ
- ดูยอดสุทธิของแต่ละคนได้ตลอด แก้/ลบรายการได้ (คนเพิ่มรายการ หรือ host)
- host ปิดปาร์ตี้ → ระบบสรุปว่า **ใครต้องโอนให้ใครเท่าไหร่ โดยโอนน้อยครั้งที่สุด**
- คนโอน **แนบสลิปการโอน** → server อ่านยอดด้วย OCR เอง ยอดต้องตรงเป๊ะถึงขึ้น "ตรวจสลิปแล้ว" (ไม่ตรง = ไม่ผ่าน แนบใบใหม่;
  สลิปใบเดียวใช้ยืนยันได้รายการเดียว) ; จ่ายเงินสดให้คนรับกด "ได้รับเงินแล้ว" — ยืนยันครบทุกรายการ = เคลียร์
- เงินคำนวณเป็นสตางค์ทั้งหมด หารไม่ลงตัวแจกเศษทีละสตางค์ (100 บาท 3 คน → 33.34 / 33.33 / 33.33)
- สลิปเดียวกัน (เลขอ้างอิงหรือรูปเดียวกัน) บันทึกในปาร์ตี้ซ้ำไม่ได้; ปิดแล้วเพิ่ม/แก้ไม่ได้ — กฎเหล่านี้บังคับในฐานข้อมูล
  (SQL functions ล็อกแถว) จึงถูกต้องแม้หลายคนกดพร้อมกัน

## สถาปัตยกรรม

```
Browser ──(supabase-js: login)──▶ Supabase Auth
   │  Authorization: Bearer <access token>
   ▼
FastAPI (Render) ──▶ slip_qr / slip_reader ──▶ Gemini API (ตัวหลัก) / Typhoon API (สำรอง)
   │  ตรวจ token กับ Supabase Auth แล้วตรวจสิทธิ์ทุก request (เจ้าของรายการ / สมาชิกปาร์ตี้)
   ▼
Supabase Postgres: expenses (รายจ่ายส่วนตัว) · parties / party_* (ปาร์ตี้ + SQL functions)
```

- browser ได้แค่ Supabase URL + publishable key; **service-role key อยู่ฝั่ง server เท่านั้น**
- ทุกตารางเปิด RLS โดยไม่มี policy ให้ browser — อ่านเขียนได้ผ่าน API ของเว็บเท่านั้น
- คำนวณเงินของปาร์ตี้อยู่ใน `party_math.py` (pure function มี unit test รวม property test)

## โครงสร้างโปรเจกต์

```
money_capture/
├── web/
│   ├── app.py           # FastAPI: หน้าเว็บ + /api/* รายจ่ายส่วนตัว
│   ├── parties.py       # /api/parties/* ปาร์ตี้หารค่าใช้จ่าย
│   ├── auth.py          # ตรวจ access token กับ Supabase Auth
│   ├── slips.py         # อ่านสลิปที่อัปโหลด (ใช้ slip_qr + slip_reader)
│   └── static/          # หน้าเว็บ (dashboard, ปาร์ตี้, ตั้งค่า) + JS + CSS
├── store.py             # ตาราง expenses ผ่าน PostgREST
├── party_store.py       # ตารางปาร์ตี้ + เรียก SQL functions
├── party_math.py        # หาร / ยอดสุทธิ / รายการโอน (สตางค์)
├── slip_qr.py           # อ่าน QR + แปลง EMVCo TLV
├── slip_reader.py       # Gemini → Typhoon OCR + Typhoon LLM → regex สำรอง
├── slip_common.py       # helper กลาง (วันที่ เวลา ยอดเงิน เขตเวลาไทย ชนิดรูป)
├── config.py            # โหลดค่าตั้งค่าจาก .env
├── supabase/
│   ├── schema.sql       # ตาราง expenses + RLS
│   └── party_schema.sql # ตารางปาร์ตี้ + SQL functions (ล็อกแถว กันกดพร้อมกัน)
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

### 2) เตรียม Supabase

1. สร้างโปรเจกต์ที่ https://supabase.com
2. **SQL Editor → New query** → วาง [supabase/schema.sql](supabase/schema.sql) → **Run** แล้วทำซ้ำกับ
   [supabase/party_schema.sql](supabase/party_schema.sql)
3. **Project Settings → API Keys** → คัดลอก Project URL, Publishable key และ Secret key
4. **Authentication → Sign In / Providers** → เปิด Discord / Google / Email ตามต้องการ
   (Discord/Google ต้องใส่ Client ID / Secret และนำ **Callback URL** ของ Supabase ไปใส่ในแอปฝั่งนั้น)
5. **Authentication → URL Configuration** → Site URL = URL ของเว็บ, Redirect URLs เพิ่ม `https://<เว็บ>/**` และ `http://127.0.0.1:8000/**`

**Typhoon (อ่านสลิป)** — สมัครที่ https://opentyphoon.ai แล้วสร้าง API key (แผนฟรี 20 request/นาที)

### 3) ตั้งค่า `.env`

```powershell
Copy-Item .env.example .env
```

| ตัวแปร | คำอธิบาย |
|---|---|
| `SUPABASE_URL` | Project URL ของ Supabase |
| `SUPABASE_ANON_KEY` | Publishable key (`sb_publishable_...`) — ส่งให้ browser ได้ |
| `SUPABASE_SERVICE_ROLE_KEY` | Secret key (`sb_secret_...`) — **ห้ามแชร์ ห้าม commit** |
| `PUBLIC_BASE_URL` | URL ของเว็บ (ใช้สร้างลิงก์เชิญปาร์ตี้) ไม่ใส่ก็ได้ |
| `TYPHOON_API_KEY` | API key จาก opentyphoon.ai |
| `TYPHOON_LLM_MODEL` | ค่าเริ่มต้น `typhoon-v2.5-30b-a3b-instruct` |
| `GEMINI_API_KEY` | ไม่บังคับ — key ฟรีจาก Google AI Studio ; ใส่แล้วอ่านสลิปด้วย Gemini ก่อน (Typhoon เป็นสำรอง) |
| `GEMINI_MODEL` | ค่าเริ่มต้น `gemini-flash-lite-latest` |
| `CATEGORIES` | หมวดรายจ่าย คั่นด้วย `,` (ไม่เกิน 25 หมวด) |

### 4) รัน

```powershell
python -m pytest -q                                                   # unit tests (ไม่ต้องมี .env)
python store.py                                                       # ทดสอบตาราง expenses กับ Supabase จริง
python party_store.py                                                 # ทดสอบ SQL functions ของปาร์ตี้กับ Supabase จริง
python slip_reader.py path\to\slip.jpg                                # ทดสอบอ่านสลิปด้วย Typhoon
python -m uvicorn web.app:create_default_app --factory --reload      # เว็บ → http://127.0.0.1:8000
```

## Deploy บน Render

1. push repo ขึ้น GitHub
2. Render → **New → Blueprint** → เลือก repo นี้ (ใช้ [render.yaml](render.yaml))
3. กรอก env vars ที่ขึ้นว่า `sync: false` (ค่าเดียวกับ `.env`, `PUBLIC_BASE_URL` = URL ที่ Render ให้)
4. เพิ่ม URL ของ Render ใน Supabase → Authentication → URL Configuration (ดูข้อ 2)

## ความปลอดภัย / ความเป็นส่วนตัว

- `.env` อยู่ใน `.gitignore` — ตรวจก่อน push ทุกครั้งว่าไม่มีคีย์หลุด
- **รูปสลิปถูกส่งไป Gemini API และ/หรือ Typhoon API** (บริษัทภายนอก) เพื่ออ่านข้อมูล; ระบบไม่เก็บไฟล์รูป
  (Gemini แบบฟรี: Google อาจใช้ข้อมูลที่ส่งไปปรับปรุงผลิตภัณฑ์ — ไม่ตั้ง `GEMINI_API_KEY` = ใช้ Typhoon อย่างเดียว)
- ระบบ**ไม่ได้ตรวจสลิปปลอมกับธนาคาร** และปุ่ม "โอนแล้ว" เชื่อตามที่สมาชิกกด
- ปาร์ตี้ที่ไม่ได้เป็นสมาชิก เปิดดูไม่ได้ (API ตอบเหมือนไม่มีปาร์ตี้นั้น)

## แก้ปัญหาที่พบบ่อย

**Login แล้วเด้งกลับหน้าแรก / ขึ้น redirect error** — ตรวจ Redirect URLs ใน Supabase (ต้องมี URL ของเว็บตามด้วย `/**`) และ Redirects ในแอป Discord/Google ต้องเป็น Callback URL ของ Supabase

**Error 429 จาก Typhoon** — ส่งถี่เกิน rate limit ฟรี รอสักพักแล้วลองใหม่

**ไม่กันบันทึกซ้ำ** — สลิปบางประเภท (เช่นจากแอปเป๋าตัง) ไม่มี QR จึงไม่มีเลขอ้างอิงให้เทียบ (ในปาร์ตี้ยังกันรูปเดียวกันซ้ำได้)

**วันที่คลาดไป 543 ปี** — สลิปไทยพิมพ์ปี พ.ศ. ระบบแปลงให้แล้ว ถ้ายังผิดให้แก้วันที่ในฟอร์มก่อนบันทึก
