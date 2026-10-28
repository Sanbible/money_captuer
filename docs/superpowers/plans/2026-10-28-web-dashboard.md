# Money Captuer Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** เว็บที่ login ด้วย Discord/Google/อีเมล แล้วอัปโหลดสลิป ดูรายการและกราฟรายจ่ายของตัวเอง โดยใช้ฐานข้อมูลเดียวกับบอท Discord

**Architecture:** FastAPI (`web/`) เสิร์ฟทั้งหน้า HTML/JS และ `/api/*` บน Render ที่เดียว Supabase ทำ Auth (หน้าเว็บใช้ supabase-js)
และ Postgres; server ตรวจ token กับ Supabase Auth แล้วเข้าถึงตาราง `expenses` ผ่าน PostgREST ด้วย service-role key
โดยกรองด้วย user_id เสมอ บอทเลิกใช้ Google Sheets มาเขียนตารางเดียวกันผ่าน `store.py`

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, Supabase (Auth + Postgres/PostgREST), supabase-js 2 (CDN), Chart.js 4 (CDN), Render

**Spec:** `docs/superpowers/specs/2026-10-28-web-dashboard-design.md`

## Global Constraints

- กำหนดส่ง 2026-10-30 — ลำดับ task = ลำดับความสำคัญ; Task 1–6 = ต้องมี (URL จริงภายในวันแรก), 7–9 = วันที่สอง, 10 = ทำถ้าทัน
  (แก้ไขรายการ, เก็บรูปสลิป, import จาก Sheet ในสเปกเป็นของเสริม — ไม่อยู่ในแผนนี้ ทำหลัง Task 10 ถ้ายังมีเวลา)
- ใช้ `.venv/Scripts/python.exe` ทุกคำสั่ง (Python ระบบไม่มี dependency); เทสต์: `.venv/Scripts/python.exe -m pytest -q`
- ห้ามแก้ logic ใน `slip_reader.py` / `slip_qr.py` — เรียก `slip_qr.read_slip_qr(bytes)` และ
  `slip_reader.read_slip(bytes, api_key, model, mime)` ผ่าน `asyncio.to_thread` เท่านั้น
- `SUPABASE_SERVICE_ROLE_KEY`, `TYPHOON_API_KEY`, `DISCORD_TOKEN` ห้ามไปถึง browser / ห้ามเข้า git; browser ได้แค่ `SUPABASE_URL` + `SUPABASE_ANON_KEY`
- ทุก query ของ API ต้องกรองด้วย user_id ของผู้เรียก; รายการของคนอื่น → 404 (ไม่บอกว่ามีอยู่); สลิปซ้ำนับเฉพาะข้อมูลของคนเดียวกัน
- ข้อความที่ผู้ใช้เห็นเป็นภาษาไทย; โค้ด/คอมเมนต์ตามสไตล์ไฟล์เดิม (docstring/คอมเมนต์ไทย, `from __future__ import annotations`)
- CDN pin เวอร์ชัน: `https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js`,
  `https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js`
- หมวดหมู่มาจาก `Config.categories` (ไม่ hardcode ในหน้าเว็บ)
- เดือน = `"YYYY-MM"`, วันที่ = `"YYYY-MM-DD"` ค.ศ.; "วันนี้" = เวลาไทย (`bkk_now()`)
- commit หลังจบแต่ละ task บน `main` (repo ส่วนตัว, Render deploy จาก main); ข้อความ commit ลงท้ายด้วย
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **token หมดอายุ/ปลอม/ไม่มี** → API ตอบ 401 และหน้าเว็บพากลับหน้า login ไม่ค้างหน้าขาว — Task 3 + Task 4 (`test_requires_token`), Task 5 (`api()` จัดการ 401)
2. **อัปโหลดไฟล์ที่ไม่ใช่รูป / ใหญ่เกิน 10MB / รูปที่ไม่ใช่สลิป** → 415 / 413 / ร่างที่ amount=null ให้กรอกเอง ไม่ 500 — Task 4
3. **ผู้ใช้ A เดา id ของ B แล้วลบ** → 404 และข้อมูล B ยังอยู่ — Task 4 (`test_delete_other_users_expense_is_404`)
4. **สลิปเดียวกันส่งทั้งเว็บและบอท / รับรายการค้างที่ trans_ref ชนกับของที่มีแล้ว** → ไม่ซ้ำ, claim ข้ามแถวที่ชน — Task 2 (SQL) + Task 2 test
5. **Render หลับ (cold start ~50 วิ) / Typhoon ช้า** → หน้าเว็บแสดงสถานะ "กำลังอ่านสลิป…" และปุ่มไม่ถูกกดซ้ำ — Task 5

