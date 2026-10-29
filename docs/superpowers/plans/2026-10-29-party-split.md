# Party Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ระบบปาร์ตี้หารค่าใช้จ่ายในบอท Discord — สร้าง/เข้าร่วมผ่าน private thread, อัปสลิปหรือเงินสด, เลือกคนจ่าย/ผู้มีส่วน, ปิดแล้วสรุปการโอนที่น้อยครั้งที่สุดพร้อมปุ่ม [โอนแล้ว]

**Architecture:** `party_math.py` (pure, สตางค์ int) คำนวณทั้งหมด; `party_store.py` คุยกับ Supabase ผ่าน PostgREST + SQL functions atomic ใน `supabase/party_schema.sql`;
`party_bot.py` มี slash commands `/party` และปุ่ม/เมนูแบบ `DynamicItem` ที่อ่าน state จาก DB ทุกครั้ง; `bot.py` แค่ลงทะเบียนและส่งข้อความใน thread ปาร์ตี้ไปให้

**Tech Stack:** Python 3.12, discord.py 2.7 (`app_commands`, `DynamicItem`), httpx, Supabase Postgres/PostgREST, pytest

**Spec:** `docs/superpowers/specs/2026-10-29-party-split-design.md`

## Global Constraints

- branch `party-group` — ห้าม merge เข้า `main` ในแผนนี้; ห้ามแก้ `web/`, `store.py`, ตาราง `expenses`
- ใช้ `.venv/Scripts/python.exe` ทุกคำสั่ง; เทสต์ทั้งหมด: `.venv/Scripts/python.exe -m pytest -q` ต้องผ่านทุก task (flow ส่วนตัวเดิมห้ามพัง)
- เงินเป็น `int` สตางค์ทุกที่ ห้าม float ในการคำนวณ; แสดงผล `"1,234.50"` บาท
- สมาชิกรวม host ≤ 10 (`MAX_MEMBERS = 10`); โค้ดเชิญ 6 ตัวจาก `ABCDEFGHJKMNPQRSTUVWXYZ23456789`
- สถานะปาร์ตี้ `open` → `closed` → `settled`; รายการ `draft` / `confirmed`; draft หมดอายุ 24 ชม. (`DRAFT_TTL = timedelta(hours=24)`)
- สลิปซ้ำ (trans_ref หรือ sha256 ของรูป ในปาร์ตี้เดียวกัน, เทียบกับรายการ confirmed) **บันทึกไม่ได้**
- custom_id รูปแบบ `party:<action>:<uuid>`; ทุกปุ่ม/เมนูเป็น `DynamicItem` ลงทะเบียนใน `setup_hook`
- ข้อความผู้ใช้ภาษาไทย; error/ฟอร์ม = ephemeral; สรุปรายการ, `/party status`, สรุปตอนปิด = โพสต์ใน thread (ทุกคนในปาร์ตี้เห็น)
- Supabase key `sb_secret_` ส่งใน header `apikey` เท่านั้น (เหมือน `store.ExpenseStore`)
- โค้ด/คอมเมนต์ตามสไตล์เดิม (`from __future__ import annotations`, docstring ไทย); commit ลงท้าย `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **บอทรีสตาร์ทระหว่างทริป** — ปุ่มเก่าทุกตัว (เข้าร่วม, ตรวจ/ยืนยัน, เมนูคนจ่าย, โอนแล้ว) ต้องยังทำงาน → Task 3 `test_custom_ids_round_trip` + ทดสอบมือ Task 7
2. **สมาชิกถูกเตะ/ออกระหว่างมี draft ที่มีชื่อเขา** — ฟอร์มตัดชื่อออก, ถ้าคนจ่ายถูกตัด คืนเป็นผู้อัป → Task 4 `test_sanitize_draft_drops_non_members`
3. **OCR อ่านยอดไม่ได้** — ฟอร์มแสดงคำเตือนและปุ่มยืนยันกดไม่ได้จนกว่าจะกรอกยอด → Task 4 `test_form_state_requires_amount`
4. **ยอดที่พิมพ์ผิดรูปแบบ** (`abc`, `0`, `-5`, `1,234.567`, `1e3`) ใน `/party add` และ modal แก้ยอด → ข้อความ ephemeral ไม่ crash → Task 1 `test_parse_baht_rejects`
5. **กด [โอนแล้ว] รายการสุดท้ายพร้อมกันสองคน** — settled ครั้งเดียว ประกาศครั้งเดียว → Task 2 smoke test + `party_toggle_paid` คืน `settled` เฉพาะครั้งที่เปลี่ยนสถานะ

---

### Task 1: `party_math.py` — หาร, ยอดสุทธิ, รายการโอน

**Files:**
- Create: `party_math.py`, `tests/test_party_math.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Expense: payer: str; amount: int; participants: tuple[str, ...]
  @dataclass(frozen=True)
  class Balance: paid: int; owed: int; net: int
  @dataclass(frozen=True)
  class Transfer: from_id: str; to_id: str; amount: int
  def split(amount: int, participants: Sequence[str]) -> dict[str, int]
  def balances(members: Sequence[str], expenses: Sequence[Expense]) -> dict[str, Balance]
  def settle(net: Mapping[str, int]) -> list[Transfer]
  def format_satang(satang: int) -> str          # 123450 -> "1,234.50"
  def parse_baht(text: str) -> int | None        # "1,234.5" / "฿99" / "99 บาท" -> สตางค์; ผิด/≤0 -> None
  ```
  - `split`: เรียงผู้มีส่วนด้วย `int(id)`; ทุกคนได้ `amount // n` แล้วแจกเศษ 1 สตางค์ให้คนแรก ๆ ตามลำดับ; participants ว่าง/ซ้ำ หรือ amount ≤ 0 → `ValueError`
  - `balances`: คีย์ = members ∪ ทุก id ที่โผล่ในรายการ; `assert sum(net) == 0`
  - `settle`: greedy ลูกหนี้ติดลบมากสุด → เจ้าหนี้บวกมากสุด จำนวน `min(...)`; เสมอกันเลือก `int(id)` น้อยกว่า; ข้าม net 0
  - `parse_baht`: ใช้ `Decimal` (ไม่ผ่าน float), ทศนิยม ≤ 2 ตำแหน่ง, คอมมาคั่นหลักพันถูกรูปแบบ (กติกาเดียวกับ `slip_common.parse_amount_input`), ≤ 10,000,000 บาท

