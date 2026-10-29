"""ปาร์ตี้หารค่าใช้จ่ายในบอท Discord — slash commands `/party …` + ปุ่ม/เมนูแบบ persistent

state ทั้งหมดอยู่ใน Supabase (party_store) — ปุ่ม/เมนูทุกตัวเป็น DynamicItem ที่ฝัง id ไว้ใน custom_id
(`party:<action>:<uuid>`) แล้วอ่านข้อมูลจาก DB ใหม่ทุกครั้งที่กด จึงใช้ได้แม้บอทรีสตาร์ท
เงินเป็นสตางค์ (int) — คำนวณด้วย party_math
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

import discord
from discord import app_commands

import slip_qr
import slip_reader
from party_math import Expense, balances, format_satang, parse_baht, split
from party_store import MAX_MEMBERS, PartyStore
from slip_common import guess_mime, is_image_attachment

if TYPE_CHECKING:
    from bot import SlipBot

log = logging.getLogger("party")

T = TypeVar("T")

DB_TIMEOUT = 30  # วินาที
UUID_RE = r"(?P<target>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
COLOR_OPEN = discord.Color.blurple()
COLOR_DONE = discord.Color.green()
COLOR_CLOSED = discord.Color.dark_grey()

JOIN_STATUS_TEXT = {
    "ok": "เข้าร่วมแล้ว",
    "full": f"ปาร์ตี้นี้เต็มแล้ว (สูงสุด {MAX_MEMBERS} คน)",
    "already": "คุณอยู่ในปาร์ตี้นี้แล้ว",
    "closed": "ปาร์ตี้นี้ปิดแล้ว เข้าร่วมไม่ได้",
    "not_found": "ไม่พบปาร์ตี้นี้",
}
REMOVE_STATUS_TEXT = {
    "ok": "ออกจากปาร์ตี้แล้ว",
    "involved": "ออก/เตะไม่ได้ เพราะคนนี้มีรายการเกี่ยวข้องอยู่ (เป็นคนจ่าย หรือมีส่วนในรายการใดรายการหนึ่ง)",
    "not_member": "คนนี้ไม่ได้อยู่ในปาร์ตี้",
    "forbidden": "เฉพาะ host เท่านั้นที่เตะสมาชิกได้",
    "host": "host ออกจากปาร์ตี้หรือถูกเตะไม่ได้",
    "closed": "ปาร์ตี้นี้ปิดแล้ว",
    "not_found": "ไม่พบปาร์ตี้นี้",
}
SAVE_STATUS_TEXT = {
    "ok": "บันทึกแล้ว",
    "closed": "ปาร์ตี้นี้ปิดแล้ว เพิ่ม/แก้รายการไม่ได้",
    "forbidden": "แก้ได้เฉพาะคนที่เพิ่มรายการนี้ หรือ host",
    "invalid": "ข้อมูลไม่ถูกต้อง (คนจ่าย/ผู้มีส่วนต้องเป็นสมาชิก และต้องมียอด) ลองเปิดฟอร์มใหม่",
    "duplicate": "สลิปนี้ถูกบันทึกในปาร์ตี้แล้ว",
    "not_found": "ไม่พบรายการนี้ (อาจถูกยกเลิกหรือลบไปแล้ว)",
}
ERROR_TEXT = "เกิดข้อผิดพลาด ลองใหม่อีกครั้ง"
DRAFT_TTL = timedelta(hours=24)
LIST_LIMIT = 25  # รายการใน status / เมนูแก้ (เมนู Discord มีได้ 25 ตัวเลือก)


# =====================================================================
# helpers
# =====================================================================

def mention(user_id: str) -> str:
    return f"<@{user_id}>"


def baht(satang: int | None) -> str:
    return "?" if satang is None else f"฿{format_satang(satang)}"


def _store(interaction: discord.Interaction) -> PartyStore:
    return interaction.client.party_store  # type: ignore[attr-defined]


async def run(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """เรียก PartyStore (blocking) ใน thread แยก พร้อม timeout"""
    return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), DB_TIMEOUT)


async def reply(interaction: discord.Interaction, text: str, **kwargs: Any) -> None:
    """ตอบแบบ ephemeral ไม่ว่าจะตอบ interaction ไปแล้วหรือยัง"""
    kwargs.setdefault("ephemeral", True)
    if interaction.response.is_done():
        await interaction.followup.send(text, **kwargs)
    else:
        await interaction.response.send_message(text, **kwargs)


def guarded(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """จับ exception ใน callback แล้วแจ้งผู้ใช้แบบ ephemeral (แทน "This interaction failed")"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
        interaction = next(a for a in (*args, *kwargs.values()) if isinstance(a, discord.Interaction))
        try:
            await fn(*args, **kwargs)
        except Exception:
            log.exception("party: %s ล้มเหลว", fn.__name__)
            try:
                await reply(interaction, ERROR_TEXT)
            except discord.HTTPException:
                pass

    return wrapper