---

### Task 1: Config ใหม่ + helper กลาง + dependencies

**Files:**
- Create: `slip_common.py`, `tests/test_slip_common.py`, `tests/test_config.py`
- Modify: `config.py`, `bot.py:55-100,145-175` (ย้ายฟังก์ชันออก แล้ว import กลับ), `requirements.txt`, `.env.example`

**Interfaces:**
- Produces: `slip_common.BKK_TZ`, `load_bkk_tz()`, `bkk_now() -> datetime`, `parse_amount_input(str) -> float | None`,
  `normalize_slip_date(str | None) -> str | None`, `normalize_slip_time(str | None) -> str | None`,
  `normalize_ref(Any) -> str` (ย้ายจาก `sheets._normalize_ref`), `MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")`
- Produces: `Config` เพิ่มฟิลด์ `supabase_url: str`, `supabase_anon_key: str`, `supabase_service_role_key: str`, `public_base_url: str`;
  ลบ `google_sheet_id`, `google_credentials_file`; `BOT_REQUIRED = ("DISCORD_TOKEN","DISCORD_CHANNEL_ID","TYPHOON_API_KEY","SUPABASE_URL","SUPABASE_SERVICE_ROLE_KEY")`,
  `WEB_REQUIRED = ("TYPHOON_API_KEY","SUPABASE_URL","SUPABASE_ANON_KEY","SUPABASE_SERVICE_ROLE_KEY")`; `ALL_REQUIRED = BOT_REQUIRED` (คงชื่อเดิม)
- `bot.py` ยังต้อง expose ชื่อเดิม (`bot.parse_amount_input`, `bot.normalize_slip_date`, `bot.bkk_now`, `bot.load_bkk_tz` …) เพราะ `tests/test_bot_helpers.py` ใช้ — `from slip_common import ...`
  (เทสต์ `test_load_bkk_tz_falls_back_without_tzdata` monkeypatch `bot.ZoneInfo` — ย้ายเทสต์นั้นไป `test_slip_common.py` แล้ว patch `slip_common.ZoneInfo`)

- [ ] **Step 1: เขียนเทสต์** `tests/test_slip_common.py`: `normalize_ref("'ABC123 ") == "ABC123"`, `normalize_ref(None) == ""`,
  `MONTH_RE.fullmatch("2026-10")` จริง, `MONTH_RE.fullmatch("2026-13")` เป็น None, เทสต์ load_bkk_tz ที่ย้ายมา
  และใน `tests/test_config.py` (ใหม่): ตั้ง env ด้วย monkeypatch → `load_config(required=WEB_REQUIRED)` คืน `supabase_url` ถูกต้อง;
  ขาด `SUPABASE_URL` → `ConfigError`; `PUBLIC_BASE_URL` ไม่ตั้ง → `""` และตัด `/` ท้ายออกเมื่อมี
  (monkeypatch `config.load_dotenv` เป็น no-op กันอ่าน `.env` จริง)