- [ ] **Step 1: เขียนเทสต์** (id เป็นสตริงตัวเลข: A="1", B="2", C="3", D="4")
  ```python
  def test_spec_example():
      exps = [Expense("1", 90000, ("1","2","3")), Expense("2", 30000, ("1","2","3"))]
      b = balances(["1","2","3"], exps)
      assert {k: v.net for k, v in b.items()} == {"1": 50000, "2": -10000, "3": -40000}
      assert settle({k: v.net for k, v in b.items()}) == [Transfer("3","1",40000), Transfer("2","1",10000)]
  def test_split_uneven(): assert split(10000, ["3","1","2"]) == {"1": 3334, "2": 3333, "3": 3333}
  def test_payer_not_participant():  # A จ่าย 300 ให้ B,C -> A +300, B -150, C -150
  def test_member_without_share_is_zero_and_not_in_transfers():  # D ไม่มีส่วน -> Balance(0,0,0), ไม่อยู่ใน settle()
  def test_everyone_paid_equal_no_transfers():
  def test_split_rejects_empty_duplicate_or_nonpositive():  # ValueError
  def test_random_invariants():  # random.Random(42), 200 ชุด, สมาชิก 1–10, รายการ 0–30, amount 1–500000:
      # sum(split)==amount; sum(net)==0; ใช้ transfers แล้วทุกคน net==0; len(transfers) <= max(0, n-1)
  def test_format_satang():  # 0->"0.00", 5->"0.05", 123450->"1,234.50"
  @pytest.mark.parametrize("text, expected", [("100", 10000), ("1,234.5", 123450), ("฿99", 9900), ("99 บาท", 9900), ("0.01", 1)])
  def test_parse_baht_accepts(text, expected):
  @pytest.mark.parametrize("text", ["", "abc", "0", "-5", "1,234.567", "1e3", "12,34", "10000000.01"])
  def test_parse_baht_rejects(text): assert parse_baht(text) is None
  ```
