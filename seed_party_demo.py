"""สร้างปาร์ตี้ทดลอง (เพื่อนปลอม) ใน Supabase จริง ไว้ลองฟังก์ชันโอนเงินคนเดียว

python seed_party_demo.py --email you@example.com              # สร้าง 3 ปาร์ตี้ (คุณเป็น host)
python seed_party_demo.py --email you@example.com --amount 50  # ยอดที่คุณต้องโอน = 50 บาท (ใช้สลิปจริงยอดนี้)
python seed_party_demo.py --email you@example.com --clean      # ลบปาร์ตี้ทดลองทั้งหมดของคุณ

ปาร์ตี้ที่ได้ (ชื่อขึ้นต้นด้วย DEMO_PREFIX):
  1. เราต้องโอน      — ปิดแล้ว ; คุณ → มิ้นท์ ตามยอด --amount → ลองแนบสลิป (ยอดต้องตรงเป๊ะ)
  2. เพื่อนโอนให้เรา  — ปิดแล้ว ; ต้น/บอส/เจน → คุณ → ลองกด "ได้รับแล้ว" / กดซ้ำเพื่อยกเลิก ; ครบ → settled
  3. ยังไม่ปิด        — เปิดอยู่ ; ลองกดปิดปาร์ตี้เองแล้วดูรายการโอนที่ระบบคำนวณ
เพื่อนปลอม = uuid สุ่ม ไม่มีบัญชีจริง จึงล็อกอินเป็นเพื่อนไม่ได้ (ฝั่งเพื่อนลองไม่ได้)
"""
from __future__ import annotations

import argparse
import sys
import uuid

import httpx

from config import ConfigError, load_config
from party_math import balances, format_satang, parse_baht, settle, split, Expense
from party_store import PartyStore

DEMO_PREFIX = "🧪 ทดลอง"


def find_user(cfg, email: str) -> tuple[str, str]:
    """(user id, ชื่อ) ของบัญชีเว็บจากอีเมล — ผ่าน Supabase Auth admin API"""
    key = cfg.supabase_service_role_key
    headers = {"apikey": key}
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    target = email.strip().lower()
    page = 1
    while True:
        resp = httpx.get(f"{cfg.supabase_url}/auth/v1/admin/users", headers=headers,
                         params={"page": page, "per_page": 200}, timeout=20)
        resp.raise_for_status()
        users = resp.json().get("users") or []
        for u in users:
            if (u.get("email") or "").lower() == target:
                meta = u.get("user_metadata") or {}
                return u["id"], meta.get("full_name") or meta.get("name") or target.split("@")[0]
        if len(users) < 200:
            raise SystemExit(f"ไม่พบบัญชีเว็บที่ใช้อีเมล {email} — ล็อกอินเว็บด้วยอีเมลนี้สักครั้งก่อน")
        page += 1


def build_party(store: PartyStore, me: tuple[str, str], name: str, friends: list[str],
                expenses: list[tuple[str, int, list[str], str]], *, close: bool) -> dict:
    """expenses = [(ชื่อคนจ่าย, ยอดสตางค์, [ชื่อผู้มีส่วน], โน้ต)] — ชื่อ "me" = คุณ"""
    me_id, me_name = me
    party = store.create_party(name=f"{DEMO_PREFIX}: {name}", host_id=me_id, host_name=me_name)
    pid = party["id"]
    ids = {"me": me_id}
    for friend in friends:
        ids[friend] = str(uuid.uuid4())
        assert store.join(pid, ids[friend], friend) == "ok"
    for payer, amount, people, note in expenses:
        status, _ = store.add_expense(pid, me_id, amount_satang=amount, payer_id=ids[payer], note=note,
                                      source="cash", trans_ref=None, image_sha256=None,
                                      shares=split(amount, [ids[p] for p in people]))
        assert status == "ok", status
    if close:  # เหมือน POST /api/parties/{id}/end
        assert store.close(pid, me_id) == "ok"
        exps = [Expense(r["payer_id"], int(r["amount_satang"]),
                        tuple(s["user_id"] for s in r["party_expense_shares"])) for r in store.expenses(pid)]
        net = {u: b.net for u, b in balances([m["user_id"] for m in store.members(pid)], exps).items()}
        store.create_transfers(pid, settle(net))

    names = {v: ("คุณ" if k == "me" else k) for k, v in ids.items()}
    print(f"\n✅ {party['name']}  (โค้ด {party['code']}, {'ปิดแล้ว' if close else 'เปิดอยู่'})")
    for t in store.transfers(pid):
        print(f"   {names[t['from_id']]} → {names[t['to_id']]}  ฿{format_satang(int(t['amount_satang']))}")
    return party


def clean(store: PartyStore, me_id: str) -> None:
    demo = [p for p in store.parties_of(me_id) if p["name"].startswith(DEMO_PREFIX) and p["host_id"] == me_id]
    for p in demo:
        store._request("DELETE", "parties", params=[("id", f"eq.{p['id']}")]).raise_for_status()
        print(f"ลบ {p['name']} ({p['code']})")
    print(f"ลบปาร์ตี้ทดลองแล้ว {len(demo)} ปาร์ตี้")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="สร้าง/ลบปาร์ตี้ทดลองสำหรับลองโอนเงิน")
    parser.add_argument("--email", required=True, help="อีเมลบัญชีเว็บของคุณ")
    parser.add_argument("--amount", default="220.50", help="ยอดที่คุณต้องโอนในปาร์ตี้ที่ 1 (บาท)")
    parser.add_argument("--clean", action="store_true", help="ลบปาร์ตี้ทดลองทั้งหมดของคุณ")
    args = parser.parse_args()

    try:
        cfg = load_config(required=["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
    except ConfigError as exc:
        print(f"ตั้งค่าไม่ครบ: {exc}")
        return 1
    store = PartyStore(cfg.supabase_url, cfg.supabase_service_role_key)
    me = find_user(cfg, args.email)
    if args.clean:
        clean(store, me[0])
        return 0
    amount = parse_baht(args.amount)
    if amount is None:
        print("--amount ไม่ถูกต้อง เช่น 220.50")
        return 1

    # 1) มิ้นท์จ่ายแทนทั้ง 3 คน → คุณกับบอสโอนให้มิ้นท์คนละ amount
    build_party(store, me, "เราต้องโอน", ["มิ้นท์", "บอส"],
                [("มิ้นท์", amount * 3, ["me", "มิ้นท์", "บอส"], "หมูกระทะ")], close=True)
    # 2) คุณจ่ายเยอะ → ต้น 400 / บอส 300 / เจน 200 โอนให้คุณ
    build_party(store, me, "เพื่อนโอนให้เรา", ["บอส", "เจน", "ต้น"],
                [("me", 120000, ["me", "บอส", "เจน", "ต้น"], "ค่าที่พัก"),
                 ("เจน", 20000, ["เจน", "ต้น"], "ค่าแท็กซี่")], close=True)
    # 3) ยังไม่ปิด → ลองกดปิดเอง: บอส → มิ้นท์ 195, คุณ → มิ้นท์ 105
    build_party(store, me, "ยังไม่ปิด", ["มิ้นท์", "บอส"],
                [("มิ้นท์", 45000, ["me", "มิ้นท์", "บอส"], "ชาบู"),
                 ("me", 9000, ["me", "บอส"], "ชานม")], close=False)
    print("\nเปิดเว็บ → ปาร์ตี้ แล้วลองได้เลย ; ลบทิ้ง: python seed_party_demo.py --email ... --clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