- [ ] **Step 2: รัน** `.venv/Scripts/python.exe -m pytest tests/test_slip_common.py tests/test_config.py -q` → FAIL (ModuleNotFoundError)
- [ ] **Step 3: สร้าง `slip_common.py`** ย้ายโค้ดจาก bot.py ตรงตัว + `normalize_ref` + `MONTH_RE`; แก้ `config.py` ตาม Interfaces
- [ ] **Step 4: dependencies** — `requirements.txt`: ลบ `gspread`; เพิ่ม `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `python-multipart>=0.0.9`, `httpx>=0.27`;
  ติดตั้ง `.venv/Scripts/python.exe -m pip install -r requirements.txt`; `.env.example`: ลบ 2 บรรทัด Google, เพิ่ม `SUPABASE_URL=`, `SUPABASE_ANON_KEY=`, `SUPABASE_SERVICE_ROLE_KEY=`, `PUBLIC_BASE_URL=`
- [ ] **Step 5: รันเทสต์ทั้งหมด** → PASS (test_sheets ยังผ่านเพราะ sheets.py ยังอยู่ — ลบใน Task 8)
- [ ] **Step 6: Commit** `refactor: move shared slip helpers to slip_common, add Supabase config`

### Task 2: Schema Supabase + `store.py`

**Files:**
- Create: `supabase/schema.sql`, `store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `slip_common.normalize_ref`
- Produces (`store.py`):
  ```python
  @dataclass
  class NewExpense:
      date: str; time: str; amount: float; receiver: str; category: str; note: str; bank: str
      trans_ref: str; source: str            # "web" | "discord"
      user_id: str | None = None; discord_id: str | None = None; discord_link: str = ""

  class ExpenseStore:
      def __init__(self, supabase_url: str, service_role_key: str, *, client: httpx.Client | None = None, timeout: float = 20.0)
      def insert(self, exp: NewExpense) -> dict | None          # แถวที่บันทึก; None = trans_ref ซ้ำ (PostgREST 409 / code 23505)
      def is_duplicate(self, trans_ref: str, *, user_id: str | None = None, discord_id: str | None = None) -> bool
      def list_expenses(self, *, start: str, end: str, user_id: str | None = None, discord_id: str | None = None) -> list[dict]
          # date >= start และ date < end, เรียง date desc, time desc
      def delete(self, expense_id: str, *, user_id: str) -> bool   # False = ไม่มี/ไม่ใช่ของคนนี้
      def claim_discord_expenses(self, user_id: str, discord_id: str) -> int
  ```
  ต้องระบุ owner อย่างน้อยหนึ่งอย่างใน `is_duplicate`/`list_expenses` ไม่งั้น `ValueError` (กันดึงข้อมูลทุกคน)
  ทุก method เป็น blocking; error อื่นจาก HTTP → `httpx.HTTPStatusError`

- [ ] **Step 1: เขียน `supabase/schema.sql`** — ตาราง `expenses` ตามสเปก (คอลัมน์/ชนิดตามตารางในสเปก, `amount numeric(12,2) check (amount > 0)`,
  `source text check (source in ('web','discord'))`, `user_id uuid references auth.users on delete cascade`),
  index `(user_id, date)` และ `(discord_id, date)`, partial unique indexes:
  ```sql
  create unique index expenses_user_ref on expenses (user_id, trans_ref) where user_id is not null and trans_ref is not null;
  create unique index expenses_discord_ref on expenses (discord_id, trans_ref) where discord_id is not null and trans_ref is not null;
  alter table expenses enable row level security;   -- ไม่สร้าง policy: anon/authenticated เข้าไม่ได้
  create or replace function claim_discord_expenses(p_user uuid, p_discord text) returns integer
  language sql security definer set search_path = public as $$
    with claimed as (
      update expenses e set user_id = p_user
      where e.discord_id = p_discord and e.user_id is null
        and (e.trans_ref is null or not exists (
          select 1 from expenses x where x.user_id = p_user and x.trans_ref = e.trans_ref))
      returning 1)
    select count(*)::int from claimed $$;
  revoke execute on function claim_discord_expenses from public, anon, authenticated;
  ```
  เก็บ trans_ref ว่างเป็น `null` (ไม่ใช่ `""`) เพื่อให้ unique index ไม่ชนกัน
- [ ] **Step 2: เขียนเทสต์** `tests/test_store.py` ด้วย `httpx.MockTransport` (บันทึก request ไว้ตรวจ):
  - `test_requests_use_service_role_headers`: ทุก request มี `apikey` และ `Authorization: Bearer <key>`, URL ขึ้นต้น `<url>/rest/v1/`
  - `test_insert_returns_row`: POST `/rest/v1/expenses` มี `Prefer: return=representation`, body มี `trans_ref` ที่ normalize แล้ว; ref ว่าง → `null`
  - `test_insert_duplicate_returns_none`: transport ตอบ 409 `{"code":"23505"}` → `None`
  - `test_insert_other_error_raises`: 500 → `httpx.HTTPStatusError`
  - `test_is_duplicate_filters_by_owner`: user+discord → query `or=(user_id.eq.U,discord_id.eq."D")`; คืน True เมื่อได้ `[{"id":..}]`, False เมื่อ `[]`; ref ว่าง → False ไม่ยิง request
  - `test_owner_required`: `list_expenses(start=.., end=..)` ไม่มี owner → `ValueError`
  - `test_list_expenses_query`: params `date=gte.2026-10-01`, `date=lt.2026-11-01`, `user_id=eq.U`, `order=date.desc,time.desc`
  - `test_delete_scoped_to_user`: DELETE มี `id=eq.X` และ `user_id=eq.U`; ตอบ `[]` → False, `[{...}]` → True
  - `test_claim_calls_rpc`: POST `/rest/v1/rpc/claim_discord_expenses` body `{"p_user":U,"p_discord":D}`; ตอบ `3` → 3