- [ ] **Step 2: รัน** `.venv/Scripts/python.exe -m pytest tests/test_party_math.py -q` → FAIL (ModuleNotFoundError)
- [ ] **Step 3: implement `party_math.py`** ตาม Interfaces
- [ ] **Step 4: รัน** → PASS; รันเทสต์ทั้งหมด → PASS
- [ ] **Step 5: Commit** `feat: party split math`

### Task 2: Schema + `party_store.py`

**Files:**
- Create: `supabase/party_schema.sql`, `party_store.py`, `tests/test_party_store.py`

**Interfaces:**
- Consumes: `party_math.Transfer`
- Produces (`party_store.py`):
  ```python
  CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"; CODE_LENGTH = 6; MAX_MEMBERS = 10
  def new_code(rng: random.Random | None = None) -> str     # ใช้ secrets ถ้า rng เป็น None
  class PartyStore:
      def __init__(self, supabase_url: str, service_role_key: str, *, client: httpx.Client | None = None, timeout: float = 20.0)
      def create_party(self, *, name: str, host_id: str, guild_id: str, channel_id: str, thread_id: str) -> dict
          # สุ่มโค้ด, ชน unique (409/23505) -> สุ่มใหม่ สูงสุด 5 ครั้ง; insert party + host เป็นสมาชิก
      def update_party(self, party_id: str, **fields) -> None    # invite_message_id / summary_message_id
      def get_party(self, party_id: str) -> dict | None
      def party_by_thread(self, thread_id: str) -> dict | None
      def party_by_code(self, code: str) -> dict | None         # upper().strip()
      def members(self, party_id: str) -> list[str]             # เรียงตาม joined_at
      def join(self, party_id: str, member_id: str) -> str      # rpc party_join
      def remove_member(self, party_id: str, member_id: str, actor_id: str) -> str
      def create_draft(self, party_id: str, *, created_by: str, payer_id: str, participants: list[str], amount_satang: int | None,
                       note: str, source: str, trans_ref: str | None = None, image_sha256: str | None = None,
                       slip_message_url: str | None = None) -> dict
      def get_expense(self, expense_id: str) -> dict | None     # รวม shares: select=*,party_expense_shares(discord_id,share_satang)
      def update_draft(self, expense_id: str, **fields) -> dict | None   # กรอง status=eq.draft; None ถ้าไม่ใช่ draft
      def update_expense_message(self, expense_id: str, message_id: str) -> None   # summary_message_id
      def is_duplicate_slip(self, party_id: str, *, trans_ref: str | None, image_sha256: str | None,
                            exclude_expense_id: str | None = None) -> bool   # เทียบเฉพาะ confirmed
      def save_expense(self, expense_id: str, actor_id: str, *, amount_satang: int, payer_id: str, note: str,
                       shares: dict[str, int]) -> str            # rpc party_save_expense
      def delete_expense(self, expense_id: str, actor_id: str) -> str
      def confirmed_expenses(self, party_id: str) -> list[dict] # รวม shares, เรียง created_at
      def close(self, party_id: str, actor_id: str) -> str
      def transfers(self, party_id: str) -> list[dict]          # เรียง created_at
      def create_transfers(self, party_id: str, transfers: list[Transfer]) -> list[dict]   # rpc party_create_transfers (idempotent)
      def toggle_paid(self, transfer_id: str, actor_id: str) -> str
      def delete_drafts(self, party_id: str) -> None
  ```
  สถานะที่ RPC คืน (สตริง): `party_join` → `ok|full|already|closed|not_found`; `party_remove_member` → `ok|involved|not_member|forbidden|host|closed`;
  `party_save_expense` → `ok|closed|forbidden|invalid|duplicate|not_found`; `party_delete_expense` → `ok|closed|forbidden|not_found`;
  `party_close` → `ok|already_closed|forbidden`; `party_toggle_paid` → `paid|unpaid|settled|forbidden|locked`;
  `party_create_transfers` → คืนแถว transfers ทั้งหมดของปาร์ตี้ (สร้างเฉพาะถ้ายังไม่มี; ไม่มีรายการโอน + สถานะ closed → ตั้ง settled)

