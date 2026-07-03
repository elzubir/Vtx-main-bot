import io
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

# Centralized colors
EMBED_COLOR = 0x27272F
GREEN = 0x57F287
RED = 0xED4245
YELLOW = 0xFEE75C
PINK = 0xEB459E

CONFIG_FILE = "ticket_config.json"
ACTIVE_FILE = "active_tickets.json"
MEDIA_FILE = "ticket_media.json"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

_cfg_defaults = {
    "review_channel": None,
    "stats_channel": None,
    "ticket_category": None,
    "member_role": None,
    "support_role": None,
    "community_role": None,
    "extra_roles": [],
    "ticket_counter": 0,
    "panel_title": "Support Tickets",
    "panel_description": "Click the button below to open a support ticket.",
    "panel_color": "27272F",
}


# -----------------------
# Safe JSON helpers
# -----------------------

def _load(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.exception("Failed to load JSON from %s", path)
            return dict(default)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4)
    except Exception:
        log.exception("Failed to write default JSON to %s", path)
    return dict(default)


def _save(path: str, obj) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=4)
    except Exception:
        log.exception("Failed to save JSON to %s", path)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -----------------------
# Embed helpers & error reporter
# -----------------------

def build_embed(title: str, description: str = "", *, color: int = EMBED_COLOR, ctx=None, success: bool = False, warning: bool = False, error: bool = False) -> discord.Embed:
    if error:
        color = RED
    elif warning:
        color = YELLOW
    elif success:
        color = GREEN
    embed = discord.Embed(title=title, description=description, color=color, timestamp=utcnow())
    try:
        if isinstance(ctx, commands.Context):
            embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        elif isinstance(ctx, discord.Interaction):
            embed.set_footer(text=f"Requested by {ctx.user}", icon_url=ctx.user.display_avatar.url)
    except Exception:
        pass
    return embed


def fmt_dt(dt: Optional[datetime], style: str = "F") -> str:
    return discord.utils.format_dt(dt, style) if dt else "Unknown"


def command_error_reporter(func):
    import functools

    @functools.wraps(func)
    async def wrapper(self, ctx, *args, **kwargs):
        try:
            return await func(self, ctx, *args, **kwargs)
        except Exception as exc:
            where = f"{self.__class__.__name__}.{func.__name__}"
            log.exception("Unhandled exception in %s", where, exc_info=exc)
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            short_tb = tb if len(tb) < 400 else tb[:397] + "..."
            embed = build_embed(
                "Internal Error",
                f"An error occurred in `{where}`:\n```py\n{short_tb}\n```",
                error=True,
                ctx=ctx if isinstance(ctx, (commands.Context, discord.Interaction)) else None,
            )
            try:
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done():
                        await ctx.followup.send(embed=embed, ephemeral=True)
                    else:
                        await ctx.response.send_message(embed=embed, ephemeral=True)
                else:
                    await ctx.send(embed=embed)
            except Exception:
                log.exception("Failed to deliver error embed to user for %s", where)
            return None

    return wrapper


# -----------------------
# Load persistent state
# -----------------------
cfg = _load(CONFIG_FILE, _cfg_defaults)
media_store = _load(MEDIA_FILE, {})
active_tickets: Dict[int, dict] = {}


def save_cfg():
    _save(CONFIG_FILE, cfg)


def save_media():
    _save(MEDIA_FILE, media_store)


def save_active():
    _save(ACTIVE_FILE, {
        str(k): {
            "user": v["user"],
            "claimed_by": v.get("claimed_by"),
            "media_count": v.get("media_count", 0),
            "ticket_num": v.get("ticket_num", 0),
            "priority": v.get("priority", "normal"),
            "opened_at": v.get("opened_at", ""),
        }
        for k, v in active_tickets.items()
    })


def next_num() -> int:
    cfg["ticket_counter"] = cfg.get("ticket_counter", 0) + 1
    save_cfg()
    return cfg["ticket_counter"]