- [ ] **Step 3: รัน** → FAIL
- [ ] **Step 4: implement `store.py`** ตาม Interfaces
- [ ] **Step 5: รัน** → PASS
- [ ] **Step 6: (เจ้าของ) รัน schema** — เปิด Supabase → SQL Editor → วางเนื้อหา `supabase/schema.sql` → Run
  ทดสอบจริง: สคริปต์สั้นใน `store.py` `if __name__ == "__main__":` (smoke test แบบ `sheets._smoke_test`) insert แถวทดสอบ discord_id `"smoke"` → is_duplicate True → ลบด้วย SQL
- [ ] **Step 7: Commit** `feat: Supabase schema and ExpenseStore`

### Task 3: ตรวจ token (`web/auth.py`)

**Files:**
- Create: `web/__init__.py`, `web/auth.py`, `tests/test_web_auth.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class AuthUser:
      id: str; email: str | None; discord_id: str | None; providers: tuple[str, ...]
  class AuthError(Exception): ...
  class SupabaseAuth:
      def __init__(self, supabase_url: str, anon_key: str, *, client: httpx.Client | None = None)
      def get_user(self, access_token: str) -> AuthUser   # GET <url>/auth/v1/user, headers apikey=anon + Bearer token; 401/403 → AuthError
  def discord_id_from_identities(identities: list[dict]) -> str | None
      # identity provider=="discord" → identity_data["provider_id"] หรือ ["sub"] หรือ identity["id"] (ตามลำดับ)
  ```

- [ ] **Step 1: เทสต์** (MockTransport): token ใช้ได้ → `AuthUser` ที่ `discord_id == "123"` จาก identities
  `[{"provider":"google",...},{"provider":"discord","identity_data":{"provider_id":"123"}}]`, `providers == ("google","discord")`;
  ไม่มี discord → `None`; ตอบ 401 → `AuthError`; token ว่าง → `AuthError` ไม่ยิง request
- [ ] **Step 2: รัน → FAIL, implement, รัน → PASS**
- [ ] **Step 3: Commit** `feat: verify Supabase access tokens`

### Task 4: API (`web/app.py`)

**Files:**
- Create: `web/app.py`, `web/slips.py`, `tests/test_web_api.py`