- [ ] **Step 1: เขียน `supabase/party_schema.sql`** — ตารางตามสเปก (`parties` เพิ่ม `summary_message_id`), FK `on delete cascade`, checks:
  `status in (...)`, `char_length(name) between 1 and 100`, `char_length(note) <= 100`,
  `party_expenses`: `check (status = 'draft' or amount_satang > 0)`; partial unique `(party_id, trans_ref)` และ `(party_id, image_sha256)`
  `where status = 'confirmed' and <col> is not null`; unique `parties(code)`, `parties(thread_id)`; RLS เปิดทุกตาราง ไม่มี policy;
  functions ทั้ง 7 ตัว `security definer set search_path = public`, ล็อก `select ... from parties where id = ... for update` ก่อนตรวจ;
  `revoke execute ... from public, anon, authenticated; grant execute ... to service_role`
  - `party_save_expense` ตรวจ: party open, actor = created_by หรือ host, payer ∈ members, ทุก key ใน shares ∈ members, shares ไม่ว่าง,
    `sum(shares) = amount > 0`; จับ `unique_violation` → `duplicate`; ลบ shares เก่าแล้ว insert ใหม่; set status/amount/payer/note/updated_at
  - `party_remove_member` "involved" = เป็น payer หรืออยู่ใน shares ของรายการ confirmed ใดในปาร์ตี้
  - `party_toggle_paid` คืน `settled` เฉพาะครั้งที่ทำให้ closed → settled
- [ ] **Step 2: เขียนเทสต์** `tests/test_party_store.py` (httpx MockTransport แบบ `tests/test_store.py`):
  `test_new_code_alphabet_and_length` (Random(1) 200 ครั้ง: ยาว 6, ทุกตัวอยู่ใน `CODE_ALPHABET`), `test_headers_secret_key_only_apikey`,
  `test_create_party_retries_on_code_conflict` (ครั้งแรก 409 code 23505 → ครั้งสองสำเร็จ; body มี host เป็นสมาชิก),
  `test_create_party_gives_up_after_5_conflicts` (raise), `test_party_by_code_uppercases`, `test_join_calls_rpc_and_returns_status`
  (`/rest/v1/rpc/party_join`, body `{"p_party":..,"p_member":..}` → `"full"`), `test_update_draft_only_drafts` (มี `status=eq.draft`; `[]` → None),
  `test_is_duplicate_slip_checks_confirmed_ref_or_hash` (`status=eq.confirmed`, `or=(trans_ref.eq."R",image_sha256.eq."H")`; ไม่มีทั้งสองค่า → False ไม่ยิง request),
  `test_save_expense_sends_shares_json` (`p_shares` = `{"1":3334,...}`), `test_confirmed_expenses_embeds_shares`
- [ ] **Step 3: รัน → FAIL; implement `party_store.py` → PASS** (ใช้ `_request` แบบเดียวกับ `store.py` — เขียนในไฟล์นี้ ไม่แก้ `store.py`)
- [ ] **Step 4: smoke test กับ Supabase จริง** — `if __name__ == "__main__":` ใน `party_store.py`: สร้างปาร์ตี้ (thread_id `"smoke-<timestamp>"`),
  join 9 คน ok → คนที่ 11 `full`, ซ้ำ `already`; draft + save shares ผลรวมผิด → `invalid`; save ถูก → `ok`; save สลิป trans_ref ซ้ำ → `duplicate`;
  remove payer → `involved`; close → `ok`; save หลังปิด → `closed`; create_transfers สองครั้งได้ชุดเดิม; toggle ทุกรายการ → ตัวสุดท้าย `settled`;
  ท้ายสุดลบปาร์ตี้ทดสอบ (DELETE parties id) — **ต้องให้เจ้าของรัน `supabase/party_schema.sql` ใน SQL Editor ก่อน** (แจ้งวิธีแบบรอบก่อน)
- [ ] **Step 5: Commit** `feat: party schema and PartyStore`

