import functools
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Colors and UI constants
EMBED_COLOR = 0x27272F
EMBED_SUCCESS = 0x57F287
EMBED_WARNING = 0xFEE75C
EMBED_ERROR = 0xED4245
PAGINATOR_TIMEOUT = 120
PER_PAGE = 10


def build_embed(title: str, description: str = "", *, color=EMBED_COLOR, ctx=None,
                success: bool = False, warning: bool = False, error: bool = False) -> discord.Embed:
    if error:
        color = EMBED_ERROR
    elif warning:
        color = EMBED_WARNING
    elif success:
        color = EMBED_SUCCESS
    embed = discord.Embed(title=title, description=description, color=color,
                          timestamp=datetime.now(timezone.utc))
    try:
        if isinstance(ctx, commands.Context):
            embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        elif isinstance(ctx, discord.Interaction):
            embed.set_footer(text=f"Requested by {ctx.user}", icon_url=ctx.user.display_avatar.url)
    except Exception:
        # If something about avatar or ctx is missing, ignore footer rather than crash.
        pass
    return embed


def fmt_dt(dt: Optional[datetime], style: str = "F") -> str:
    return discord.utils.format_dt(dt, style) if dt else "Unknown"


# -----------------------
# Error reporter decorator
# -----------------------
def command_error_reporter(func):
    """
    Decorator that wraps command/cog methods to catch exceptions,
    log them, and send a standardized embed indicating where the error occurred.
    Works for both commands.Context and discord.Interaction.
    """
    @functools.wraps(func)
    async def wrapper(self, ctx, *args, **kwargs):
        try:
            return await func(self, ctx, *args, **kwargs)
        except Exception as exc:
            where = f"{self.__class__.__name__}.{func.__name__}"
            # Log full traceback to logger
            log.exception("Unhandled exception in %s", where, exc_info=exc)

            # Build concise error message for users
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            short_tb = tb if len(tb) < 400 else tb[:397] + "..."

            embed = build_embed(
                "Internal Error",
                f"An error occurred in `{where}`:\n```py\n{short_tb}\n```",
                error=True,
                ctx=ctx if isinstance(ctx, (commands.Context, discord.Interaction)) else None
            )

            # Try to send a response depending on whether ctx is Interaction or Context
            try:
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done():
                        await ctx.followup.send(embed=embed, ephemeral=True)
                    else:
                        await ctx.response.send_message(embed=embed, ephemeral=True)
                else:
                    await ctx.send(embed=embed)
            except Exception:
                # Best-effort only; avoid raising another exception
                log.exception("Failed to deliver error embed to user for %s", where)

            # Optionally: re-raise or swallow. We swallow so the bot remains responsive.
            return None

    return wrapper