**Interfaces:**
- Consumes: `ExpenseStore`, `NewExpense` (Task 2), `SupabaseAuth`, `AuthUser`, `AuthError` (Task 3), `slip_common.*`, `Config` (Task 1)
- Produces:
  - `web/slips.py`: `SlipDraft` dataclass `{amount: float|None, date: str|None, time: str|None, receiver: str|None, bank: str, trans_ref: str|None, error: str|None}`;
    `read_draft(data: bytes, mime: str, api_key: str, model: str) -> SlipDraft` (blocking: `read_slip_qr` → `read_slip`; ตรวจ amount แบบ `SlipState.from_results`: ไม่ใช่จำนวนบวก → None; ใช้ `normalize_slip_date/time`, `slip_qr.bank_name`)
  - `web/app.py`: `create_app(cfg: Config, *, store, auth, reader=read_draft) -> FastAPI` และ
    `create_default_app() -> FastAPI` (โหลด `load_config(required=WEB_REQUIRED)` แล้วสร้าง `ExpenseStore`/`SupabaseAuth` จริง) —
    ไม่มี module-level `app` เพื่อให้ import ในเทสต์ได้โดยไม่ต้องมี `.env`; start ด้วย `uvicorn web.app:create_default_app --factory`
  - Dependency `current_user` อ่าน `Authorization: Bearer` → `auth.get_user` (ผ่าน `asyncio.to_thread`) → ถ้ามี `discord_id` เรียก
    `store.claim_discord_expenses` (error ให้ log แล้วไปต่อ); ไม่มี/ผิด → 401 `{"detail":"กรุณาเข้าสู่ระบบ"}`
  - endpoints ตามตารางในสเปก; JSON ของรายการ = แถวจาก store ตัดเหลือ `id,date,time,amount,receiver,category,note,bank,trans_ref,source,created_at`
  - `GET /api/summary?month=` → `{"month","total","by_category":[{"category","total"}] (มาก→น้อย),"monthly":[{"month","total"}] (6 เดือนเก่า→ใหม่ จบที่ month, เดือนว่าง = 0)}`
  - `month` ไม่ส่ง = เดือนปัจจุบันเวลาไทย; ผิดรูปแบบ (`MONTH_RE`) → 422
  - `POST /api/expenses` body (pydantic): `amount` (>0, ≤ 10,000,000), `category` (ต้องอยู่ใน `cfg.categories` → ไม่งั้น 422),
    `date` (optional, ต้องผ่าน `normalize_slip_date`), `time`, `receiver`, `note` (≤ 500 ตัวอักษร), `bank`, `trans_ref` (optional);
    บันทึก `source="web"`, `user_id=me.id`, `discord_id=me.discord_id`; `store.insert` คืน None → 409 `{"detail":"สลิปนี้บันทึกไปแล้ว"}`; สำเร็จ → 201 + แถว
  - `POST /api/slips/read`: multipart field `file`; content-type ไม่ใช่ png/jpeg → 415; > 10 MB (`MAX_UPLOAD_BYTES = 10 * 1024 * 1024`) → 413;
    ส่งผล `reader` (ผ่าน `asyncio.to_thread`, จำกัดทีละ 1 ด้วย `asyncio.Semaphore(1)` เหมือนบอท) + `"duplicate": store.is_duplicate(...)` เมื่อมี trans_ref
  - `DELETE /api/expenses/{expense_id}` — `expense_id: UUID`; `store.delete` False → 404
  - `GET /api/config` → `{"supabase_url","supabase_anon_key","categories"}` (ไม่ต้อง login); `GET /health` → `{"ok": true}`
  - หน้า: `GET /` → `web/static/index.html`, `/dashboard` → `dashboard.html`, `/settings` → `settings.html`; mount `/static`

- [ ] **Step 1: เทสต์** `tests/test_web_api.py` ใช้ `fastapi.testclient.TestClient` + `FakeStore` (เก็บแถวใน list, ใช้กติกา unique เดียวกับ SQL) +
  `FakeAuth` (token `"tok-a"` → user A มี discord `"111"`, `"tok-b"` → user B ไม่มี discord) + fake reader:
  - `test_requires_token`: ทุก `/api/*` ยกเว้น `/api/config` ไม่มี/ผิด token → 401
  - `test_config_is_public_and_has_no_secrets`: ไม่มี service role key / typhoon key อยู่ใน response text
  - `test_read_slip_returns_draft_and_duplicate_flag`, `test_read_slip_rejects_non_image` (415), `test_read_slip_rejects_large_file` (413)
  - `test_read_slip_unreadable_amount_returns_null_amount` (reader คืน amount None → 200, `amount` null)
  - `test_create_expense_and_list_month`, `test_create_expense_defaults_date_to_today_bkk`
  - `test_create_expense_invalid_category_422`, `test_create_expense_non_positive_amount_422`, `test_create_expense_bad_date_422`
  - `test_create_duplicate_409` และ `test_same_ref_different_users_both_ok`
  - `test_list_only_own_expenses`, `test_delete_other_users_expense_is_404` (แถว B ยังอยู่), `test_delete_own_expense_204` (ลบสำเร็จ → 204 ไม่มี body)
  - `test_summary_totals_and_six_months` (มีข้อมูล ส.ค. และ ต.ค. → monthly 6 รายการ, ก.ย. = 0, by_category เรียงมาก→น้อย)
  - `test_bad_month_422`
  - `test_claim_called_for_discord_user` (A เรียก API → FakeStore.claim ถูกเรียกด้วย (A.id,"111"); B ไม่ถูกเรียก)
  - `test_pages_served`: `/`, `/dashboard`, `/settings` → 200 text/html; `/health` → `{"ok": true}`