### Task 3: `/party create / join / leave / kick` + ปุ่ม [เข้าร่วม]

**Files:**
- Create: `party_bot.py`, `tests/test_party_bot.py`
- Modify: `bot.py` (`SlipBot.__init__`, `setup_hook` ใหม่, `on_ready`, `on_message`, `main`)

**Interfaces:**
- Consumes: `PartyStore`, `MAX_MEMBERS` (Task 2)
- Produces (`party_bot.py`):
  ```python
  def setup(bot: SlipBot, store: PartyStore) -> None   # เพิ่ม group "party" เข้า bot.tree + bot.add_dynamic_items(*DYNAMIC_ITEMS)
  async def sync_commands(bot: SlipBot) -> None        # sync ไปที่ guild ของช่อง DISCORD_CHANNEL_ID (copy_global_to + sync(guild=))
  async def handle_message(bot: SlipBot, message: discord.Message) -> bool   # True = ข้อความใน thread ปาร์ตี้ (จัดการแล้ว ห้ามเข้า flow ส่วนตัว)
  JOIN_STATUS_TEXT: dict[str, str]   # สถานะจาก RPC -> ข้อความไทย (และ REMOVE_STATUS_TEXT, SAVE_STATUS_TEXT … ใน task ถัดไป)
  class JoinButton(DynamicItem[discord.ui.Button], template=r"party:join:(?P<party_id>[0-9a-f-]{36})")
  def invite_embed(party: dict, member_count: int) -> discord.Embed
  ```
  `bot.py`: `self.tree = app_commands.CommandTree(self)`, `self.party_store`; `async def setup_hook(self)` → `party_bot.setup(self, self.party_store)`;
  `on_ready` → `await party_bot.sync_commands(self)` (ครั้งเดียว); `on_message` บรรทัดแรกหลังเช็ก bot: `if await party_bot.handle_message(self, message): return`
  — ไม่เปลี่ยนส่วนอื่นของ flow ส่วนตัว; `main()` สร้าง `PartyStore(cfg.supabase_url, cfg.supabase_service_role_key)` ส่งเข้า `SlipBot(cfg, expense_store, party_store)`
- พฤติกรรม: ตามส่วน "Discord" ของสเปก — create (private thread `🎉 <ชื่อ>`, invitable=False, เพิ่ม host, โพสต์ invite embed + JoinButton ในช่องหลัก,
  ephemeral ตอบ host ด้วยลิงก์ thread + โค้ด); join ทั้งปุ่มและ `/party join <code>` → `store.join` → ok ⇒ `thread.add_user`, แก้ invite embed (จำนวนสมาชิก),
  โพสต์ใน thread `<@id> เข้าร่วมแล้ว`; leave/kick → `store.remove_member` → ok ⇒ `thread.remove_user` + โพสต์ใน thread
  คำสั่งที่ต้องอยู่ใน thread แต่ใช้นอก thread → ephemeral "ใช้คำสั่งนี้ใน thread ของปาร์ตี้"

- [ ] **Step 1: เทสต์ helper** `tests/test_party_bot.py`:
  `test_custom_ids_round_trip` — ทุกคลาสใน `party_bot.DYNAMIC_ITEMS` (เพิ่มตามแต่ละ task) สร้างด้วย uuid แล้ว `re.fullmatch(cls.__discord_ui_compiled_template__, item.custom_id)` ได้ uuid เดิม และ custom_id ≤ 100 ตัวอักษร;
  `test_join_status_text_covers_all_statuses` (ทุกสถานะของ `party_join` มีข้อความ), `test_invite_embed_shows_code_and_count` (มีโค้ด และ `"3/10"`)
- [ ] **Step 2: รัน → FAIL; implement → PASS**; รันเทสต์ทั้งหมด (โดยเฉพาะ `tests/test_bot_helpers.py`) → PASS
- [ ] **Step 3: ทดสอบมือ** (หลัง Task 2 Step 4): `python bot.py` → `/party create ทดสอบ` → thread ส่วนตัวเกิด, ข้อความเชิญในช่องหลัก → บัญชีที่ 2 กด [เข้าร่วม] → ถูกเพิ่มเข้า thread;
  บัญชีที่ 3 `/party join <code>` ได้; `/party leave` ได้; host `/party kick` ได้; ส่งสลิปในช่องเดิม `DISCORD_CHANNEL_ID` → flow ส่วนตัวทำงานเหมือนเดิม