# -----------------------
# Transcript/builders
# -----------------------
async def build_transcript(channel: discord.TextChannel) -> discord.File:
    lines = [f"# Transcript — {channel.name}", ""]
    async for msg in channel.history(limit=500, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = msg.content or ""
        for emb in msg.embeds:
            content += f" [EMBED: {emb.title or ''} | {(emb.description or '')[:80]}]"
        for att in msg.attachments:
            content += f" [FILE: {att.filename} | {att.url}]"
        lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {content}")
    return discord.File(io.BytesIO("\n".join(lines).encode()), filename=f"{channel.name}-transcript.txt")


# -----------------------
# Duration helper
# -----------------------

def calc_duration(opened_at: str) -> str:
    try:
        delta = utcnow() - datetime.fromisoformat(opened_at)
        m, s = divmod(int(delta.total_seconds()), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s"
    except Exception:
        return "Unknown"


# -----------------------
# Forward images to stats
# -----------------------
async def forward_images_to_stats(channel: discord.TextChannel, guild: discord.Guild):
    stats_ch_id = cfg.get("stats_channel")
    if not stats_ch_id:
        return
    stats_ch = guild.get_channel(int(stats_ch_id)) if stats_ch_id else None
    if not stats_ch:
        return
    # Collect images by user
    user_images: Dict[int, Dict[str, list]] = {}
    async for msg in channel.history(limit=500, oldest_first=True):
        for att in msg.attachments:
            ext = os.path.splitext(att.filename)[1].lower()
            if ext in IMAGE_EXTS:
                uid = msg.author.id
                user_images.setdefault(uid, {"user": msg.author, "images": []})["images"].append(att.url)
    for uid, data in user_images.items():
        user = data["user"]
        for img_url in data["images"]:
            embed = build_embed(f"📸 Image from {user}", color=EMBED_COLOR)
            embed.set_image(url=img_url)
            embed.set_footer(text=f"Ticket: #{channel.name}")
            try:
                if user.avatar:
                    embed.set_author(name=str(user), icon_url=user.display_avatar.url)
            except Exception:
                pass
            try:
                await stats_ch.send(embed=embed)
            except Exception:
                log.exception("Failed to forward image to stats channel")


# -----------------------
# Close ticket
# -----------------------
async def close_ticket(channel: discord.TextChannel, reason: str = "Closed", closer: Optional[discord.User] = None, verdict: str = "closed"):
    cid = channel.id
    tdata = active_tickets.pop(cid, None)
    save_active()

    if tdata:
        member = channel.guild.get_member(tdata["user"])
        files = media_store.get(str(cid), {}).get("files", [])
        duration = calc_duration(tdata.get("opened_at", ""))

        # Forward images to stats channel
        await forward_images_to_stats(channel, channel.guild)

        # Review channel notification
        review_ch_id = cfg.get("review_channel")
        if review_ch_id:
            review_ch = channel.guild.get_channel(int(review_ch_id))
            if review_ch:
                colors = {"accepted": GREEN, "rejected": RED, "closed": EMBED_COLOR}
                icons = {"accepted": "✅", "rejected": "❌", "closed": "🔒"}
                embed = discord.Embed(
                    title=f"{icons.get(verdict, '🔒')} Ticket #{tdata.get('ticket_num', 0):04d} — {verdict.upper()}",
                    color=colors.get(verdict, EMBED_COLOR),
                    timestamp=utcnow(),
                )
                embed.add_field(name="👤 User", value=f"{member} ({member.id})" if member else str(tdata.get("user")), inline=True)
                embed.add_field(name="🛡️ Actioned By", value=f"{closer} ({closer.id})" if closer else "System", inline=True)
                embed.add_field(name="⏱️ Duration", value=duration, inline=True)
                embed.add_field(name="📌 Verdict", value=verdict.upper(), inline=True)
                embed.add_field(name="📋 Reason", value=reason[:300], inline=True)
                embed.add_field(name="🖼️ Media Saved", value=f"{len(files)} file(s)", inline=True)
                opened_at = tdata.get("opened_at", "")
                embed.add_field(name="🕒 Opened At", value=(opened_at[:19].replace("T", " ") + " UTC") if opened_at else "Unknown", inline=False)
                try:
                    if member and member.display_avatar:
                        embed.set_thumbnail(url=member.display_avatar.url)
                except Exception:
                    pass
                transcript = await build_transcript(channel)
                try:
                    await review_ch.send(embed=embed, file=transcript)
                except Exception:
                    log.exception("Failed to send review embed/transcript")

        # Role handling
        if member:
            if verdict == "accepted":
                member_role_id = cfg.get("member_role")
                community_role_id = cfg.get("community_role")
                if member_role_id:
                    mrole = channel.guild.get_role(int(member_role_id))
                    if mrole:
                        try:
                            await member.add_roles(mrole, reason="Ticket accepted — verified")
                        except Exception:
                            log.exception("Failed to add member role")
                if community_role_id:
                    crole = channel.guild.get_role(int(community_role_id))
                    if crole and crole in member.roles:
                        try:
                            await member.remove_roles(crole, reason="Replaced by Member role")
                        except Exception:
                            log.exception("Failed to remove community role")
            elif verdict == "rejected":
                # No automatic punitive actions in this implementation.
                pass

    # Notify and delete
    try:
        await channel.send(embed=build_embed("⏳ Closing", "Closing in 5s...", color=RED))
    except Exception:
        pass
    await discord.utils.sleep_until(utcnow() + discord.timedelta(seconds=5)) if False else None
    # fallback simple sleep
    import asyncio
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=reason)
    except Exception:
        log.exception("Failed to delete ticket channel %s", cid)


# -----------------------
# Open ticket button view
# -----------------------
class OpenTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.success, emoji="🎫", custom_id="open_ticket_v4")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        # Prevent duplicate tickets per user
        for cid, t in active_tickets.items():
            if t.get("user") == interaction.user.id:
                ch = guild.get_channel(cid)
                if ch:
                    return await interaction.response.send_message(f"You already have a ticket: {ch.mention}", ephemeral=True)

        cat_id = cfg.get("ticket_category")
        category = guild.get_channel(int(cat_id)) if cat_id else None
        num = next_num()

        bot_member = guild.get_member(interaction.client.user.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        support_id = cfg.get("support_role")
        if support_id:
            role = guild.get_role(int(support_id))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        for rid in cfg.get("extra_roles", []):
            try:
                role = guild.get_role(int(rid))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True)
            except Exception:
                continue

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{num:04d}", category=category,
                overwrites=overwrites, topic=f"Ticket #{num:04d} | {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Missing permissions to create channel.", ephemeral=True)
        except Exception:
            log.exception("Failed to create ticket channel")
            return await interaction.response.send_message("❌ Failed to create ticket channel.", ephemeral=True)

        active_tickets[channel.id] = {
            "user": interaction.user.id,
            "claimed_by": None,
            "media_count": 0,
            "ticket_num": num,
            "priority": "normal",
            "opened_at": utcnow().isoformat(),
        }
        save_active()

        embed = build_embed(f"🎫 Ticket #{num:04d}", f"Welcome {interaction.user.mention}!\nStaff will be with you shortly.\n\nDescribe your issue below.", ctx=interaction)
        embed.set_footer(text="Use the buttons below to manage this ticket.")
        try:
            await channel.send(embed=embed, view=TicketControlView())
        except Exception:
            log.exception("Failed to send ticket control panel")
        await interaction.response.send_message(f"✅ Ticket opened: {channel.mention}", ephemeral=True)