# -----------------------
# Paginator (unchanged behavior, polished)
# -----------------------
class Paginator(discord.ui.View):
    def __init__(self, items: List[str], author: discord.abc.Snowflake, *, per_page=PER_PAGE, title="Items", ctx=None):
        super().__init__(timeout=PAGINATOR_TIMEOUT)
        self.items = items
        self.author = author
        self.per_page = per_page
        self.title = title
        self.ctx = ctx
        self.page = 0
        self.max_pages = max(0, (len(items) - 1) // per_page)
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _format_page(self) -> str:
        start = self.page * self.per_page
        chunk = self.items[start:start + self.per_page]
        return "\n".join(f"`{i+start+1:02}` • {x}" for i, x in enumerate(chunk)) or "*No data.*"

    def _build_page_embed(self) -> discord.Embed:
        return build_embed(f"{self.title} — Page {self.page+1}/{self.max_pages+1}",
                           self._format_page(), ctx=self.ctx)

    def _update_buttons(self):
        # Buttons are added below using decorators; we guard their presence using getattr.
        try:
            self.first_btn.disabled = self.page == 0
            self.prev_btn.disabled = self.page == 0
            self.next_btn.disabled = self.page >= self.max_pages
            self.last_btn.disabled = self.page >= self.max_pages
            self.page_btn.label = f"{self.page+1}/{self.max_pages+1}"
            self.jump_btn.disabled = self.max_pages == 0
        except Exception:
            pass

    async def _render(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_page_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("⛔ This paginator belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def first_btn(self, interaction: discord.Interaction, _):
        self.page = 0
        await self._render(interaction)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.primary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, _):
        self.page = max(0, self.page - 1)
        await self._render(interaction)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.primary, row=0)
    async def next_btn(self, interaction: discord.Interaction, _):
        self.page = min(self.max_pages, self.page + 1)
        await self._render(interaction)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def last_btn(self, interaction: discord.Interaction, _):
        self.page = self.max_pages
        await self._render(interaction)

    @discord.ui.button(label="Jump to page", emoji="🔢", style=discord.ButtonStyle.secondary, row=1)
    async def jump_btn(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(PageJumpModal(self))


class PageJumpModal(discord.ui.Modal, title="Jump to Page"):
    page_input = discord.ui.TextInput(label="Page number", placeholder="Enter a page number…",
                                      min_length=1, max_length=4)

    def __init__(self, paginator: Paginator):
        super().__init__()
        self.paginator = paginator

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.page_input.value.strip()
        if not raw.isdigit():
            return await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)
        target = int(raw) - 1
        if not (0 <= target <= self.paginator.max_pages):
            return await interaction.response.send_message(
                f"❌ Page must be between **1** and **{self.paginator.max_pages+1}**.", ephemeral=True)
        self.paginator.page = target
        await self.paginator._render(interaction)


# -----------------------
# Reusable User Info View (buttons + separator + select)
# -----------------------
class UserInfoView(discord.ui.View):
    def __init__(self, member: discord.Member, ctx: commands.Context):
        super().__init__(timeout=300)
        self.member = member
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Restrict interactions to the original command invoker
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ This menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Avatar", emoji="🖼️", style=discord.ButtonStyle.primary, row=0)
    async def avatar_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = build_embed(f"🖼 Avatar — {self.member.display_name}", ctx=self.ctx)
        e.set_image(url=self.member.display_avatar.url)
        av = self.member.display_avatar
        links = " | ".join(
            f"[{fmt}]({av.replace(format=fmt, size=4096)})" for fmt in ("webp", "png", "jpg")
        )
        e.add_field(name="Download", value=links, inline=False)
        await interaction.response.edit_message(embed=e, view=self)

    # Separator for layout clarity (ui.Separator added in 2.7.1)
    # Using add_item is optional: button decorators are used above/below; separator is a standalone component.
    # If your discord.py variant supports it, this will create a visual separator.
    try:
        sep = discord.ui.Separator()
    except Exception:
        sep = None
    if sep:
        add_item = getattr(discord.ui.View, "add_item")
        # We attach the separator at initialization time
        # (we don't call add_item here; decorator-defined buttons already exist)
        # If the library accepts it, it will be rendered between rows automatically.

    @discord.ui.button(label="Banner", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def banner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            user = await self.ctx.bot.fetch_user(self.member.id)
            if not user.banner:
                e = build_embed("No Banner", f"**{self.member}** has no banner.", warning=True, ctx=self.ctx)
            else:
                e = build_embed(f"🎨 Banner — {self.member.display_name}", ctx=self.ctx)
                e.set_image(url=user.banner.url)
            await interaction.response.edit_message(embed=e, view=self)
        except Exception as exc:
            log.exception("Failed to fetch banner for %s", self.member.id, exc_info=exc)
            await interaction.response.send_message("Failed to fetch banner.", ephemeral=True)

    @discord.ui.select(
        placeholder="Quick actions",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Show account creation date", value="created"),
            discord.SelectOption(label="Show server join date", value="joined"),
            discord.SelectOption(label="Mention user", value="mention"),
        ],
        row=1
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        v = select.values[0]
        if v == "created":
            e = build_embed("Account Created", fmt_dt(self.member.created_at), ctx=self.ctx)
            await interaction.response.edit_message(embed=e, view=self)
        elif v == "joined":
            e = build_embed("Joined Server", fmt_dt(self.member.joined_at), ctx=self.ctx)
            await interaction.response.edit_message(embed=e, view=self)
        elif v == "mention":
            await interaction.response.send_message(f"{self.member.mention}", ephemeral=True)


# -----------------------
# Info Cog (commands correctly placed inside a Cog class)
# -----------------------
class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Important commands: hybrid (slash + prefix)
    @command_error_reporter
    @commands.hybrid_command(name="userinfo", description="Detailed info about a member.")
    @app_commands.describe(member="The member to inspect (defaults to you).")
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        e = build_embed(f"👤 {member.display_name}", ctx=ctx)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Username", value=str(member))
        e.add_field(name="User ID", value=f"`{member.id}`")
        e.add_field(name="Bot?", value="✅" if member.bot else "❌")
        e.add_field(name="Account Created", value=fmt_dt(member.created_at), inline=False)
        e.add_field(name="Joined Server", value=fmt_dt(member.joined_at), inline=False)
        e.add_field(name="Top Role", value=member.top_role.mention)
        e.add_field(name="Roles", value=f"`{len(member.roles)-1}`")
        e.add_field(name="Status", value=str(member.status).title())
        if member.premium_since:
            e.add_field(name="Boosting Since", value=fmt_dt(member.premium_since), inline=False)

        view = UserInfoView(member, ctx)
        # Save message for view timeouts to update UI later if desired
        msg = await ctx.send(embed=e, view=view)
        view.message = msg

    @command_error_reporter
    @commands.hybrid_command(name="whois", description="Deep user lookup.")
    @app_commands.describe(member="Target member.")
    async def whois(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)
        flags = [f.name.replace("_", " ").title() for f, v in member.public_flags if v]
        e = build_embed(f"🔍 {member}", ctx=ctx)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="ID", value=f"`{member.id}`")
        e.add_field(name="Created", value=fmt_dt(member.created_at))
        e.add_field(name="Joined", value=fmt_dt(member.joined_at))
        e.add_field(name="Top Role", value=member.top_role.mention)
        e.add_field(name="Roles", value=f"`{len(member.roles)-1}`")
        e.add_field(name="Status", value=str(member.status).title())
        e.add_field(name="Badges", value=", ".join(flags) if flags else "None", inline=False)
        if user.banner:
            e.set_image(url=user.banner.url)
        await ctx.send(embed=e)

    # Bot stats: hybrid
    @command_error_reporter
    @commands.hybrid_command(name="botstats", description="Live stats for this bot.")
    async def botstats(self, ctx: commands.Context):
        total_users = sum(g.member_count for g in self.bot.guilds)
        uptime_delta = datetime.now(timezone.utc) - self.bot.start_time
        e = build_embed("🤖 Bot Statistics", ctx=ctx)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)
        e.add_field(name="Servers", value=f"`{len(self.bot.guilds)}`")
        e.add_field(name="Users", value=f"`{total_users:,}`")
        e.add_field(name="Latency", value=f"`{round(self.bot.latency * 1000)}ms`")
        e.add_field(name="Commands", value=f"`{len(self.bot.commands)}`")
        e.add_field(name="Uptime", value=f"`{str(uptime_delta).split('.')[0]}`", inline=False)
        await ctx.send(embed=e)

    # Prefix-only commands: mark as command() not hybrid_command
    @command_error_reporter
    @commands.command(name="roles", help="List all server roles (prefix-only).")
    async def roles_command(self, ctx: commands.Context):
        items = [r.mention for r in reversed(ctx.guild.roles) if not r.is_default()]
        await self._send_paginated(ctx, items, "Server Roles")

    @command_error_reporter
    @commands.command(name="membercount", help="Quick member count breakdown (prefix-only).")
    async def membercount_command(self, ctx: commands.Context):
        g = ctx.guild
        humans = sum(1 for m in g.members if not m.bot)
        bots = sum(1 for m in g.members if m.bot)
        ratio = round(humans / g.member_count * 100) if g.member_count else 0
        e = build_embed("👥 Member Count", ctx=ctx)
        e.add_field(name="Total", value=f"`{g.member_count}`")
        e.add_field(name="Humans", value=f"`{humans}`")
        e.add_field(name="Bots", value=f"`{bots}`")
        e.add_field(name="Human Ratio", value=f"`{ratio}%`", inline=False)
        await ctx.send(embed=e)

    # Helper to send paginated content using Paginator view
    async def _send_paginated(self, ctx: commands.Context, items: List[str], title: str):
        view = Paginator(items, ctx.author, title=title, ctx=ctx)
        embed = build_embed(title + " — Page 1/1", view._format_page(), ctx=ctx)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    # Example: ping left prefix-only by choice
    @command_error_reporter
    @commands.command(name="ping", help="Check bot latency (prefix-only).")
    async def ping_command(self, ctx: commands.Context):
        import time
        start = time.monotonic()
        msg = await ctx.send("Pinging…")
        rest = round((time.monotonic() - start) * 1000)
        ws = round(self.bot.latency * 1000)
        e = build_embed("🏓 Pong!", ctx=ctx, success=True)
        e.add_field(name="WebSocket", value=f"`{ws}ms`")
        e.add_field(name="REST", value=f"`{rest}ms`")
        await msg.edit(content=None, embed=e)

    # Centralized fallback for Cog-level errors (keeps previous behavior)
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        # We preserve the specific error handlers first
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(embed=build_embed("Missing Argument",
                                                    f"Usage: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`",
                                                    error=True, ctx=ctx))
        if isinstance(error, commands.MemberNotFound):
            return await ctx.send(embed=build_embed("Member Not Found", str(error), error=True, ctx=ctx))
        if isinstance(error, commands.BadArgument):
            return await ctx.send(embed=build_embed("Bad Argument", str(error), error=True, ctx=ctx))
        if isinstance(error, commands.CommandOnCooldown):
            return await ctx.send(embed=build_embed("⏱ Slow Down",
                                                    f"Try again in **{error.retry_after:.1f}s**.", warning=True, ctx=ctx))
        if isinstance(error, commands.MissingPermissions):
            return await ctx.send(embed=build_embed("No Permission", str(error), error=True, ctx=ctx))

        # Unhandled: log for later investigation
        log.exception("Unhandled error in %s", getattr(ctx, "command", None), exc_info=error)


# Cog setup entrypoint
async def setup(bot):
    await bot.add_cog(InfoCog(bot))