async def get_thread(bot: discord.Client, thread_id: str) -> discord.Thread | None:
    channel = bot.get_channel(int(thread_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(thread_id))
        except discord.HTTPException:
            return None
    return channel if isinstance(channel, discord.Thread) else None


async def party_here(interaction: discord.Interaction) -> dict | None:
    """ปาร์ตี้ของ thread ที่ใช้คำสั่งอยู่ — ไม่ใช่ thread ปาร์ตี้ → แจ้ง ephemeral แล้วคืน None"""
    channel = interaction.channel
    party = None
    if isinstance(channel, discord.Thread):
        party = await run(_store(interaction).party_by_thread, str(channel.id))
    if party is None:
        await reply(interaction, "ใช้คำสั่งนี้ใน thread ของปาร์ตี้")
    return party


# =====================================================================
# ข้อความเชิญ + ปุ่มเข้าร่วม
# =====================================================================

def invite_embed(party: dict, member_count: int) -> discord.Embed:
    is_open = party.get("status") == "open"
    embed = discord.Embed(
        title=f"🎉 {party['name']}",
        description=(f"ปาร์ตี้หารค่าใช้จ่าย โดย {mention(party['host_id'])}\n"
                     + ("กด **เข้าร่วม** หรือพิมพ์ `/party join` ตามด้วยโค้ด" if is_open else "**ปาร์ตี้นี้ปิดแล้ว**")),
        color=COLOR_OPEN if is_open else COLOR_CLOSED,
    )
    embed.add_field(name="โค้ด", value=f"`{party['code']}`")
    embed.add_field(name="สมาชิก", value=f"{member_count}/{MAX_MEMBERS}")
    return embed


class JoinButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"party:join:{UUID_RE}"):
    def __init__(self, target: str, *, disabled: bool = False) -> None:
        super().__init__(discord.ui.Button(
            label="ปิดแล้ว" if disabled else "เข้าร่วม", emoji="🙋", style=discord.ButtonStyle.success,
            custom_id=f"party:join:{target}", disabled=disabled,
        ))
        self.target = target

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: Any) -> JoinButton:
        return cls(match["target"])

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        party = await run(_store(interaction).get_party, self.target)
        if party is None:
            await reply(interaction, JOIN_STATUS_TEXT["not_found"])
            return
        await join_party(interaction, party)