# -----------------------
# Ticket control view
# -----------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _ensure_active(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id not in active_tickets:
            await interaction.response.send_message("❌ Not an active ticket.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Accept / Member", style=discord.ButtonStyle.success, custom_id="ticket_accept_v4")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_active(interaction):
            return
        await interaction.response.send_message("✅ Accepted — assigning member role and closing…", ephemeral=True)
        await close_ticket(interaction.channel, reason="Accepted by staff", closer=interaction.user, verdict="accepted")

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger, custom_id="ticket_reject_v4")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_active(interaction):
            return
        await interaction.response.send_message("❌ Rejected — closing ticket…", ephemeral=True)
        await close_ticket(interaction.channel, reason="Rejected by staff", closer=interaction.user, verdict="rejected")

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.secondary, custom_id="ticket_close_v4")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_active(interaction):
            return
        await interaction.response.send_message("Closing…", ephemeral=True)
        await close_ticket(interaction.channel, reason="Closed via button", closer=interaction.user, verdict="closed")

    @discord.ui.button(label="✋ Claim", style=discord.ButtonStyle.primary, custom_id="ticket_claim_v4")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = interaction.channel_id
        if cid not in active_tickets:
            return await interaction.response.send_message("Not an active ticket.", ephemeral=True)
        active_tickets[cid]["claimed_by"] = interaction.user.id
        save_active()
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name[:15]}"
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        try:
            await interaction.followup.send(embed=build_embed("✋ Ticket Claimed", f"{interaction.user.mention} claimed this ticket."), ephemeral=True)
        except Exception:
            pass

    @discord.ui.button(label="🔴 High Priority", style=discord.ButtonStyle.secondary, custom_id="ticket_priority_v4")
    async def priority_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = interaction.channel_id
        if cid not in active_tickets:
            return await interaction.response.send_message("Not an active ticket.", ephemeral=True)
        current = active_tickets[cid].get("priority", "normal")
        new_p = "high" if current != "high" else "normal"
        active_tickets[cid]["priority"] = new_p
        save_active()
        # Toggle button style and inform
        try:
            button.style = discord.ButtonStyle.danger if new_p == "high" else discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        try:
            await interaction.followup.send(embed=build_embed("Priority Updated", f"Priority → **{new_p.upper()}**", ctx=interaction), ephemeral=True)
        except Exception:
            pass


