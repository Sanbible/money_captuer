"""Slip Expense Bot — entry point (python bot.py)

ผู้ใช้ส่งรูปสลิปในช่องที่กำหนด → อ่าน QR (กันบันทึกซ้ำด้วยเลขอ้างอิง) → Typhoon OCR/LLM อ่านยอด →
ตอบ embed + เมนูเลือกหมวด / ปุ่มแก้ไข / ปุ่มยกเลิก → บันทึกลง Supabase (ตารางเดียวกับเว็บ)
คำสั่ง `!สรุป` หรือ `!สรุป YYYY-MM` → ยอดรวมแต่ละหมวดของเดือน (เฉพาะสลิปของคนที่สั่ง)

ส่วนบนของไฟล์เป็น pure helpers (มี unit test ใน tests/test_bot_helpers.py)
ส่วนล่างเป็นส่วนที่คุยกับ Discord — import ไฟล์นี้ได้โดยไม่ต้องมี .env และไม่สตาร์ตบอท
บอทไม่รู้จักบัญชีเว็บ: บันทึกด้วย discord_id เท่านั้น แล้วเว็บผูกเข้าบัญชีให้ตอนผู้ใช้ login ด้วย Discord
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping, NoReturn, Sequence, TypeVar

import discord
from discord import app_commands

import party_bot
import slip_qr
import slip_reader
import store
from config import BOT_REQUIRED, Config, ConfigError, load_config
from party_store import PartyStore
from slip_common import (  # noqa: F401 — re-export ให้ tests/test_bot_helpers.py
    BKK_TZ,
    bkk_now,
    load_bkk_tz,
    normalize_slip_date,
    normalize_slip_time,
    parse_amount_input,
)

# ใช้ชื่อคงที่ (ไม่ใช่ __name__) เพราะตอนรัน `python bot.py` __name__ จะเป็น "__main__"
log = logging.getLogger("bot")

T = TypeVar("T")

VIEW_TIMEOUT = 24 * 60 * 60  # ปุ่ม/เมนูใช้ได้ 24 ชม.
MODAL_TIMEOUT = 30 * 60  # modal ที่เปิดค้างไว้ถูกลบจาก memory หลัง 30 นาที
DB_TIMEOUT = 30  # วินาที กัน Supabase ค้างจนล็อกไว้ตลอด
SUMMARY_COMMAND = "!สรุป"

EMOJI_WORKING = "⏳"
EMOJI_OK = "✅"
EMOJI_FAIL = "❌"

# content_type ที่รับ -> mime ที่ส่งให้ Typhoon
IMAGE_MIME_TYPES = {"image/png": "image/png", "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg"}
IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

FIELD_LIMIT = 1024  # ความยาวสูงสุดของค่าใน embed field


# =====================================================================
# Pure helpers
# =====================================================================

def format_baht(amount: float | None) -> str:
    """1234.5 -> "฿1,234.50" ; None -> "?" """
    return "?" if amount is None else f"฿{amount:,.2f}"


def format_amount_plain(amount: float | None) -> str:
    """ค่าเริ่มต้นในช่องกรอกยอดของ modal: 1234.5 -> "1234.50" ; None -> "" """
    return "" if amount is None else f"{amount:.2f}"


def _content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _extension(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


def is_image_attachment(filename: str | None, content_type: str | None) -> bool:
    """ไฟล์แนบเป็นรูป PNG/JPEG หรือไม่ (ดูจาก content_type หรือนามสกุลไฟล์)"""
    return _content_type(content_type) in IMAGE_MIME_TYPES or _extension(filename) in IMAGE_EXTENSIONS


def guess_mime(filename: str | None, content_type: str | None) -> str:
    """mime type ที่จะส่งให้ OCR — เชื่อ content_type ก่อน แล้วค่อยดูนามสกุล, ไม่รู้ใช้ image/png"""
    ct = _content_type(content_type)
    if ct in IMAGE_MIME_TYPES:
        return IMAGE_MIME_TYPES[ct]
    return IMAGE_EXTENSIONS.get(_extension(filename), "image/png")


_MONTH_ARG_RE = re.compile(r"(\d{4})-(\d{1,2})")
SUMMARY_USAGE = "รูปแบบไม่ถูกต้อง ใช้ `!สรุป` หรือ `!สรุป YYYY-MM` เช่น `!สรุป 2026-10`"


def parse_summary_command(content: str, today: date) -> str | None:
    """แยกคำสั่งสรุปรายเดือน

    - ไม่ใช่คำสั่ง `!สรุป` -> None
    - `!สรุป` -> เดือนของ `today` ("YYYY-MM")
    - `!สรุป 2026-09` -> "2026-09"
    - รูปแบบเดือนผิด -> raise ValueError (ข้อความภาษาไทยพร้อมตอบผู้ใช้)
    """
    parts = (content or "").split()
    if not parts or parts[0] != SUMMARY_COMMAND:
        return None
    if len(parts) == 1:
        return f"{today.year:04d}-{today.month:02d}"
    match = _MONTH_ARG_RE.fullmatch(parts[1]) if len(parts) == 2 else None
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise ValueError(SUMMARY_USAGE)
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"


def format_slip_datetime(slip_date: str | None, slip_time: str | None) -> str:
    """ข้อความวันที่/เวลาใน embed"""
    return " ".join(part for part in (slip_date or "ไม่ทราบวันที่", slip_time) if part)


@dataclass
class SlipState:
    """ข้อมูลสลิป 1 ใบที่รอผู้ใช้เลือกหมวด (แก้ไขได้ผ่าน modal)"""

    author_id: int
    slip_url: str  # jump_url ของข้อความสลิป
    amount: float | None = None
    date: str | None = None  # "YYYY-MM-DD"
    time: str | None = None  # "HH:MM"
    receiver: str | None = None
    note: str = ""
    bank_code: str | None = None
    trans_ref: str | None = None

    @classmethod
    def from_results(
        cls,
        *,
        author_id: int,
        slip_url: str,
        qr: Mapping[str, str] | None,
        slip: slip_reader.SlipData,
    ) -> SlipState:
        amount = slip.amount
        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
            amount = None
        qr = qr or {}
        return cls(
            author_id=author_id,
            slip_url=slip_url,
            amount=None if amount is None else round(float(amount), 2),
            date=normalize_slip_date(slip.date),
            time=normalize_slip_time(slip.time),
            receiver=(slip.receiver or "").strip() or None,
            bank_code=qr.get("bank_code") or None,
            trans_ref=qr.get("trans_ref") or None,
        )


def build_record(state: SlipState, category: str, now: datetime) -> store.NewExpense:
    """สร้างแถวที่จะบันทึก — ไม่มีวันที่บนสลิปใช้วันที่ของ `now` (เวลาไทย)"""
    if state.amount is None:
        raise ValueError("ยังไม่มีจำนวนเงิน")
    return store.NewExpense(
        date=state.date or now.strftime("%Y-%m-%d"),
        time=state.time or "",
        amount=state.amount,
        receiver=state.receiver or "",
        category=category,
        note=state.note or "",
        bank=slip_qr.bank_name(state.bank_code),
        trans_ref=state.trans_ref or "",
        source="discord",
        discord_id=str(state.author_id),
        discord_link=state.slip_url,
    )


UNCATEGORIZED = "ไม่ระบุหมวด"


def summarize_by_category(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """{หมวด: ยอดรวม} เรียงจากมากไปน้อย"""
    totals: dict[str, float] = {}
    for row in rows:
        category = str(row.get("category") or "").strip() or UNCATEGORIZED
        totals[category] = totals.get(category, 0.0) + float(row["amount"])
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return {category: round(total, 2) for category, total in ordered}


def month_range(month: str) -> tuple[str, str]:
    """"2026-10" -> ("2026-10-01", "2026-11-01") — ช่วง [start, end) ของเดือน"""
    year, mon = int(month[:4]), int(month[5:7])
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return date(year, mon, 1).isoformat(), end.isoformat()


def format_summary(month: str, totals: Mapping[str, float] | None) -> str:
    """เนื้อความสรุปรายเดือน: หมวดเรียงจากยอดมากไปน้อย + บรรทัดยอดรวม"""
    if not totals:
        return f"ยังไม่มีข้อมูลเดือน {month}"
    items = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = [f"• {category or UNCATEGORIZED}: {format_baht(total)}" for category, total in items]
    lines.append(f"**รวมทั้งเดือน: {format_baht(sum(totals.values()))}**")
    return "\n".join(lines)


def _clip(text: str, limit: int = FIELD_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# =====================================================================
# Discord UI
# =====================================================================

def build_slip_embed(
    state: SlipState,
    *,
    title: str,
    color: discord.Color,
    footer: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="จำนวนเงิน", value=format_baht(state.amount))
    embed.add_field(name="วันที่/เวลา", value=format_slip_datetime(state.date, state.time))
    embed.add_field(name="ผู้รับ", value=_clip(state.receiver or "-"), inline=False)
    embed.add_field(name="ธนาคาร", value=slip_qr.bank_name(state.bank_code) or "-")
    embed.add_field(name="เลขอ้างอิง", value=_clip(state.trans_ref or "-"))
    if state.note:
        embed.add_field(name="โน้ต", value=_clip(state.note), inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def send_ephemeral(interaction: discord.Interaction, text: str) -> None:
    """ตอบแบบเห็นคนเดียว ไม่ว่า interaction จะถูกตอบไปแล้วหรือยัง (ไม่ throw)"""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException as e:
        log.warning("ส่งข้อความ ephemeral ไม่สำเร็จ: %s", e)


AMOUNT_ERROR = "จำนวนเงินไม่ถูกต้อง — ใส่ตัวเลขมากกว่า 0 เช่น 1234.50 หรือ 1,234.50"


class CategorySelect(discord.ui.Select):
    def __init__(self, categories: Sequence[str]) -> None:
        unique = list(dict.fromkeys(c[:100] for c in categories))  # Discord ห้าม value ซ้ำ, ยาวสุด 100
        super().__init__(
            placeholder="เลือกหมวด…",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=c, value=c) for c in unique],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, SlipView)
        await self.view.on_category_selected(interaction, self.values[0])


class EditSlipModal(discord.ui.Modal):
    """ปุ่ม "แก้ไข" — แก้ จำนวนเงิน / ผู้รับ / โน้ต"""

    def __init__(self, slip_view: SlipView) -> None:
        super().__init__(title="แก้ไขรายการ", timeout=MODAL_TIMEOUT)
        self.slip_view = slip_view
        state = slip_view.state
        self.amount_input = discord.ui.TextInput(
            default=format_amount_plain(state.amount) or None,
            placeholder="เช่น 1,234.50 (เว้นว่าง = ยังไม่รู้ยอด)",
            required=False,
            max_length=20,
        )
        self.receiver_input = discord.ui.TextInput(default=state.receiver or None, required=False, max_length=100)
        self.note_input = discord.ui.TextInput(default=state.note or None, required=False, max_length=200)
        self.add_item(discord.ui.Label(text="จำนวนเงิน (บาท)", component=self.amount_input))
        self.add_item(discord.ui.Label(text="ผู้รับ", component=self.receiver_input))
        self.add_item(discord.ui.Label(text="โน้ต", component=self.note_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = self.slip_view
        if view.closed:
            await send_ephemeral(interaction, "รายการนี้ปิดไปแล้ว ส่งสลิปใหม่ได้")
            return
        raw_amount = self.amount_input.value.strip()
        amount = parse_amount_input(raw_amount) if raw_amount else None
        if raw_amount and amount is None:
            await view.reject(interaction, AMOUNT_ERROR)
            return
        view.state.amount = amount
        view.state.receiver = self.receiver_input.value.strip() or None
        view.state.note = self.note_input.value.strip()
        log.info("แก้ไขรายการ amount=%s ref=%s", amount, view.state.trans_ref)
        await interaction.response.edit_message(embed=view.pending_embed(), view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("EditSlipModal error: %s", error, exc_info=error)
        await send_ephemeral(interaction, "เกิดข้อผิดพลาด ลองใหม่อีกครั้ง")


class AmountModal(discord.ui.Modal):
    """เลือกหมวดแล้วแต่ยังไม่รู้ยอด -> ให้พิมพ์ยอด (และโน้ต) ก่อนบันทึก"""

    def __init__(self, slip_view: SlipView, category: str) -> None:
        super().__init__(title="ใส่จำนวนเงินก่อนบันทึก", timeout=MODAL_TIMEOUT)
        self.slip_view = slip_view
        self.category = category
        self.amount_input = discord.ui.TextInput(placeholder="เช่น 1,234.50", required=True, max_length=20)
        self.note_input = discord.ui.TextInput(
            default=slip_view.state.note or None, required=False, max_length=200
        )
        self.add_item(
            discord.ui.Label(
                text="จำนวนเงิน (บาท)",
                description=_clip(f"หมวด: {category}", 100),
                component=self.amount_input,
            )
        )
        self.add_item(discord.ui.Label(text="โน้ต (ไม่บังคับ)", component=self.note_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = self.slip_view
        if view.closed:
            await send_ephemeral(interaction, "รายการนี้ปิดไปแล้ว ส่งสลิปใหม่ได้")
            return
        amount = parse_amount_input(self.amount_input.value)
        if amount is None:
            await view.reject(interaction, AMOUNT_ERROR + " แล้วเลือกหมวดอีกครั้ง")
            return
        view.state.amount = amount
        view.state.note = self.note_input.value.strip()
        await view.save(interaction, self.category)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("AmountModal error: %s", error, exc_info=error)
        await send_ephemeral(interaction, "เกิดข้อผิดพลาด ลองใหม่อีกครั้ง")


class SlipView(discord.ui.View):
    """เมนูหมวด + ปุ่มแก้ไข/ยกเลิก ใต้ embed ของสลิป 1 ใบ"""

    def __init__(self, bot: SlipBot, state: SlipState, slip_message: discord.Message) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.bot = bot
        self.state = state
        self.slip_message = slip_message
        self.message: discord.Message | None = None  # ข้อความของบอท (ตั้งหลัง reply)
        self.closed = False  # บันทึก/ยกเลิก/หมดเวลาแล้ว
        self._busy = False  # กันกดซ้ำระหว่างบันทึก
        self.add_item(CategorySelect(bot.cfg.categories))

    # ---------- embeds ----------

    def pending_embed(self) -> discord.Embed:
        if self.state.amount is None:
            footer = "อ่านยอดไม่ได้ — เลือกหมวดแล้วพิมพ์ยอดเอง หรือกด แก้ไข"
            color = discord.Color.orange()
        else:
            footer = "เลือกหมวดเพื่อบันทึก • กด แก้ไข เพื่อแก้ยอด/ผู้รับ/โน้ต"
            color = discord.Color.blurple()
        return build_slip_embed(self.state, title="🧾 สลิปโอนเงิน", color=color, footer=footer)

    def saved_embed(self, category: str) -> discord.Embed:
        embed = build_slip_embed(self.state, title="✅ บันทึกแล้ว", color=discord.Color.green())
        embed.add_field(name="หมวด", value=_clip(category))
        if self.bot.cfg.public_base_url:
            embed.add_field(name="ดูในเว็บ", value=f"{self.bot.cfg.public_base_url}/dashboard")
        return embed

    # ---------- discord.ui.View hooks ----------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.state.author_id:
            return True
        await interaction.response.send_message("ไม่มีสิทธิ์ใช้เมนูนี้ — เฉพาะคนที่ส่งสลิปเท่านั้น", ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.closed:
            return
        self._close()
        if self.message is None:
            return
        try:
            await self.message.edit(content="หมดเวลา ส่งสลิปใหม่ได้", view=self)
        except discord.HTTPException as e:  # ข้อความถูกลบ ฯลฯ
            log.warning("แก้ข้อความตอนหมดเวลาไม่สำเร็จ ref=%s: %s", self.state.trans_ref, e)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.error("SlipView error ref=%s: %s", self.state.trans_ref, error, exc_info=error)
        await send_ephemeral(interaction, "เกิดข้อผิดพลาด ลองใหม่อีกครั้ง")

    # ---------- buttons ----------

    @discord.ui.button(label="แก้ไข", style=discord.ButtonStyle.secondary, row=1)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EditSlipModal(self))

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.danger, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self._busy:
            await send_ephemeral(interaction, "กำลังบันทึกอยู่ รอสักครู่")
            return
        self._close()
        embed = build_slip_embed(self.state, title="ยกเลิกแล้ว", color=discord.Color.light_grey())
        await interaction.response.edit_message(content="ยกเลิกแล้ว", embed=embed, view=self)
        log.info("ยกเลิก amount=%s ref=%s", self.state.amount, self.state.trans_ref)

    # ---------- flow ----------

    async def on_category_selected(self, interaction: discord.Interaction, category: str) -> None:
        if self.state.amount is None:
            # Select callback ตอบด้วย modal ได้ (component interaction)
            await interaction.response.send_modal(AmountModal(self, category))
        else:
            await self.save(interaction, category)

    async def reject(self, interaction: discord.Interaction, text: str) -> None:
        """ข้อมูลใน modal ไม่ถูก: วาดข้อความใหม่ (รีเซ็ตเมนูที่ค้างค่าไว้ให้เลือกซ้ำได้) แล้วแจ้งแบบ ephemeral"""
        await interaction.response.edit_message(embed=self.pending_embed(), view=self)
        await send_ephemeral(interaction, text)

    async def save(self, interaction: discord.Interaction, category: str) -> None:
        if self.closed or self._busy:
            await send_ephemeral(interaction, "รายการนี้กำลังบันทึกหรือปิดไปแล้ว")
            return
        self._busy = True  # ตั้งก่อน await แรก -> กดซ้ำจะไม่บันทึกสองแถว
        try:
            await interaction.response.defer()  # ack ภายใน 3 วิ ก่อนเรียกฐานข้อมูล
            record = build_record(self.state, category, bkk_now())
            try:
                saved = await self.bot.save_expense(record)
            except Exception as e:
                log.error("บันทึกลงฐานข้อมูลไม่สำเร็จ amount=%s ref=%s: %s", record.amount, record.trans_ref, e)
                await self._edit(interaction, embed=self.pending_embed(), view=self)  # รีเซ็ตเมนูให้ลองใหม่ได้
                await send_ephemeral(interaction, "บันทึกลงฐานข้อมูลไม่สำเร็จ ลองเลือกหมวดอีกครั้ง")
                return

            self._close()
            if not saved:
                await self._edit(interaction, content="สลิปนี้บันทึกไปแล้ว", view=self)
                return
            log.info("บันทึกแล้ว amount=%.2f ref=%s", record.amount, record.trans_ref)
            await self._edit(interaction, content=None, embed=self.saved_embed(category), view=self)
            await self.bot.swap_reaction(self.slip_message, EMOJI_FAIL, EMOJI_OK)
        finally:
            self._busy = False

    # ---------- internals ----------

    def _close(self) -> None:
        """ปิดทุกปุ่ม/เมนูและหยุดฟัง interaction"""
        self.closed = True
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.stop()

    async def _edit(self, interaction: discord.Interaction, **fields: Any) -> None:
        """แก้ข้อความของบอทหลัง defer — ใช้ webhook ของ interaction ก่อน ถ้าไม่ได้ค่อยแก้ข้อความตรง ๆ"""
        try:
            await interaction.edit_original_response(**fields)
            return
        except discord.HTTPException as e:
            log.warning("edit_original_response ไม่สำเร็จ: %s", e)
        if self.message is not None:
            try:
                await self.message.edit(**fields)
            except discord.HTTPException as e:
                log.warning("แก้ข้อความบอทไม่สำเร็จ: %s", e)


# =====================================================================
# Bot
# =====================================================================

class SlipBot(discord.Client):
    def __init__(
        self, cfg: Config, expense_store: store.ExpenseStore, party_store: PartyStore | None = None
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.cfg = cfg
        self.store = expense_store
        self.tree = app_commands.CommandTree(self)
        self._party_store = party_store  # None = ปิดระบบปาร์ตี้
        self._commands_synced = False
        self._db_lock = asyncio.Lock()  # insert ทีละรายการ (unique index ใน DB กันสลิปซ้ำอีกชั้น)
        self._ocr_slot = asyncio.Semaphore(1)  # Typhoon OCR จำกัด 2 req/s, 20 req/min — อ่านทีละใบ

    # ---------- events ----------

    async def setup_hook(self) -> None:
        if self._party_store is not None:
            party_bot.setup(self, self._party_store)  # /party + ปุ่มแบบ persistent (ใช้ได้หลังรีสตาร์ท)

    async def on_ready(self) -> None:
        log.info("ล็อกอินเป็น %s — ฟังช่อง %s", self.user, self.cfg.discord_channel_id)
        if self._party_store is not None and not self._commands_synced:
            self._commands_synced = True
            try:
                await party_bot.sync_commands(self)
            except discord.HTTPException as e:
                log.error("ลงทะเบียนคำสั่ง /party ไม่สำเร็จ: %s", e)
        if self.get_channel(self.cfg.discord_channel_id) is None:
            log.warning(
                "มองไม่เห็นช่อง DISCORD_CHANNEL_ID=%s — เช็กว่าเชิญบอทเข้าเซิร์ฟเวอร์และให้สิทธิ์ View Channel แล้ว",
                self.cfg.discord_channel_id,
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        # ข้อความใน thread ปาร์ตี้ไปที่ party_bot เท่านั้น — ไม่เข้า flow รายจ่ายส่วนตัว
        if self._party_store is not None and await party_bot.handle_message(self, message):
            return
        if message.channel.id != self.cfg.discord_channel_id:
            return

        try:
            month = parse_summary_command(message.content, bkk_now().date())
        except ValueError as e:
            await self._reply(message, str(e))
            return
        if month is not None:
            await self.handle_summary(message, month)
            return

        images = [a for a in message.attachments if is_image_attachment(a.filename, a.content_type)]
        if images:
            await self.handle_slip_message(message, images)

    # ---------- slips ----------

    async def handle_slip_message(self, message: discord.Message, images: Sequence[discord.Attachment]) -> None:
        await self._add_reaction(message, EMOJI_WORKING)
        all_ok = True
        for attachment in images:
            try:
                ok = await self.process_attachment(message, attachment)
            except Exception:
                log.exception("ประมวลผลสลิปไม่สำเร็จ")
                ok = False
                await self._reply(message, "ประมวลผลสลิปไม่สำเร็จ ลองส่งใหม่อีกครั้ง")
            all_ok = all_ok and ok
        await self.swap_reaction(message, EMOJI_WORKING, EMOJI_OK if all_ok else EMOJI_FAIL)

    async def process_attachment(self, message: discord.Message, attachment: discord.Attachment) -> bool:
        """อ่านสลิป 1 รูปแล้วตอบ embed + View ; คืน True ถ้าอ่านยอดได้ (หรือเป็นสลิปซ้ำ)"""
        data = await attachment.read()
        mime = guess_mime(attachment.filename, attachment.content_type)

        qr = await asyncio.to_thread(slip_qr.read_slip_qr, data)
        trans_ref = qr.get("trans_ref") if qr else None
        if trans_ref and await self.is_duplicate(trans_ref, str(message.author.id)):
            log.info("สลิปซ้ำ ref=%s", trans_ref)
            await self._reply(message, "สลิปนี้บันทึกไปแล้ว")
            return True

        async with self._ocr_slot:
            slip = await asyncio.to_thread(
                slip_reader.read_slip, data, self.cfg.typhoon_api_key, self.cfg.typhoon_llm_model, mime
            )
        state = SlipState.from_results(author_id=message.author.id, slip_url=message.jump_url, qr=qr, slip=slip)
        if state.amount is None:
            log.warning("อ่านยอดไม่ได้ ref=%s error=%s", trans_ref, slip.error)
        else:
            log.info("อ่านสลิปแล้ว amount=%.2f ref=%s", state.amount, trans_ref)

        view = SlipView(self, state, message)
        view.message = await message.reply(embed=view.pending_embed(), view=view, mention_author=False)
        return state.amount is not None

    # ---------- Supabase ----------

    async def _db_call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), DB_TIMEOUT)

    async def is_duplicate(self, trans_ref: str, discord_id: str) -> bool:
        """คนนี้เคยบันทึกเลขอ้างอิงนี้แล้วหรือยัง — ถ้าฐานข้อมูลมีปัญหาให้ log แล้วถือว่าไม่ซ้ำ (ไม่บล็อกผู้ใช้)"""
        try:
            return await self._db_call(self.store.is_duplicate, trans_ref, discord_id=discord_id)
        except Exception as e:
            log.warning("เช็กสลิปซ้ำไม่สำเร็จ ref=%s: %s", trans_ref, e)
            return False

    async def save_expense(self, record: store.NewExpense) -> bool:
        """insert — False ถ้าซ้ำ (unique index ใน DB) ; ล้มเหลว -> raise"""
        async with self._db_lock:
            row = await self._db_call(self.store.insert, record)
        if row is None:
            log.info("สลิปซ้ำตอนบันทึก ref=%s", record.trans_ref)
        return row is not None

    async def handle_summary(self, message: discord.Message, month: str) -> None:
        start, end = month_range(month)
        try:
            rows = await self._db_call(
                self.store.list_expenses, start=start, end=end, discord_id=str(message.author.id)
            )
        except Exception as e:
            log.error("ดึงสรุปเดือน %s ไม่สำเร็จ: %s", month, e)
            await self._reply(message, "ดึงข้อมูลจากฐานข้อมูลไม่สำเร็จ ลองใหม่อีกครั้ง")
            return
        embed = discord.Embed(
            title=f"📊 สรุปรายจ่าย {month}",
            description=format_summary(month, summarize_by_category(rows)),
            color=discord.Color.teal(),
        )
        await self._reply(message, embed=embed)

    # ---------- small Discord helpers (ไม่ throw) ----------

    async def _reply(self, message: discord.Message, content: str | None = None, **kwargs: Any) -> None:
        try:
            await message.reply(content, mention_author=False, **kwargs)
        except discord.HTTPException as e:
            log.warning("ตอบข้อความไม่สำเร็จ: %s", e)

    async def _add_reaction(self, message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException as e:  # ไม่มีสิทธิ์ Add Reactions ฯลฯ
            log.warning("ใส่ reaction %s ไม่สำเร็จ: %s", emoji, e)

    async def _remove_reaction(self, message: discord.Message, emoji: str) -> None:
        if self.user is None:
            return
        try:
            await message.remove_reaction(emoji, self.user)
        except discord.HTTPException as e:
            log.warning("ลบ reaction %s ไม่สำเร็จ: %s", emoji, e)

    async def swap_reaction(self, message: discord.Message, old: str, new: str) -> None:
        await self._remove_reaction(message, old)
        await self._add_reaction(message, new)


# =====================================================================
# Startup
# =====================================================================

def _setup_logging() -> None:
    # คอนโซล Windows อาจไม่ใช่ UTF-8 — กันข้อความภาษาไทยทำ print/log พัง
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    # httpx (openai SDK) log ทุก request ที่ INFO — ลดเสียงรบกวน
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _fail(text: str) -> NoReturn:
    print(text, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    _setup_logging()

    try:
        cfg = load_config(required=BOT_REQUIRED)
    except ConfigError as e:
        _fail(f"❌ {e}\nคัดลอก .env.example เป็น .env แล้วกรอกค่าให้ครบ (ดู README.md)")

    expense_store = store.ExpenseStore(cfg.supabase_url, cfg.supabase_service_role_key)
    party_store = PartyStore(cfg.supabase_url, cfg.supabase_service_role_key)

    bot = SlipBot(cfg, expense_store, party_store)
    try:
        # log_handler=None: ใช้ root logging ที่ตั้งไว้ข้างบน (discord.py ไม่ต้องติด handler ของตัวเอง)
        bot.run(cfg.discord_token, log_handler=None)
    except discord.LoginFailure:
        _fail("❌ DISCORD_TOKEN ไม่ถูกต้อง — Reset Token ใน Discord Developer Portal แล้วใส่ใน .env ใหม่")
    except discord.PrivilegedIntentsRequired:
        _fail("❌ ยังไม่ได้เปิด Message Content Intent — Developer Portal → Bot → Privileged Gateway Intents")


if __name__ == "__main__":
    main()