def invite_view(party: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(JoinButton(party["id"], disabled=party.get("status") != "open"))
    return view


async def refresh_invite(bot: discord.Client, party: dict) -> None:
    """อัปเดตจำนวนสมาชิก/สถานะในข้อความเชิญ (ล้มก็ไม่เป็นไร)"""
    if not party.get("invite_message_id"):
        return
    try:
        channel = bot.get_channel(int(party["channel_id"])) or await bot.fetch_channel(int(party["channel_id"]))
        message = await channel.fetch_message(int(party["invite_message_id"]))  # type: ignore[union-attr]
        count = len(await run(bot.party_store.members, party["id"]))  # type: ignore[attr-defined]
        await message.edit(embed=invite_embed(party, count), view=invite_view(party))
    except (discord.HTTPException, AttributeError) as e:
        log.warning("อัปเดตข้อความเชิญไม่สำเร็จ party=%s: %s", party["id"], e)


async def join_party(interaction: discord.Interaction, party: dict) -> None:
    user_id = str(interaction.user.id)
    status = await run(_store(interaction).join, party["id"], user_id)
    if status != "ok":
        await reply(interaction, JOIN_STATUS_TEXT.get(status, ERROR_TEXT))
        return
    thread = await get_thread(interaction.client, party["thread_id"])
    if thread is None:
        await reply(interaction, "เข้าร่วมแล้ว แต่หา thread ของปาร์ตี้ไม่เจอ — แจ้ง host")
        return
    await thread.add_user(interaction.user)
    await thread.send(f"👋 {mention(user_id)} เข้าร่วมปาร์ตี้แล้ว")
    await reply(interaction, f"เข้าร่วม **{party['name']}** แล้ว → {thread.mention}")
    await refresh_invite(interaction.client, party)


# =====================================================================
# /party
# =====================================================================

party_group = app_commands.Group(name="party", description="ปาร์ตี้หารค่าใช้จ่าย", guild_only=True)


@party_group.command(name="create", description="สร้างปาร์ตี้ใหม่ (คุณเป็น host)")
@app_commands.rename(name="ชื่อ")
@app_commands.describe(name="ชื่อปาร์ตี้ เช่น ทริปเขาใหญ่")
@guarded
async def create_cmd(interaction: discord.Interaction, name: app_commands.Range[str, 1, 100]) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await reply(interaction, "ใช้คำสั่งนี้ในช่องข้อความปกติ (ไม่ใช่ใน thread)")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    name = name.strip()
    thread = await channel.create_thread(name=f"🎉 {name}"[:100], type=discord.ChannelType.private_thread,
                                         invitable=False)
    try:
        party = await run(_store(interaction).create_party, name=name, host_id=str(interaction.user.id),
                          guild_id=str(channel.guild.id), channel_id=str(channel.id), thread_id=str(thread.id))
    except Exception:
        await thread.delete()
        raise
    await thread.add_user(interaction.user)
    invite = await channel.send(embed=invite_embed(party, 1), view=invite_view(party))
    await run(_store(interaction).update_party, party["id"], invite_message_id=str(invite.id))
    await thread.send(
        f"🎉 ปาร์ตี้ **{name}** — host {mention(party['host_id'])}\n"
        "• ส่ง**รูปสลิป**ใน thread นี้เพื่อเพิ่มค่าใช้จ่าย หรือ `/party add` สำหรับเงินสด\n"
        "• `/party status` ดูรายการและยอดสุทธิ • `/party end` (host) ปิดปาร์ตี้และสรุปการโอน\n"
        f"• ชวนเพื่อน: กด **เข้าร่วม** ในช่อง {channel.mention} หรือใช้โค้ด `{party['code']}`"
    )
    await interaction.followup.send(f"สร้างปาร์ตี้แล้ว → {thread.mention}\nโค้ดเชิญ: `{party['code']}`", ephemeral=True)


@party_group.command(name="join", description="เข้าร่วมปาร์ตี้ด้วยโค้ด")
@app_commands.rename(code="โค้ด")
@app_commands.describe(code="โค้ด 6 ตัวที่ได้จาก host")
@guarded
async def join_cmd(interaction: discord.Interaction, code: app_commands.Range[str, 1, 20]) -> None:
    party = await run(_store(interaction).party_by_code, code)
    if party is None:
        await reply(interaction, "ไม่พบปาร์ตี้ที่ใช้โค้ดนี้")
        return
    await join_party(interaction, party)


@party_group.command(name="leave", description="ออกจากปาร์ตี้นี้ (ถ้ายังไม่มีรายการเกี่ยวข้อง)")
@guarded
async def leave_cmd(interaction: discord.Interaction) -> None:
    party = await party_here(interaction)
    if party is None:
        return
    await remove_member(interaction, party, interaction.user)


@party_group.command(name="kick", description="(host) เตะสมาชิกที่ยังไม่มีรายการเกี่ยวข้อง")
@app_commands.rename(member="สมาชิก")
@guarded
async def kick_cmd(interaction: discord.Interaction, member: discord.Member) -> None:
    party = await party_here(interaction)
    if party is None:
        return
    await remove_member(interaction, party, member)


async def remove_member(interaction: discord.Interaction, party: dict, member: discord.abc.User) -> None:
    actor_id, member_id = str(interaction.user.id), str(member.id)
    status = await run(_store(interaction).remove_member, party["id"], member_id, actor_id)
    if status != "ok":
        await reply(interaction, REMOVE_STATUS_TEXT.get(status, ERROR_TEXT))
        return
    thread = interaction.channel
    if isinstance(thread, discord.Thread):
        try:
            await thread.remove_user(member)
        except discord.HTTPException as e:
            log.warning("เอา %s ออกจาก thread ไม่สำเร็จ: %s", member_id, e)
        text = (f"🚪 {mention(member_id)} ออกจากปาร์ตี้แล้ว" if actor_id == member_id
                else f"🚪 host นำ {mention(member_id)} ออกจากปาร์ตี้แล้ว")
        await thread.send(text)
    await reply(interaction, "เรียบร้อย")
    await refresh_invite(interaction.client, party)



# =====================================================================
# รายการค่าใช้จ่าย: draft → ฟอร์มยืนยัน → confirmed
# =====================================================================

@dataclass
class FormState:
    can_confirm: bool
    warnings: list[str] = field(default_factory=list)
    shares: dict[str, int] = field(default_factory=dict)


def amount_to_satang(value: float | None) -> int | None:
    """ยอดจาก OCR (บาท, float) -> สตางค์ ; ไม่ใช่จำนวนบวก -> None"""
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    satang = int((Decimal(str(value)) * 100).to_integral_value())
    return satang if satang > 0 else None


def sanitize_draft(draft: dict, members: list[str]) -> dict:
    """ตัดคนที่ไม่ใช่สมาชิกแล้วออก (เช่นถูกเตะ) ; คนจ่ายหลุด → คนเพิ่มรายการ ; ผู้มีส่วนว่าง → ทุกคน"""
    out = dict(draft)
    participants = [m for m in (draft.get("participants") or []) if m in members]
    out["participants"] = participants or list(members)
    if out.get("payer_id") not in members:
        out["payer_id"] = draft["created_by"] if draft["created_by"] in members else members[0]
    return out


def is_expired(row: dict, now: datetime) -> bool:
    if row.get("status") != "draft":
        return False
    created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    return now - created > DRAFT_TTL


def form_state(draft: dict, *, duplicate: bool, expired: bool) -> FormState:
    warnings: list[str] = []
    amount = draft.get("amount_satang")
    shares = split(amount, draft["participants"]) if amount else {}
    if not amount:
        warnings.append("⚠️ อ่านยอดจากสลิปไม่ได้ — กด **แก้ยอด/หมายเหตุ** เพื่อกรอกยอด")
    if duplicate:
        warnings.append("⛔ " + SAVE_STATUS_TEXT["duplicate"])
    if expired:
        warnings.append("⌛ รายการนี้หมดอายุแล้ว (เกิน 24 ชั่วโมง) — ส่งสลิปใหม่อีกครั้ง")
    return FormState(can_confirm=bool(amount) and not duplicate and not expired, warnings=warnings, shares=shares)


def share_lines(shares: dict[str, int]) -> str:
    return "\n".join(f"{mention(m)} {baht(v)}" for m, v in shares.items()) or "–"


def expense_summary_embed(expense: dict, shares: dict[str, int]) -> discord.Embed:
    """สรุปรายการที่ยืนยันแล้ว (โพสต์ใน thread ให้ทุกคนเห็น)"""
    note = expense.get("note") or ("เงินสด" if expense.get("source") == "cash" else "ค่าใช้จ่าย")
    embed = discord.Embed(
        title=f"🧾 {baht(expense['amount_satang'])} — {note}",
        description=f"จ่ายโดย {mention(expense['payer_id'])} · เพิ่มโดย {mention(expense['created_by'])}",
        color=COLOR_DONE,
    )
    embed.add_field(name=f"หารกัน {len(shares)} คน", value=share_lines(shares), inline=False)
    if expense.get("slip_message_url"):
        embed.add_field(name="สลิป", value=f"[ดูสลิป]({expense['slip_message_url']})", inline=False)
    return embed


def form_embed(expense: dict, fs: FormState) -> discord.Embed:
    editing = expense.get("status") == "confirmed"
    embed = discord.Embed(
        title="✏️ แก้ไขรายการ (บันทึกทันทีที่เปลี่ยน)" if editing else "🧾 ตรวจรายการก่อนบันทึก",
        description="\n".join(fs.warnings) or None,
        color=COLOR_OPEN if fs.can_confirm or editing else discord.Color.orange(),
    )
    embed.add_field(name="ยอด", value=baht(expense.get("amount_satang")))
    embed.add_field(name="คนจ่าย", value=mention(expense["payer_id"]))
    embed.add_field(name="หมายเหตุ", value=expense.get("note") or "–")
    embed.add_field(name=f"ผู้มีส่วน ({len(expense['participants'])} คน)",
                    value=share_lines(fs.shares) if fs.shares else " ".join(mention(m) for m in expense["participants"]),
                    inline=False)
    return embed


_name_cache: dict[tuple[int, str], str] = {}


async def member_names(guild: discord.Guild | None, ids: list[str]) -> dict[str, str]:
    """ชื่อที่แสดงในเมนู (เมนู Discord แสดง mention ไม่ได้)"""
    names: dict[str, str] = {}
    for member_id in ids:
        key = (guild.id if guild else 0, member_id)
        if key not in _name_cache and guild is not None:
            member = guild.get_member(int(member_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(member_id))
                except discord.HTTPException:
                    member = None
            if member is not None:
                _name_cache[key] = member.display_name
        names[member_id] = _name_cache.get(key, f"ผู้ใช้ {member_id[-4:]}")
    return names


class _ExpenseItem:
    """ส่วนกลางของปุ่ม/เมนูที่ผูกกับรายการ (target = expense id)"""

    target: str

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any) -> Any:
        return cls(match["target"])  # type: ignore[call-arg]


class OpenDraftButton(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Button], template=rf"party:open:{UUID_RE}"):
    def __init__(self, target: str) -> None:
        super().__init__(discord.ui.Button(label="ตรวจ/ยืนยัน", emoji="📝", style=discord.ButtonStyle.primary,
                                           custom_id=f"party:open:{target}"))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = await load_form(interaction, self.target)
        if ctx is None:
            return
        expense, party, members = ctx
        if expense["status"] != "draft":
            await reply(interaction, "รายการนี้ยืนยันไปแล้ว — ใช้ `/party status` เพื่อแก้ไข")
            return
        embed, view = await render_form(interaction, expense, party, members)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class PayerSelect(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Select], template=rf"party:payer:{UUID_RE}"):
    def __init__(self, target: str, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(discord.ui.Select(custom_id=f"party:payer:{target}", placeholder="คนจ่าย",
                                           options=options or [], min_values=1, max_values=1, row=0))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        await apply_change(interaction, self.target, payer_id=values[0])


class SharesSelect(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Select], template=rf"party:shares:{UUID_RE}"):
    def __init__(self, target: str, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(discord.ui.Select(custom_id=f"party:shares:{target}", placeholder="ผู้มีส่วน (เลือกได้หลายคน)",
                                           options=options or [], min_values=1,
                                           max_values=max(1, len(options or [])), row=1))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        values = list((interaction.data or {}).get("values") or [])
        await apply_change(interaction, self.target, participants=values)


class AmountButton(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Button], template=rf"party:amount:{UUID_RE}"):
    def __init__(self, target: str) -> None:
        super().__init__(discord.ui.Button(label="แก้ยอด/หมายเหตุ", emoji="✏️", style=discord.ButtonStyle.secondary,
                                           custom_id=f"party:amount:{target}", row=2))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = await load_form(interaction, self.target)
        if ctx is None:
            return
        await interaction.response.send_modal(AmountNoteModal(ctx[0]))