- [ ] **Step 4: Commit** `feat: party create, join, leave, kick`

### Task 4: อัปสลิป + ฟอร์มยืนยัน + `/party add`

**Files:**
- Modify: `party_bot.py`, `tests/test_party_bot.py`

**Interfaces:**
- Consumes: `party_math.split / parse_baht / format_satang`, `PartyStore.create_draft / update_draft / get_expense / is_duplicate_slip / save_expense / members`, `SlipBot._ocr_slot`, `slip_qr.read_slip_qr`, `slip_reader.read_slip`
- Produces:
  ```python
  @dataclass
  class FormState: can_confirm: bool; warnings: list[str]; shares: dict[str, int]
  def sanitize_draft(draft: dict, members: list[str]) -> dict   # ตัด participants ที่ไม่ใช่สมาชิก; payer ไม่ใช่สมาชิก -> created_by; participants ว่าง -> ทุกคน
  def form_state(draft: dict, *, duplicate: bool, expired: bool) -> FormState
      # warnings ภาษาไทย: ไม่มียอด / สลิปซ้ำ / หมดอายุ; can_confirm = มียอด and not duplicate and not expired; shares = split ถ้ามียอด
  def expense_summary_embed(expense: dict, shares: dict[str, int]) -> discord.Embed   # ยอด, คนจ่าย, ผู้มีส่วน + ส่วนแบ่งแต่ละคน, หมายเหตุ, ลิงก์สลิป
  async def open_form(interaction: discord.Interaction, expense_id: str, *, edit: bool = False) -> None
  # DynamicItems: OpenDraftButton "party:open:<expense_id>", PayerSelect "party:payer:<expense_id>", SharesSelect "party:shares:<expense_id>",
  #               AmountButton "party:amount:<expense_id>", ConfirmButton "party:confirm:<expense_id>", CancelButton "party:cancel:<expense_id>"
  ```
- พฤติกรรมตามสเปก: `handle_message` — thread ปาร์ตี้ + รูป PNG/JPEG (ใช้ `bot.is_image_attachment` / `bot.guess_mime`) → ปาร์ตี้ไม่ open หรือผู้ส่งไม่ใช่สมาชิก ⇒ reply สั้น ๆ;
  sha256 + QR → ซ้ำ ⇒ reply "สลิปนี้ถูกบันทึกในปาร์ตี้แล้ว" ไม่สร้าง draft; ไม่ซ้ำ ⇒ OCR (ใน `_ocr_slot`) → `create_draft(payer=ผู้ส่ง, participants=ทุกคน, amount=ยอดเป็นสตางค์หรือ None)`
  → reply "พบสลิป ฿X — <@ผู้ส่ง> กดเพื่อตรวจและยืนยัน" + OpenDraftButton (กดได้เฉพาะ created_by) และเก็บ message id ของ reply ลง `summary_message_id`;
  ฟอร์ม ephemeral; ทุกการเปลี่ยนเขียน draft แล้ววาดใหม่ด้วย `interaction.response.edit_message`; Confirm → `save_expense(shares=split(...))` → `ok` ⇒ แก้ข้อความ "พบสลิป" เป็น
  `expense_summary_embed` (หรือโพสต์ใหม่ถ้าเป็น `/party add`), อื่น ๆ ⇒ ข้อความตาม `SAVE_STATUS_TEXT`; Cancel → ลบ draft + ลบข้อความ "พบสลิป"
  `/party add <ยอด> <หมายเหตุ>` → `parse_baht` ผิด ⇒ ephemeral; ถูก ⇒ draft (source cash) → `open_form` ทันที

- [ ] **Step 1: เทสต์** `test_sanitize_draft_drops_non_members`, `test_sanitize_draft_payer_falls_back_to_creator`,
  `test_form_state_requires_amount` (amount None → can_confirm False + คำเตือน), `test_form_state_duplicate_blocks`, `test_form_state_expired_blocks`,
  `test_form_state_shares_sum_to_amount`, `test_save_status_text_covers_all_statuses`, เพิ่ม DynamicItems ใหม่ใน `test_custom_ids_round_trip`
