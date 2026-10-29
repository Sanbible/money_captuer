"""ปาร์ตี้หารค่าใช้จ่ายในบอท Discord — slash commands `/party …` + ปุ่ม/เมนูแบบ persistent

state ทั้งหมดอยู่ใน Supabase (party_store) — ปุ่ม/เมนูทุกตัวเป็น DynamicItem ที่ฝัง id ไว้ใน custom_id
(`party:<action>:<uuid>`) แล้วอ่านข้อมูลจาก DB ใหม่ทุกครั้งที่กด จึงใช้ได้แม้บอทรีสตาร์ท
เงินเป็นสตางค์ (int) — คำนวณด้วย party_math
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

import discord
from discord import app_commands

from party_math import format_satang
from party_store import MAX_MEMBERS, PartyStore

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
ERROR_TEXT = "เกิดข้อผิดพลาด ลองใหม่อีกครั้ง"


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
# ลงทะเบียนกับบอท
# =====================================================================

DYNAMIC_ITEMS: list[type[discord.ui.DynamicItem[Any]]] = [JoinButton]


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
    return party is not None