class AmountNoteModal(discord.ui.Modal):
    def __init__(self, expense: dict) -> None:
        super().__init__(title="แก้ยอด / หมายเหตุ", timeout=15 * 60)
        self.expense_id = expense["id"]
        amount = expense.get("amount_satang")
        self.amount = discord.ui.TextInput(label="ยอด (บาท)", placeholder="เช่น 1,234.50", max_length=20,
                                           default=format_satang(amount).replace(",", "") if amount else None)
        self.note = discord.ui.TextInput(label="หมายเหตุ (ไม่ใส่ก็ได้)", placeholder="เช่น ค่าหมูกระทะ", required=False,
                                         max_length=100, default=expense.get("note") or None)
        self.add_item(self.amount)
        self.add_item(self.note)

    @guarded
    async def on_submit(self, interaction: discord.Interaction) -> None:
        satang = parse_baht(self.amount.value)
        if satang is None:
            await reply(interaction, "ยอดไม่ถูกต้อง ใส่ตัวเลขบวก ทศนิยมไม่เกิน 2 ตำแหน่ง เช่น `1,234.50`")
            return
        await apply_change(interaction, self.expense_id, amount_satang=satang, note=self.note.value.strip())


class ConfirmButton(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Button], template=rf"party:confirm:{UUID_RE}"):
    def __init__(self, target: str, *, disabled: bool = False) -> None:
        super().__init__(discord.ui.Button(label="ยืนยัน", emoji="✅", style=discord.ButtonStyle.success,
                                           custom_id=f"party:confirm:{target}", disabled=disabled, row=2))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = await load_form(interaction, self.target)
        if ctx is None:
            return
        expense, party, members = ctx
        draft = sanitize_draft(expense, members)
        fs = await current_form_state(interaction, draft, party)
        if not fs.can_confirm:
            await reply(interaction, "\n".join(fs.warnings) or "ยังยืนยันไม่ได้")
            return
        status = await run(_store(interaction).save_expense, expense["id"], str(interaction.user.id),
                           amount_satang=draft["amount_satang"], payer_id=draft["payer_id"],
                           note=draft.get("note") or "", shares=fs.shares)
        if status != "ok":
            await reply(interaction, SAVE_STATUS_TEXT.get(status, ERROR_TEXT))
            return
        await interaction.response.edit_message(content="✅ บันทึกแล้ว", embed=None, view=None)
        await publish_expense(interaction.client, party, expense["id"])