- [ ] **Step 2: รัน → FAIL; implement → PASS**; เทสต์ทั้งหมด → PASS
- [ ] **Step 3: ทดสอบมือ:** อัปสลิปใน thread → [ตรวจ/ยืนยัน] → เปลี่ยนคนจ่าย/ผู้มีส่วน/ยอด → ยืนยัน → สรุปขึ้นใน thread; อัปสลิปเดิมซ้ำ → ถูกปฏิเสธ;
  `/party add 120 ค่าน้ำ` → ฟอร์ม → ยืนยัน; คนอื่นกด [ตรวจ/ยืนยัน] ของเรา → ephemeral ปฏิเสธ; **รีสตาร์ทบอทระหว่างฟอร์มเปิด แล้วกดเมนูต่อ → ใช้ได้**
- [ ] **Step 4: Commit** `feat: party expenses from slips and cash`

### Task 5: `/party status` + แก้ / ลบรายการ

**Files:**
- Modify: `party_bot.py`, `tests/test_party_bot.py`

**Interfaces:**
- Consumes: `party_math.balances / format_satang`, `PartyStore.confirmed_expenses / delete_expense / members`, `open_form(edit=True)` (Task 4)
- Produces:
  ```python
  def expenses_from_rows(rows: list[dict]) -> list[party_math.Expense]   # ใช้ shares ที่บันทึกไว้ (participants = key ของ shares)
  def status_embed(party: dict, rows: list[dict], members: list[str]) -> discord.Embed   # ล่าสุด 25 รายการ + ตาราง จ่ายไป/รับผิดชอบ/สุทธิ
  def editable_rows(rows: list[dict], viewer_id: str, host_id: str) -> list[dict]          # ผู้สร้างหรือ host; ล่าสุด 25 รายการ
  # DynamicItems: EditMenuButton "party:editmenu:<party_id>", PickExpenseSelect "party:pick:<party_id>", DeleteButton "party:delete:<expense_id>"
  ```
  `/party status` โพสต์ใน thread (ไม่ ephemeral) พร้อม EditMenuButton; กดแล้วได้ PickExpenseSelect แบบ ephemeral เฉพาะรายการที่มีสิทธิ์ (ไม่มี → ข้อความ ephemeral);
  เลือก → `open_form(edit=True)` (ฟอร์มเดียวกับ Task 4 + DeleteButton; Confirm ใช้ `save_expense` เหมือนเดิม, แก้ข้อความสรุปรายการใน thread ถ้ามี `summary_message_id`);
  Delete → ยืนยันใน ephemeral → `delete_expense` → ลบ/ขีดฆ่าข้อความสรุปรายการ + โพสต์ "ลบรายการ ... แล้ว"

- [ ] **Step 1: เทสต์** `test_expenses_from_rows_uses_stored_shares` (shares 3334/3333/3333 → balances ตรง), `test_status_embed_totals_and_net`
  (ตัวอย่างสเปก: แสดง `+500.00`, `-100.00`, `-400.00`), `test_editable_rows_creator_or_host`, `test_status_embed_limits_25_rows`
- [ ] **Step 2: รัน → FAIL; implement → PASS**; เทสต์ทั้งหมด → PASS
- [ ] **Step 3: ทดสอบมือ:** status → ทุกคนเห็น; คนที่ไม่ใช่ผู้สร้าง/host ไม่เห็นรายการของคนอื่นในเมนูแก้; แก้ยอด → สรุปรายการใน thread อัปเดต; ลบ → หายจาก status
- [ ] **Step 4: Commit** `feat: party status, edit and delete`

### Task 6: `/party end` + สรุป + [โอนแล้ว]

**Files:**
- Modify: `party_bot.py`, `tests/test_party_bot.py`