- [ ] **Step 2: รัน → FAIL**
- [ ] **Step 3: implement `web/slips.py` และ `web/app.py`** (สร้างไฟล์ HTML เปล่า 3 ไฟล์ให้ test_pages ผ่าน — เนื้อหาจริงใน Task 5)
- [ ] **Step 4: รัน → PASS** และรันเทสต์ทั้งหมด
- [ ] **Step 5: ทดสอบมือในเครื่อง** `.venv/Scripts/python.exe -m uvicorn web.app:create_default_app --factory --reload` → เปิด `http://127.0.0.1:8000/health`
- [ ] **Step 6: Commit** `feat: FastAPI expenses API with slip upload`

### Task 5: หน้าเว็บ — login Discord, dashboard (ตาราง + อัปโหลด)

**Files:**
- Create/Modify: `web/static/index.html`, `web/static/dashboard.html`, `web/static/settings.html`, `web/static/app.js`, `web/static/style.css`

**Interfaces:**
- Consumes: API ตาม Task 4
- Produces (`app.js`, global `MC`): `MC.init() -> Promise<{supabase, config}>` (โหลด `/api/config` แล้ว `supabase.createClient`),
  `MC.requireSession() -> Promise<session>` (ไม่มี → `location = "/"`), `MC.api(path, options) -> Promise<any>` (แนบ Bearer, 401 → signOut + กลับ `/`,
  error อื่น → throw `Error(detail)`), `MC.baht(n) -> "฿1,234.50"`

- [ ] **Step 1: `index.html`** — ชื่อเว็บ, คำอธิบาย 1–2 บรรทัด (อัปโหลดสลิป → บันทึกรายจ่าย → ดูกราฟ), ปุ่ม "เข้าสู่ระบบด้วย Discord"
  (`signInWithOAuth({provider:"discord", options:{redirectTo: location.origin + "/dashboard"}})`); มี session อยู่แล้ว → ไป `/dashboard`
- [ ] **Step 2: `dashboard.html`** — header (ชื่อ/อีเมลผู้ใช้, ลิงก์ตั้งค่า, ออกจากระบบ), ตัวเลือกเดือน (`<input type="month">`), ยอดรวม, ปุ่ม "อัปโหลดสลิป",
  ตารางรายการ (วันที่, เวลา, ผู้รับ, หมวด, จำนวนเงิน, ที่มา เว็บ/Discord, ปุ่มลบพร้อม confirm), สถานะว่าง "ยังไม่มีรายการเดือนนี้ — อัปโหลดสลิปแรกได้เลย",
  กล่องสำหรับกราฟ 2 อัน (เติมใน Task 7)
- [ ] **Step 3: การอัปโหลด (dialog)** — เลือกไฟล์ (`accept="image/png,image/jpeg"`, กล้องมือถือได้) → ปุ่มปิดใช้ + "กำลังอ่านสลิป… อาจใช้เวลาสักครู่" →
  ฟอร์มเติมค่า (ยอด, วันที่, เวลา, ผู้รับ, โน้ต, select หมวดจาก config) → อ่านยอดไม่ได้ แสดงข้อความให้กรอกเอง; `duplicate` → เตือนและปิดปุ่มบันทึก →
  บันทึก (ปุ่มปิดระหว่างส่ง) → 409 แสดงข้อความ → สำเร็จ ปิด dialog แล้วโหลดเดือนนั้นใหม่
- [ ] **Step 4: `style.css`** — ใช้บนมือถือได้ (กว้าง 360px ไม่มี scroll แนวนอน), ตารางเลื่อนแนวนอนได้เฉพาะในกล่องตัวเอง, ฟอนต์ไทยอ่านง่าย (`"Noto Sans Thai", system-ui`)
- [ ] **Step 5: ทดสอบมือในเครื่อง** — ต้องทำ Task 6 Step 1–3 (ตั้งค่า Discord provider ใน Supabase, redirect `http://127.0.0.1:8000/**`) ก่อน:
  login Discord → อัปโหลดสลิปจริง 1 ใบ → เห็นในตาราง → อัปโหลดซ้ำ → เตือนซ้ำ → ลบได้ → logout แล้วเปิด `/dashboard` ตรง ๆ → กลับหน้า login
- [ ] **Step 6: Commit** `feat: login and dashboard pages`

### Task 6: Deploy ขึ้น Render + ตั้งค่า OAuth