class CancelButton(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Button], template=rf"party:cancel:{UUID_RE}"):
    def __init__(self, target: str) -> None:
        super().__init__(discord.ui.Button(label="ยกเลิก", style=discord.ButtonStyle.secondary,
                                           custom_id=f"party:cancel:{target}", row=2))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = await load_form(interaction, self.target)
        if ctx is None:
            return
        expense, party, _ = ctx
        status = await run(_store(interaction).delete_expense, expense["id"], str(interaction.user.id))
        if status != "ok":
            await reply(interaction, SAVE_STATUS_TEXT.get(status, ERROR_TEXT))
            return
        await interaction.response.edit_message(content="ยกเลิกรายการนี้แล้ว", embed=None, view=None)
        await delete_thread_message(interaction.client, party, expense.get("summary_message_id"))


async def load_form(interaction: discord.Interaction, expense_id: str) -> tuple[dict, dict, list[str]] | None:
    """โหลดรายการ + ปาร์ตี้ + สมาชิก แล้วตรวจสิทธิ์ (คนเพิ่มรายการ หรือ host) — ไม่ผ่านแจ้ง ephemeral แล้วคืน None"""
    store = _store(interaction)
    expense = await run(store.get_expense, expense_id)
    if expense is None:
        await reply(interaction, SAVE_STATUS_TEXT["not_found"])
        return None
    party = await run(store.get_party, expense["party_id"])
    if party is None or party["status"] != "open":
        await reply(interaction, SAVE_STATUS_TEXT["closed"])
        return None
    user_id = str(interaction.user.id)
    if user_id not in (expense["created_by"], party["host_id"]):
        await reply(interaction, SAVE_STATUS_TEXT["forbidden"])
        return None
    members = await run(store.members, party["id"])
    return expense, party, members


async def current_form_state(interaction: discord.Interaction, expense: dict, party: dict) -> FormState:
    duplicate = await run(_store(interaction).is_duplicate_slip, party["id"], trans_ref=expense.get("trans_ref"),
                          image_sha256=expense.get("image_sha256"), exclude_expense_id=expense["id"])
    return form_state(expense, duplicate=duplicate, expired=is_expired(expense, datetime.now(timezone.utc)))


async def render_form(
    interaction: discord.Interaction, expense: dict, party: dict, members: list[str]
) -> tuple[discord.Embed, discord.ui.View]:
    expense = sanitize_draft(expense, members)
    fs = await current_form_state(interaction, expense, party)
    names = await member_names(interaction.guild, members)
    view = discord.ui.View(timeout=None)
    view.add_item(PayerSelect(expense["id"], [
        discord.SelectOption(label=names[m], value=m, default=m == expense["payer_id"]) for m in members
    ]))
    view.add_item(SharesSelect(expense["id"], [
        discord.SelectOption(label=names[m], value=m, default=m in expense["participants"]) for m in members
    ]))
    view.add_item(AmountButton(expense["id"]))
    if expense["status"] == "draft":
        view.add_item(ConfirmButton(expense["id"], disabled=not fs.can_confirm))
        view.add_item(CancelButton(expense["id"]))
    else:
        for item in edit_mode_items(expense["id"]):
            view.add_item(item)
    return form_embed(expense, fs), view


def edit_mode_items(expense_id: str) -> list[discord.ui.Item[Any]]:
    """ปุ่มเพิ่มเติมตอนแก้รายการที่ยืนยันแล้ว"""
    return [DeleteButton(expense_id)]


