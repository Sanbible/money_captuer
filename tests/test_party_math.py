"""Unit tests สำหรับ party_math.py — เงินเป็นสตางค์ (int) ทั้งหมด; id ผู้ใช้เป็นสตริงตัวเลขแบบ Discord"""
from __future__ import annotations

import random

import pytest

from party_math import Balance, Expense, Transfer, balances, format_satang, parse_baht, settle, split

A, B, C, D = "1", "2", "3", "4"


def nets(b: dict[str, Balance]) -> dict[str, int]:
    return {k: v.net for k, v in b.items()}


# ---------- กรณีในสเปก ----------

def test_spec_example():
    exps = [Expense(A, 90000, (A, B, C)), Expense(B, 30000, (A, B, C))]
    b = balances([A, B, C], exps)
    assert nets(b) == {A: 50000, B: -10000, C: -40000}
    assert settle(nets(b)) == [Transfer(C, A, 40000), Transfer(B, A, 10000)]


def test_split_uneven():
    assert split(10000, [C, A, B]) == {A: 3334, B: 3333, C: 3333}


def test_split_remainder_follows_numeric_id_order():
    # "10" > "9" ทางตัวเลข แม้ว่าเรียงแบบสตริงจะกลับกัน
    assert split(101, ["10", "9"]) == {"9": 51, "10": 50}


def test_payer_not_participant():
    b = balances([A, B, C], [Expense(A, 30000, (B, C))])
    assert b[A] == Balance(paid=30000, owed=0, net=30000)
    assert nets(b) == {A: 30000, B: -15000, C: -15000}


def test_member_without_share_is_zero_and_not_in_transfers():
    b = balances([A, B, C, D], [Expense(A, 30000, (A, B, C))])
    assert b[D] == Balance(0, 0, 0)
    assert all(D not in (t.from_id, t.to_id) for t in settle(nets(b)))


def test_everyone_paid_equal_no_transfers():
    exps = [Expense(p, 9000, (A, B, C)) for p in (A, B, C)]  # หาร 3 ลงตัว
    b = balances([A, B, C], exps)
    assert nets(b) == {A: 0, B: 0, C: 0}
    assert settle(nets(b)) == []


def test_no_expenses():
    assert nets(balances([A, B], [])) == {A: 0, B: 0}
    assert settle({}) == []


@pytest.mark.parametrize(
    "amount, participants",
    [(100, []), (100, [A, A]), (0, [A]), (-5, [A, B])],
)
def test_split_rejects_empty_duplicate_or_nonpositive(amount, participants):
    with pytest.raises(ValueError):
        split(amount, participants)


def test_settle_rejects_unbalanced():
    with pytest.raises(ValueError):
        settle({A: 100, B: -50})


# ---------- invariants ----------

def test_random_invariants():
    rng = random.Random(42)
    for _ in range(200):
        n = rng.randint(1, 10)
        members = [str(rng.randint(10**17, 10**18)) for _ in range(n)]
        members = list(dict.fromkeys(members))
        exps = []
        for _ in range(rng.randint(0, 30)):
            amount = rng.randint(1, 500000)
            parts = tuple(rng.sample(members, rng.randint(1, len(members))))
            shares = split(amount, parts)
            assert sum(shares.values()) == amount
            assert max(shares.values()) - min(shares.values()) <= 1
            exps.append(Expense(rng.choice(members), amount, parts))
        net = nets(balances(members, exps))
        assert sum(net.values()) == 0
        transfers = settle(net)
        assert len(transfers) <= max(0, len(members) - 1)
        left = dict(net)
        for t in transfers:
            assert t.amount > 0 and t.from_id != t.to_id
            left[t.from_id] += t.amount
            left[t.to_id] -= t.amount
        assert all(v == 0 for v in left.values())


# ---------- format / parse ----------

@pytest.mark.parametrize("satang, text", [(0, "0.00"), (5, "0.05"), (123450, "1,234.50"), (-10000, "-100.00")])
def test_format_satang(satang, text):
    assert format_satang(satang) == text


@pytest.mark.parametrize(
    "text, expected",
    [("100", 10000), ("1,234.5", 123450), ("฿99", 9900), ("99 บาท", 9900), ("0.01", 1), (" 12.30 ", 1230),
     ("10000000", 1_000_000_000)],
)
def test_parse_baht_accepts(text, expected):
    assert parse_baht(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", "1,234.567", "1e3", "12,34", "10000000.01", None])
def test_parse_baht_rejects(text):
    assert parse_baht(text) is None