**Files:**
- Create: `render.yaml`, `.python-version` (`3.12`)
- Modify: `README.md` (ส่วนบนสุด: Live URL, GitHub, Tech Stack, ภาพรวมสถาปัตยกรรม — คงเนื้อหาบอทไว้ด้านล่างโดยตัดส่วน Google Sheets ออก)

- [ ] **Step 1: (เจ้าของ, agent พาทำ) Discord OAuth** — Discord Developer Portal → แอปของบอท → OAuth2 → คัดลอก Client ID/Secret →
  Supabase → Authentication → Providers → Discord → เปิดและวางค่า → คัดลอก Callback URL ของ Supabase ไปใส่ Redirects ใน Discord
- [ ] **Step 2: `render.yaml`**: web service, `runtime: python`, build `pip install -r requirements.txt`,
  start `uvicorn web.app:create_default_app --factory --host 0.0.0.0 --port $PORT`, `healthCheckPath: /health`, `plan: free`,
  envVars (sync: false): `TYPHOON_API_KEY`, `TYPHOON_LLM_MODEL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `PUBLIC_BASE_URL`, `CATEGORIES`
- [ ] **Step 3: Commit + push** `chore: Render deployment config` (`git push origin main` — remote เป็นบัญชี Sanbible แล้ว)
- [ ] **Step 4: (เจ้าของ) Render** → New → Blueprint → เลือก repo → ใส่ env vars → Deploy → ได้ URL `https://<name>.onrender.com`
- [ ] **Step 5: (เจ้าของ) Supabase → Authentication → URL Configuration**: Site URL = URL Render, Redirect URLs เพิ่ม `https://<name>.onrender.com/**` และ `http://127.0.0.1:8000/**`
- [ ] **Step 6: ตรวจบน URL จริง**: `/health` ok, login Discord, อัปโหลดสลิป, ลบ — ทั้งหมดผ่าน
- [ ] **Step 7: README** ใส่ Live URL + Tech Stack (FastAPI, Supabase Auth/Postgres, Chart.js, Typhoon OCR/LLM, OpenCV, discord.py, Render) → commit + push `docs: live URL and tech stack`

### Task 7: กราฟใน dashboard

**Files:**
- Modify: `web/static/dashboard.html`, `web/static/app.js` (หรือ `web/static/charts.js` ใหม่)

**Interfaces:**
- Consumes: `GET /api/summary?month=` (Task 4)

- [ ] **Step 1:** โดนัทตามหมวด (`by_category`) + กราฟแท่ง 6 เดือน (`monthly`), tooltip แสดง `MC.baht`, สีหมวดคงที่ต่อชื่อหมวด (ไล่ index ใน `config.categories`)
- [ ] **Step 2:** เปลี่ยนเดือน / บันทึก / ลบ → โหลด summary ใหม่และ `chart.destroy()` ก่อนสร้างใหม่ (กันกราฟซ้อน); เดือนไม่มีข้อมูล → ซ่อนโดนัท แสดงสถานะว่าง
- [ ] **Step 3:** ทดสอบมือ (ในเครื่องและบน Render หลัง push) — ตัวเลขกราฟตรงกับยอดรวมตาราง
- [ ] **Step 4: Commit + push** `feat: expense charts`

### Task 8: บอทเขียนลง Supabase, เลิกใช้ Google Sheets

**Files:**
- Modify: `bot.py`, `tests/test_bot_helpers.py`, `README.md` (ส่วนตั้งค่าบอท)
- Delete: `sheets.py`, `tests/test_sheets.py`

**Interfaces:**
- Consumes: `ExpenseStore`, `NewExpense` (Task 2), `slip_common.MONTH_RE`
- Produces: `build_record(state: SlipState, category: str, now: datetime) -> store.NewExpense` (`source="discord"`, `discord_id=str(state.author_id)`,
  `user_id=None`, `discord_link=state.slip_url`); `summarize_by_category(rows: list[dict]) -> dict[str, float]` (pure, มาก→น้อย, หมวดว่าง → `"ไม่ระบุหมวด"`, ปัด 2 ตำแหน่ง)

- [ ] **Step 1: แก้เทสต์** `tests/test_bot_helpers.py`: `build_record` คืน `NewExpense` ที่ `discord_id == "42"`, `source == "discord"`, `user_id is None`,
  ไม่มีฟิลด์ `recorded_at`; เพิ่มเทสต์ `summarize_by_category` (ยอดรวมถูก, เรียงลำดับ, หมวดว่าง)