async def apply_change(interaction: discord.Interaction, expense_id: str, **changes: Any) -> None:
    """เปลี่ยนคนจ่าย / ผู้มีส่วน / ยอด / หมายเหตุ แล้ววาดฟอร์มใหม่
    draft → เขียนลง draft ; รายการที่ยืนยันแล้ว → บันทึกใหม่ทันที (ตรวจทุกอย่างในฐานข้อมูล)"""
    ctx = await load_form(interaction, expense_id)
    if ctx is None:
        return
    expense, party, members = ctx
    store = _store(interaction)
    updated = sanitize_draft({**expense, **changes}, members)
    if expense["status"] == "draft":
        row = await run(store.update_draft, expense_id, payer_id=updated["payer_id"],
                        participants=updated["participants"], amount_satang=updated.get("amount_satang"),
                        note=updated.get("note") or "")
        if row is None:
            await reply(interaction, "รายการนี้ยืนยันหรือยกเลิกไปแล้ว")
            return
    else:
        status = await run(store.save_expense, expense_id, str(interaction.user.id),
                           amount_satang=updated["amount_satang"], payer_id=updated["payer_id"],
                           note=updated.get("note") or "", shares=split(updated["amount_satang"], updated["participants"]))
        if status != "ok":
            await reply(interaction, SAVE_STATUS_TEXT.get(status, ERROR_TEXT))
            return
    fresh = await run(store.get_expense, expense_id)
    embed, view = await render_form(interaction, fresh or updated, party, members)
    await interaction.response.edit_message(embed=embed, view=view)
    if expense["status"] == "confirmed":
        await publish_expense(interaction.client, party, expense_id)


def shares_of(expense: dict) -> dict[str, int]:
    rows = expense.get("party_expense_shares") or []
    return {r["discord_id"]: int(r["share_satang"]) for r in sorted(rows, key=lambda r: int(r["discord_id"]))}


async def publish_expense(bot: discord.Client, party: dict, expense_id: str) -> None:
    """โพสต์/อัปเดตสรุปรายการใน thread (แทนข้อความ "พบสลิป" ถ้ามี)"""
    store: PartyStore = bot.party_store  # type: ignore[attr-defined]
    expense = await run(store.get_expense, expense_id)
    thread = await get_thread(bot, party["thread_id"])
    if expense is None or thread is None:
        return
    embed = expense_summary_embed(expense, shares_of(expense))
    if expense.get("summary_message_id"):
        try:
            message = await thread.fetch_message(int(expense["summary_message_id"]))
            await message.edit(content=None, embed=embed, view=None)
            return
        except discord.HTTPException:
            pass
    message = await thread.send(embed=embed)
    await run(store.update_expense_message, expense_id, str(message.id))


async def delete_thread_message(bot: discord.Client, party: dict, message_id: str | None) -> None:
    if not message_id:
        return
    thread = await get_thread(bot, party["thread_id"])
    if thread is None:
        return
    try:
        await thread.get_partial_message(int(message_id)).delete()
    except discord.HTTPException as e:
        log.warning("ลบข้อความ %s ไม่สำเร็จ: %s", message_id, e)


@party_group.command(name="add", description="เพิ่มค่าใช้จ่ายแบบไม่มีสลิป (เช่น จ่ายเงินสด)")
@app_commands.rename(amount="ยอด", note="หมายเหตุ")
@app_commands.describe(amount="ยอดเงิน (บาท) เช่น 250 หรือ 1,234.50", note="เช่น ค่าหมูกระทะ")
@guarded
async def add_cmd(interaction: discord.Interaction, amount: app_commands.Range[str, 1, 20],
                  note: app_commands.Range[str, 0, 100] = "") -> None:
    party = await party_here(interaction)
    if party is None:
        return
    if party["status"] != "open":
        await reply(interaction, SAVE_STATUS_TEXT["closed"])
        return
    satang = parse_baht(amount)
    if satang is None:
        await reply(interaction, "ยอดไม่ถูกต้อง ใส่ตัวเลขบวก ทศนิยมไม่เกิน 2 ตำแหน่ง เช่น `1,234.50`")
        return
    store = _store(interaction)
    members = await run(store.members, party["id"])
    user_id = str(interaction.user.id)
    if user_id not in members:
        await reply(interaction, "เฉพาะสมาชิกปาร์ตี้เท่านั้นที่เพิ่มรายการได้")
        return
    draft = await run(store.create_draft, party["id"], created_by=user_id, payer_id=user_id, participants=members,
                      amount_satang=satang, note=note.strip(), source="cash")
    embed, view = await render_form(interaction, draft, party, members)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def handle_party_slips(bot: SlipBot, message: discord.Message, party: dict) -> None:
    """รูปสลิปใน thread ปาร์ตี้ → อ่านยอด → สร้าง draft → ปุ่ม [ตรวจ/ยืนยัน] ให้คนอัป"""
    images = [a for a in message.attachments if is_image_attachment(a.filename, a.content_type)]
    if not images:
        return
    store = bot.party_store
    user_id = str(message.author.id)
    if party["status"] != "open":
        await message.reply("ปาร์ตี้นี้ปิดแล้ว เพิ่มรายการไม่ได้", mention_author=False)
        return
    members = await run(store.members, party["id"])
    if user_id not in members:
        await message.reply("เฉพาะสมาชิกปาร์ตี้เท่านั้นที่เพิ่มรายการได้", mention_author=False)
        return
    for attachment in images:
        data = await attachment.read()
        sha = hashlib.sha256(data).hexdigest()
        qr = await asyncio.to_thread(slip_qr.read_slip_qr, data) or {}
        trans_ref = qr.get("trans_ref") or None
        if await run(store.is_duplicate_slip, party["id"], trans_ref=trans_ref, image_sha256=sha):
            await message.reply("⛔ " + SAVE_STATUS_TEXT["duplicate"], mention_author=False)
            continue
        async with message.channel.typing(), bot._ocr_slot:
            slip = await asyncio.to_thread(slip_reader.read_slip, data, bot.cfg.typhoon_api_key,
                                           bot.cfg.typhoon_llm_model, guess_mime(attachment.filename, attachment.content_type))
        amount = amount_to_satang(slip.amount)
        draft = await run(store.create_draft, party["id"], created_by=user_id, payer_id=user_id, participants=members,
                          amount_satang=amount, note="", source="slip", trans_ref=trans_ref, image_sha256=sha,
                          slip_message_url=message.jump_url)
        text = f"พบสลิป {baht(amount)}" if amount else "พบสลิป แต่อ่านยอดไม่ได้"
        view = discord.ui.View(timeout=None)
        view.add_item(OpenDraftButton(draft["id"]))
        sent = await message.reply(f"🧾 {text} — {mention(user_id)} กดปุ่มเพื่อตรวจและยืนยัน", view=view,
                                   mention_author=False)
        await run(store.update_expense_message, draft["id"], str(sent.id))