**Interfaces:**
- Consumes: `party_math.balances / settle`, `PartyStore.close / confirmed_expenses / create_transfers / transfers / toggle_paid / delete_drafts / update_party`
- Produces:
  ```python
  def final_summary_embed(party: dict, rows: list[dict], members: list[str], transfers: list[dict]) -> discord.Embed
      # ยอดรวม, รายคน จ่ายไป/รับผิดชอบ/สุทธิ, รายการโอน "<@A> → <@B> : 250.00 บาท" + ✅ ถ้า paid; ไม่มีรายการโอน -> "ไม่มีใครต้องโอน"
  def transfer_buttons(transfers: list[dict]) -> discord.ui.View   # PaidButton ต่อรายการ (label = "โอนแล้ว: <ยอด>" / "✅ <ยอด>"), ≤ 9 ปุ่ม
  # DynamicItems: EndConfirmButton "party:end:<party_id>", PaidButton "party:paid:<transfer_id>"
  ```
  `/party end` (host, ephemeral ยืนยัน) → `close` (`ok` หรือ `already_closed` ที่ยังไม่มีสรุป → ทำต่อ) → `delete_drafts` → คำนวณ → `create_transfers`
  → โพสต์ `final_summary_embed` + ปุ่ม, เก็บ `summary_message_id` → แก้ข้อความเชิญในช่องหลักให้ปุ่ม disabled "ปิดแล้ว";
  ไม่มีรายการโอน → ไม่มีปุ่ม และ (RPC ตั้ง settled แล้ว) โพสต์ "✅ ปาร์ตี้นี้เคลียร์ครบแล้ว";
  PaidButton → `toggle_paid` → วาดสรุปใหม่ทั้งข้อความ; `settled` → แก้หัวสรุปเป็น "✅ เคลียร์ครบแล้ว", ปิดปุ่มทั้งหมด, โพสต์แจ้ง (ครั้งเดียว — เฉพาะเมื่อ RPC คืน `settled`);
  `forbidden` / `locked` → ephemeral

- [ ] **Step 1: เทสต์** `test_final_summary_spec_example` (โอน C→A 400.00, B→A 100.00; ยอดรวม 1,200.00), `test_final_summary_no_transfers`,
  `test_final_summary_marks_paid`, `test_transfer_buttons_one_per_transfer`, `test_toggle_status_text_covers_all_statuses`, DynamicItems ใหม่ใน round-trip
- [ ] **Step 2: รัน → FAIL; implement → PASS**; เทสต์ทั้งหมด → PASS
- [ ] **Step 3: ทดสอบมือ:** end → ยืนยัน → สรุปถูกตามตัวอย่าง; อัปสลิป/`/party add` หลังปิด → ปฏิเสธ; คนนอกคู่โอนกด [โอนแล้ว] → ปฏิเสธ; กดครบ → settled + ประกาศครั้งเดียว;
  รีสตาร์ทบอทแล้วกด [โอนแล้ว] ที่เหลือ → ใช้ได้; `/party end` ซ้ำ → ไม่สร้างรายการโอนซ้ำ
- [ ] **Step 4: Commit** `feat: party end, settlement summary and paid buttons`

### Task 7: README + ทดสอบรวม

**Files:**
- Modify: `README.md` (หัวข้อ "ปาร์ตี้หารค่าใช้จ่าย": คำสั่งทั้งหมด, สิทธิ์บอทที่ต้องมี, รัน `supabase/party_schema.sql`)

- [ ] **Step 1:** README ตามข้างบน + บรรทัดในโครงสร้างโปรเจกต์ (`party_math.py`, `party_store.py`, `party_bot.py`, `supabase/party_schema.sql`)
- [ ] **Step 2:** ทดสอบมือครบตามหัวข้อ "การทดสอบ" ในสเปก (2–3 บัญชี, รวมรีสตาร์ทบอทกลางทาง) และ flow ส่วนตัวเดิม (ส่งสลิปในช่องเดิม + `!สรุป`)
- [ ] **Step 3:** `git grep -nE "sb_secret_[A-Za-z0-9]|eyJ[A-Za-z0-9_-]{20,}"` ไม่เจอ; เทสต์ทั้งหมดผ่าน
- [ ] **Step 4: Commit + push branch** `docs: party feature` → `git push -u origin party-group` (ไม่ merge เข้า main)