# -----------------------
# Save media handler
# -----------------------
async def do_save(interaction: discord.Interaction):
    cid = interaction.channel_id
    if cid not in active_tickets:
        return await interaction.response.send_message("❌ Not a ticket.", ephemeral=True)
    ticket = active_tickets[cid]
    ticket_num = ticket.get("ticket_num", 0)
    member = interaction.guild.get_member(ticket["user"]) if interaction.guild else None
    key = str(cid)
    existing = {e["orig_url"] for e in media_store.get(key, {}).get("files", [])}
    await interaction.response.defer(ephemeral=True)
    saved = 0
    async for msg in interaction.channel.history(limit=1000, oldest_first=True):
        for att in msg.attachments:
            ext = os.path.splitext(att.filename)[1].lower()
            kind = "image" if ext in IMAGE_EXTS else ("video" if ext in VIDEO_EXTS else None)
            if kind and att.url not in existing:
                media_store.setdefault(key, {"user_id": member.id if member else 0, "user_tag": str(member or "unknown"), "ticket_num": ticket_num, "files": []})
                media_store[key]["files"].append({
                    "filename": att.filename,
                    "type": kind,
                    "url": att.url,
                    "orig_url": att.url,
                    "saved_at": utcnow().isoformat(),
                })
                existing.add(att.url)
                saved += 1
    save_media()
    ticket["media_count"] = len(media_store.get(key, {}).get("files", []))
    save_active()
    try:
        await interaction.followup.send(embed=build_embed("💾 Save Complete", f"**{saved}** new file(s) — **{ticket['media_count']}** total.", success=True, ctx=interaction), ephemeral=True)
    except Exception:
        pass