# =====================================================================
# /party status + แก้ / ลบรายการ
# =====================================================================

def expenses_from_rows(rows: list[dict]) -> list[Expense]:
    """แถว confirmed จาก DB → party_math.Expense (ผู้มีส่วน = คนที่มี share บันทึกไว้)"""
    return [Expense(r["payer_id"], int(r["amount_satang"]), tuple(shares_of(r))) for r in rows]


def signed(satang: int) -> str:
    return f"+{format_satang(satang)}" if satang > 0 else format_satang(satang)


def balance_lines(rows: list[dict], members: list[str]) -> str:
    bal = balances(members, expenses_from_rows(rows))
    return "\n".join(
        f"{mention(m)} จ่าย {format_satang(b.paid)} · ส่วน {format_satang(b.owed)} · **{signed(b.net)}**"
        for m, b in sorted(bal.items(), key=lambda kv: (-kv[1].net, int(kv[0])))
    )


def expense_line(r: dict) -> str:
    note = r.get("note") or ("เงินสด" if r.get("source") == "cash" else "ค่าใช้จ่าย")
    return f"`{baht(r['amount_satang'])}` {note} — จ่ายโดย {mention(r['payer_id'])} · หาร {len(shares_of(r))} คน"


def status_embed(party: dict, rows: list[dict], members: list[str]) -> discord.Embed:
    total = sum(int(r["amount_satang"]) for r in rows)
    shown = rows[-LIST_LIMIT:]
    lines = [expense_line(r) for r in shown]
    if len(rows) > len(shown):
        lines.insert(0, f"… และอีก {len(rows) - len(shown)} รายการก่อนหน้า")
    embed = discord.Embed(
        title=f"📊 {party['name']}",
        description="\n".join(lines) if rows else "ยังไม่มีรายการ — ส่งรูปสลิปใน thread นี้ หรือใช้ `/party add`",
        color=COLOR_OPEN if party.get("status") == "open" else COLOR_CLOSED,
    )
    embed.add_field(name="ยอดรวม", value=baht(total))
    embed.add_field(name="รายการ", value=str(len(rows)))
    embed.add_field(name="ยอดสุทธิ (บวก = ได้คืน, ลบ = ต้องโอน)", value=balance_lines(rows, members) or "–",
                    inline=False)
    return embed


def editable_rows(rows: list[dict], viewer_id: str, host_id: str) -> list[dict]:
    """รายการที่คนนี้แก้ได้ (คนเพิ่มรายการ หรือ host) ใหม่สุดก่อน ไม่เกิน 25"""
    mine = [r for r in rows if viewer_id == host_id or r["created_by"] == viewer_id]
    return list(reversed(mine))[:LIST_LIMIT]


@party_group.command(name="status", description="ดูรายการทั้งหมดและยอดสุทธิของแต่ละคน")
@guarded
async def status_cmd(interaction: discord.Interaction) -> None:
    party = await party_here(interaction)
    if party is None:
        return
    store = _store(interaction)
    rows = await run(store.confirmed_expenses, party["id"])
    members = await run(store.members, party["id"])
    view = discord.ui.View(timeout=None)
    if party["status"] == "open":
        view.add_item(EditMenuButton(party["id"]))
    await interaction.response.send_message(embed=status_embed(party, rows, members), view=view)


class EditMenuButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"party:editmenu:{UUID_RE}"):
    def __init__(self, target: str) -> None:
        super().__init__(discord.ui.Button(label="แก้/ลบรายการ", emoji="✏️", style=discord.ButtonStyle.secondary,
                                           custom_id=f"party:editmenu:{target}"))
        self.target = target

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: Any) -> EditMenuButton:
        return cls(match["target"])

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        store = _store(interaction)
        party = await run(store.get_party, self.target)
        if party is None or party["status"] != "open":
            await reply(interaction, SAVE_STATUS_TEXT["closed"])
            return
        rows = editable_rows(await run(store.confirmed_expenses, party["id"]), str(interaction.user.id),
                             party["host_id"])
        if not rows:
            await reply(interaction, "ไม่มีรายการที่คุณแก้ได้ (แก้ได้เฉพาะรายการที่คุณเพิ่ม หรือถ้าคุณเป็น host)")
            return
        names = await member_names(interaction.guild, sorted({r["payer_id"] for r in rows}))
        options = [
            discord.SelectOption(label=f"{baht(r['amount_satang'])} — {r.get('note') or 'ค่าใช้จ่าย'}"[:100],
                                 description=f"จ่ายโดย {names[r['payer_id']]}"[:100], value=r["id"])
            for r in rows
        ]
        view = discord.ui.View(timeout=None)
        view.add_item(PickExpenseSelect(party["id"], options))
        await interaction.response.send_message("เลือกรายการที่จะแก้หรือลบ", view=view, ephemeral=True)


class PickExpenseSelect(discord.ui.DynamicItem[discord.ui.Select], template=rf"party:pick:{UUID_RE}"):
    def __init__(self, target: str, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(discord.ui.Select(custom_id=f"party:pick:{target}", placeholder="เลือกรายการ",
                                           options=options or [], min_values=1, max_values=1))
        self.target = target

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match: Any) -> PickExpenseSelect:
        return cls(match["target"])

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        ctx = await load_form(interaction, values[0])
        if ctx is None:
            return
        embed, view = await render_form(interaction, *ctx)
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class DeleteButton(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Button], template=rf"party:delete:{UUID_RE}"):
    def __init__(self, target: str) -> None:
        super().__init__(discord.ui.Button(label="ลบรายการ", emoji="🗑️", style=discord.ButtonStyle.danger,
                                           custom_id=f"party:delete:{target}", row=2))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = await load_form(interaction, self.target)
        if ctx is None:
            return
        view = discord.ui.View(timeout=None)
        view.add_item(DeleteConfirmButton(self.target))
        await interaction.response.edit_message(
            content=f"ลบรายการ {expense_line(ctx[0])} จริงไหม? ลบแล้วกู้คืนไม่ได้", embed=None, view=view)


class DeleteConfirmButton(_ExpenseItem, discord.ui.DynamicItem[discord.ui.Button], template=rf"party:delok:{UUID_RE}"):
    def __init__(self, target: str) -> None:
        super().__init__(discord.ui.Button(label="ยืนยันลบ", style=discord.ButtonStyle.danger,
                                           custom_id=f"party:delok:{target}"))
        self.target = target

    @guarded
    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = await load_form(interaction, self.target)
        if ctx is None:
            return
        expense, party, _ = ctx
        status = await run(_store(interaction).delete_expense, expense["id"], str(interaction.user.id))
        if status != "ok":
            await reply(interaction, SAVE_STATUS_TEXT.get(status, ERROR_TEXT))
            return
        await interaction.response.edit_message(content="🗑️ ลบรายการแล้ว", embed=None, view=None)
        await delete_thread_message(interaction.client, party, expense.get("summary_message_id"))
        thread = await get_thread(interaction.client, party["thread_id"])
        if thread is not None:
            await thread.send(f"🗑️ {mention(str(interaction.user.id))} ลบรายการ {expense_line(expense)}")


# =====================================================================
# ลงทะเบียนกับบอท
# =====================================================================

DYNAMIC_ITEMS: list[type[discord.ui.DynamicItem[Any]]] = [
    JoinButton, OpenDraftButton, PayerSelect, SharesSelect, AmountButton, ConfirmButton, CancelButton,
    EditMenuButton, PickExpenseSelect, DeleteButton, DeleteConfirmButton,
]


def setup(bot: SlipBot, store: PartyStore) -> None:
    bot.party_store = store
    bot.tree.add_command(party_group)
    bot.add_dynamic_items(*DYNAMIC_ITEMS)


async def sync_commands(bot: SlipBot) -> None:
    """ลงทะเบียน /party เฉพาะเซิร์ฟเวอร์ของช่องหลัก (ขึ้นทันที ไม่ต้องรอแบบ global)"""
    channel = bot.get_channel(bot.cfg.discord_channel_id)
    guild = getattr(channel, "guild", None)
    if guild is None:
        log.warning("หาเซิร์ฟเวอร์ของ DISCORD_CHANNEL_ID ไม่เจอ — sync /party แบบ global (อาจใช้เวลาถึง 1 ชม.)")
        await bot.tree.sync()
        return
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    log.info("ลงทะเบียนคำสั่ง %d ชุดในเซิร์ฟเวอร์ %s", len(synced), guild.name)


async def handle_message(bot: SlipBot, message: discord.Message) -> bool:
    """ข้อความใน thread ปาร์ตี้ → True (จัดการแล้ว ห้ามเข้า flow รายจ่ายส่วนตัว)"""
    if not isinstance(message.channel, discord.Thread):
        return False
    party = await run(bot.party_store.party_by_thread, str(message.channel.id))
    if party is None:
        return False
    try:
        await handle_party_slips(bot, message, party)
    except Exception:
        log.exception("ประมวลผลสลิปในปาร์ตี้ไม่สำเร็จ")
        await message.reply("ประมวลผลสลิปไม่สำเร็จ ลองส่งใหม่อีกครั้ง", mention_author=False)
    return True