- [ ] **Step 2: รัน → FAIL**
- [ ] **Step 3: แก้ `bot.py`** — `SlipBot(cfg, store)`; `is_duplicate(trans_ref, discord_id)`; `save_expense(record) -> bool` (False = ซ้ำ);
  `saved_embed` แทนฟิลด์ "แท็บ" ด้วย "ดูในเว็บ" = `cfg.public_base_url + "/dashboard"` (ถ้า public_base_url ว่างไม่ต้องใส่);
  `handle_summary` ใช้ `store.list_expenses(start, end, discord_id=str(message.author.id))` + `summarize_by_category`;
  ข้อความ error "Google Sheets" → "ฐานข้อมูล"; `SHEETS_TIMEOUT` → `DB_TIMEOUT`; `main()` ใช้ `load_config(required=BOT_REQUIRED)` และสร้าง `ExpenseStore`
  (ลบการเช็กไฟล์ credentials); docstring บนสุดอัปเดต
- [ ] **Step 4: ลบ** `sheets.py`, `tests/test_sheets.py`; `git grep -n "sheets\|gspread"` ต้องไม่เจอในโค้ด (README เจอได้เฉพาะถ้ายังไม่แก้ — แก้ด้วย)
- [ ] **Step 5: รันเทสต์ทั้งหมด → PASS**
- [ ] **Step 6: ทดสอบมือ** `python bot.py` → ส่งสลิปใน Discord → บันทึก → `!สรุป` ถูกต้อง → login เว็บด้วย Discord บัญชีเดียวกัน → รายการจากบอทขึ้นในเว็บ (claim ทำงาน) ที่มา = Discord
- [ ] **Step 7: Commit + push** `feat: bot saves to Supabase instead of Google Sheets`

### Task 9: ตรวจรอบสุดท้ายก่อนส่ง (ต้องมี)

- [ ] **Step 1:** login สองบัญชี Discord (หรือขอเพื่อน) บน URL จริง → แต่ละคนไม่เห็นรายการของอีกคน
- [ ] **Step 2:** เปิดบนมือถือ → login, อัปโหลดจากกล้อง, กราฟแสดงผล
- [ ] **Step 3:** README: Live URL, GitHub link, Tech Stack, วิธีรันในเครื่อง, หมายเหตุ cold start ของ Render
- [ ] **Step 4:** `git grep -nE "eyJ|sk-|SERVICE_ROLE_KEY="` ไม่เจอคีย์จริง; push

### Task 10 (ทำถ้าทัน): Login Google + อีเมล และเชื่อม Discord

**Files:**
- Modify: `web/static/index.html`, `web/static/settings.html`, `web/static/app.js`

- [ ] **Step 1: (เจ้าของ, agent พาทำ)** Google Cloud → OAuth client (Web) → redirect = Callback URL ของ Supabase → Supabase → Providers → Google;
  Supabase → Authentication → เปิด **Manual Linking**; Email provider เปิด (magic link)
- [ ] **Step 2: `index.html`** — ปุ่ม "เข้าสู่ระบบด้วย Google" และช่องอีเมล + ปุ่ม "ส่งลิงก์เข้าสู่ระบบ"
  (`signInWithOtp({email, options:{emailRedirectTo: location.origin + "/dashboard"}})` → แสดง "เช็กอีเมลของคุณ"; error rate limit → แสดงข้อความให้ลองใหม่ภายหลัง)
- [ ] **Step 3: `settings.html`** — แสดงวิธี login ที่ผูกแล้ว (`supabase.auth.getUserIdentities()`), ถ้ายังไม่มี discord → ปุ่ม "เชื่อมบัญชี Discord"
  (`linkIdentity({provider:"discord", options:{redirectTo: location.origin + "/settings"}})`), แสดงคำอธิบายว่าเชื่อมแล้วสลิปจากบอทจะเข้าบัญชีนี้; ปุ่ม logout
- [ ] **Step 4: ทดสอบมือ** — login Google → อัปโหลด → เชื่อม Discord → รายการจากบอทขึ้นในบัญชี Google นี้ (claim ทำงานใน request ถัดไป)
- [ ] **Step 5: Commit + push** `feat: Google and email login, link Discord account`