# -----------------------
# Cog
# -----------------------
class TicketCog(commands.Cog, name="Tickets"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._backup_loop.start()

    def cog_unload(self):
        self._backup_loop.cancel()

    @tasks.loop(minutes=2)
    async def _backup_loop(self):
        save_active()
        save_media()

    @_backup_loop.before_loop
    async def _before_backup(self):
        await self.bot.wait_until_ready()

    @command_error_reporter
    @commands.hybrid_command(name="ticket-panel", description="Send the ticket panel (admin only)")
    @app_commands.describe(channel="Channel to send panel to (defaults to current)")
    @commands.has_guild_permissions(administrator=True)
    async def ticket_panel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        target = channel or ctx.channel
        title = cfg.get("panel_title", "Support Tickets")
        desc = cfg.get("panel_description", "Click the button below to open a support ticket.")
        try:
            color = int(cfg.get("panel_color", "27272F"), 16)
        except Exception:
            color = EMBED_COLOR
        embed = build_embed(f"🎫 {title}", desc, color=color, ctx=ctx)
        try:
            if ctx.guild and ctx.guild.icon:
                embed.set_thumbnail(url=ctx.guild.icon.url)
        except Exception:
            pass
        try:
            await target.send(embed=embed, view=OpenTicketView())
            await ctx.send(embed=build_embed("✅ Panel Sent", f"Panel sent to {target.mention}", success=True, ctx=ctx))
        except Exception:
            log.exception("Failed to send ticket panel")
            await ctx.send(embed=build_embed("❌ Failed", "Could not send panel — check permissions.", error=True, ctx=ctx))

    @command_error_reporter
    @commands.hybrid_command(name="close", description="Close this ticket")
    @app_commands.describe(reason="Reason for closing")
    async def close(self, ctx: commands.Context, reason: str = "Closed by staff"):
        if ctx.channel.id not in active_tickets:
            return await ctx.send(embed=build_embed("❌ Not a ticket", "This channel is not an active ticket.", error=True, ctx=ctx))
        await ctx.send(embed=build_embed("Closing", "Closing ticket…", ctx=ctx))
        await close_ticket(ctx.channel, reason=reason, closer=ctx.author, verdict="closed")

    @command_error_reporter
    @commands.hybrid_command(name="accept", description="Accept ticket — gives member role")
    async def accept(self, ctx: commands.Context, reason: str = "Accepted"):
        if ctx.channel.id not in active_tickets:
            return await ctx.send(embed=build_embed("❌ Not a ticket", "This channel is not an active ticket.", error=True, ctx=ctx))
        await ctx.send(embed=build_embed("Accepting", "Assigning roles and closing…", ctx=ctx))
        await close_ticket(ctx.channel, reason=reason, closer=ctx.author, verdict="accepted")

    @command_error_reporter
    @commands.hybrid_command(name="reject", description="Reject ticket")
    async def reject(self, ctx: commands.Context, reason: str = "Rejected"):
        if ctx.channel.id not in active_tickets:
            return await ctx.send(embed=build_embed("❌ Not a ticket", "This channel is not an active ticket.", error=True, ctx=ctx))
        await ctx.send(embed=build_embed("Rejecting", "Closing ticket as rejected…", ctx=ctx))
        await close_ticket(ctx.channel, reason=reason, closer=ctx.author, verdict="rejected")

    @command_error_reporter
    @commands.hybrid_command(name="save", description="Force-save all ticket media")
    async def save(self, ctx: commands.Context):
        # Reuse do_save by simulating an interaction where necessary
        fake_interaction = ctx
        await do_save(fake_interaction)

    @command_error_reporter
    @commands.hybrid_command(name="ticket-stats", description="Ticket system statistics")
    async def ticket_stats(self, ctx: commands.Context):
        total_media = sum(len(v.get("files", [])) for v in media_store.values())
        high_p = sum(1 for t in active_tickets.values() if t.get("priority") == "high")
        e = build_embed("🎫 Ticket Statistics", ctx=ctx)
        e.add_field(name="Total Tickets", value=str(cfg.get("ticket_counter", 0)), inline=True)
        e.add_field(name="Active Tickets", value=str(len(active_tickets)), inline=True)
        e.add_field(name="High Priority", value=str(high_p), inline=True)
        e.add_field(name="Media Saved", value=str(total_media), inline=True)
        await ctx.send(embed=e)

    @command_error_reporter
    @commands.hybrid_command(name="ticket-config", description="View ticket config (admin)")
    @commands.has_guild_permissions(administrator=True)
    async def ticket_config(self, ctx: commands.Context):
        def fc(cid):
            if not cid:
                return "Not set"
            ch = ctx.guild.get_channel(int(cid)) if ctx.guild else None
            return ch.mention if ch else f"`{cid}`"

        def fr(rid):
            if not rid:
                return "Not set"
            r = ctx.guild.get_role(int(rid)) if ctx.guild else None
            return r.mention if r else f"`{rid}`"

        embed = build_embed("⚙️ Ticket Config", ctx=ctx)
        embed.add_field(name="Review Channel", value=fc(cfg.get("review_channel")), inline=True)
        embed.add_field(name="Stats Channel", value=fc(cfg.get("stats_channel")), inline=True)
        embed.add_field(name="Category", value=fc(cfg.get("ticket_category")), inline=True)
        embed.add_field(name="Support Role", value=fr(cfg.get("support_role")), inline=True)
        embed.add_field(name="Member Role", value=fr(cfg.get("member_role")), inline=True)
        embed.add_field(name="Community Role", value=fr(cfg.get("community_role")), inline=True)
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="ticket-set", description="Set a ticket config value (admin)")
    @app_commands.describe(setting="Which setting", value="Channel/Role ID or name")
    @app_commands.choices(setting=[
        app_commands.Choice(name="Review Channel", value="review_channel"),
        app_commands.Choice(name="Stats Channel", value="stats_channel"),
        app_commands.Choice(name="Ticket Category", value="ticket_category"),
        app_commands.Choice(name="Support Role", value="support_role"),
        app_commands.Choice(name="Member Role", value="member_role"),
        app_commands.Choice(name="Community Role", value="community_role"),
        app_commands.Choice(name="Panel Title", value="panel_title"),
        app_commands.Choice(name="Panel Description", value="panel_description"),
        app_commands.Choice(name="Panel Color (hex)", value="panel_color"),
    ])
    @commands.has_guild_permissions(administrator=True)
    async def ticket_set(self, ctx: commands.Context, setting: str, value: str):
        if setting in ("panel_title", "panel_description", "panel_color"):
            cfg[setting] = value
            save_cfg()
            return await ctx.send(embed=build_embed("✅ Updated", f"{setting} updated to `{value}`", success=True, ctx=ctx))
        raw = value.strip().lstrip("<#@&>").rstrip(">")
        target = None
        if setting in ("review_channel", "stats_channel", "ticket_category"):
            if raw.isdigit():
                target = ctx.guild.get_channel(int(raw))
            else:
                target = discord.utils.get(ctx.guild.channels, name=value)
        else:
            if raw.isdigit():
                target = ctx.guild.get_role(int(raw))
            else:
                target = discord.utils.get(ctx.guild.roles, name=value)
        if not target:
            return await ctx.send(embed=build_embed("❌ Not found", f"Could not find `{value}`", error=True, ctx=ctx))
        cfg[setting] = target.id
        save_cfg()
        await ctx.send(embed=build_embed("✅ Updated", f"{setting} → {target.mention}", success=True, ctx=ctx))

    @commands.Cog.listener()
    @command_error_reporter
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        cid = message.channel.id
        if cid in active_tickets and message.attachments:
            ticket = active_tickets[cid]
            member = message.guild.get_member(ticket["user"])
            key = str(cid)
            added = 0
            for att in message.attachments:
                ext = os.path.splitext(att.filename)[1].lower()
                kind = "image" if ext in IMAGE_EXTS else ("video" if ext in VIDEO_EXTS else None)
                if kind:
                    media_store.setdefault(key, {"user_id": ticket["user"], "user_tag": str(member or "unknown"), "ticket_num": ticket.get("ticket_num", 0), "files": []})
                    media_store[key]["files"].append({
                        "filename": att.filename,
                        "type": kind,
                        "url": att.url,
                        "orig_url": att.url,
                        "saved_at": utcnow().isoformat(),
                    })
                    added += 1
            if added:
                ticket["media_count"] = len(media_store[key]["files"])
                save_media()
                save_active()


async def setup(bot: commands.Bot):
    data = _load(ACTIVE_FILE, {})
    for k, v in data.items():
        try:
            active_tickets[int(k)] = {**v, "user": int(v.get("user", 0))}
        except Exception:
            continue
    bot.add_view(OpenTicketView())
    bot.add_view(TicketControlView())
    await bot.add_cog(TicketCog(bot))
    print("[ticket_cog] Loaded — ticket panel, controls, save, stats and config")
