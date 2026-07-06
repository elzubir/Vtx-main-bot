import functools
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, List

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Centralized colors (keep UI consistent with other cogs)
EMBED_COLOR = 0x27272F
EMBED_SUCCESS = 0x57F287
EMBED_WARNING = 0xFEE75C
EMBED_ERROR = 0xED4245

WELCOME_FILE = "welcome_channels.json"
LEAVE_FILE = "leave_channels.json"


# -----------------------
# Helpers
# -----------------------

def _load(path: str) -> Dict[str, int]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.exception("Failed to load JSON from %s", path)
            return {}
    return {}


def _save(path: str, data: Dict[str, int]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        log.exception("Failed to save JSON to %s", path)


def fmt_dt(dt: Optional[datetime], style: str = "F") -> str:
    return discord.utils.format_dt(dt, style) if dt else "Unknown"


def build_embed(title: str, description: str = "", *, color: int = EMBED_COLOR, ctx=None, success=False, warning=False, error=False) -> discord.Embed:
    if error:
        color = EMBED_ERROR
    elif warning:
        color = EMBED_WARNING
    elif success:
        color = EMBED_SUCCESS
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    try:
        if isinstance(ctx, commands.Context):
            embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        elif isinstance(ctx, discord.Interaction):
            embed.set_footer(text=f"Requested by {ctx.user}", icon_url=ctx.user.display_avatar.url)
    except Exception:
        pass
    return embed


# -----------------------
# Error reporter decorator
# -----------------------

def command_error_reporter(func):
    """Decorator to catch exceptions in commands and interactions, log tracebacks and notify the invoker."""
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
# Welcome/Leave embed builders
# -----------------------

def create_welcome_embed(member: discord.Member, guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title=f"👋 Welcome to {guild.name}!",
        description=f"Hey {member.mention}, glad you're here! 🎉\nYou are member **#{guild.member_count}**.",
        color=EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    try:
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    except Exception:
        pass
    embed.add_field(name="🆔 User ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="📛 Username", value=str(member), inline=True)
    embed.add_field(name="🤖 Bot?", value="Yes" if member.bot else "No", inline=True)
    embed.add_field(name="📅 Account Age", value=fmt_dt(member.created_at), inline=False)
    embed.add_field(name="📥 Joined At", value=fmt_dt(member.joined_at), inline=False)
    embed.set_footer(text=f"Member #{guild.member_count} • {guild.name}", icon_url=guild.icon.url if guild.icon else None)
    return embed


def create_leave_embed(member: discord.Member, guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title=f"🚪 {member.display_name} left the server",
        description=f"{member.mention} has left **{guild.name}**.",
        color=EMBED_ERROR,
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    try:
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    except Exception:
        pass
    embed.add_field(name="🆔 User ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="📛 Username", value=str(member), inline=True)
    embed.add_field(name="🤖 Bot?", value="Yes" if member.bot else "No", inline=True)
    embed.add_field(name="📅 Account Age", value=fmt_dt(member.created_at), inline=False)
    embed.set_footer(text=f"Now {guild.member_count} members • {guild.name}", icon_url=guild.icon.url if guild.icon else None)
    return embed


# -----------------------
# Preview view (buttons)
# -----------------------
class GatePreviewView(discord.ui.View):
    def __init__(self, member: discord.Member, guild: discord.Guild, invoker: discord.User):
        super().__init__(timeout=120)
        self.member = member
        self.guild = guild
        self.invoker = invoker

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message("⛔ This preview belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Send Welcome", style=discord.ButtonStyle.success, row=0)
    async def send_welcome(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            await interaction.response.send_message(embed=create_welcome_embed(self.member, self.guild))
        except Exception:
            log.exception("Failed to send welcome preview")
            await interaction.response.send_message("Failed to send welcome preview.", ephemeral=True)

    @discord.ui.button(label="Send Leave", style=discord.ButtonStyle.danger, row=0)
    async def send_leave(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            await interaction.response.send_message(embed=create_leave_embed(self.member, self.guild))
        except Exception:
            log.exception("Failed to send leave preview")
            await interaction.response.send_message("Failed to send leave preview.", ephemeral=True)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass


# -----------------------
# Message panel UI
# -----------------------
class _ChannelSelect(discord.ui.Select):
    def __init__(self, channels: List[discord.TextChannel], placeholder: str = "Choose a channel..."):
        options = [discord.SelectOption(label=c.name, value=str(c.id), description=f"#{c.name}") for c in channels[:25]]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: MessagePanelView = self.view  # type: ignore
        view.selected_channel_id = int(self.values[0])
        await interaction.response.defer(ephemeral=True)


class _RoleSelect(discord.ui.Select):
    def __init__(self, roles: List[discord.Role], placeholder: str = "Choose a role (optional)..."):
        options = [discord.SelectOption(label=r.name, value=str(r.id), description=f"@{r.name}") for r in roles[:25] if not r.is_default()]
        if not options:
            options = [discord.SelectOption(label="No roles available", value="0", description="", default=True)]
            super().__init__(placeholder=placeholder, min_values=0, max_values=0, options=options, disabled=True)
        else:
            super().__init__(placeholder=placeholder, min_values=0, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: MessagePanelView = self.view  # type: ignore
        if self.values:
            view.selected_role_id = int(self.values[0])
        else:
            view.selected_role_id = None
        await interaction.response.defer(ephemeral=True)


class EmbedModal(discord.ui.Modal, title="Send Embed Message"):
    title_input = discord.ui.TextInput(label="Title", required=True, max_length=256)
    description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=4000)

    def __init__(self, view: 'MessagePanelView'):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        view = self.view
        if not view.selected_channel_id:
            await interaction.response.send_message(embed=build_embed("⛔ No channel selected", "Please select a channel first.", error=True, ctx=interaction), ephemeral=True)
            return
        channel = view.guild.get_channel(view.selected_channel_id)
        if channel is None:
            await interaction.response.send_message(embed=build_embed("⛔ Channel not found", "The selected channel could not be found.", error=True, ctx=interaction), ephemeral=True)
            return
        embed = discord.Embed(title=self.title_input.value, description=self.description_input.value, color=EMBED_COLOR, timestamp=discord.utils.utcnow())
        # small UI-like separator using a field (since discord.ui.separator doesn't exist)
        embed.add_field(name="", value="", inline=False)
        if view.selected_role_id:
            role = view.guild.get_role(view.selected_role_id)
            if role:
                embed.add_field(name="Mention", value=role.mention, inline=False)
        try:
            embed.set_footer(text=f"Sent by {view.invoker}", icon_url=view.invoker.display_avatar.url)
        except Exception:
            embed.set_footer(text=f"Sent by {view.invoker}")
        try:
            await channel.send(embed=embed)
            await interaction.response.send_message(embed=build_embed("✅ Embed sent", f"Embed message sent to <#{channel.id}>", success=True, ctx=interaction), ephemeral=True)
        except Exception as exc:
            log.exception("Failed to send embed from panel: %s", exc)
            await interaction.response.send_message(embed=build_embed("Failed to send", "Could not deliver the embed message.", error=True, ctx=interaction), ephemeral=True)


class NormalModal(discord.ui.Modal, title="Send Plain Message"):
    content_input = discord.ui.TextInput(label="Message content", style=discord.TextStyle.paragraph, required=True, max_length=2000)

    def __init__(self, view: 'MessagePanelView'):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        view = self.view
        if not view.selected_channel_id:
            await interaction.response.send_message(embed=build_embed("⛔ No channel selected", "Please select a channel first.", error=True, ctx=interaction), ephemeral=True)
            return
        channel = view.guild.get_channel(view.selected_channel_id)
        if channel is None:
            await interaction.response.send_message(embed=build_embed("⛔ Channel not found", "The selected channel could not be found.", error=True, ctx=interaction), ephemeral=True)
            return
        content = self.content_input.value
        footer = f"\n\n— Sent by {view.invoker}"
        if view.selected_role_id:
            role = view.guild.get_role(view.selected_role_id)
            if role:
                content = f"{role.mention} {content}"
        try:
            await channel.send(content + footer)
            await interaction.response.send_message(embed=build_embed("✅ Message sent", f"Message sent to <#{channel.id}>", success=True, ctx=interaction), ephemeral=True)
        except Exception as exc:
            log.exception("Failed to send plain message from panel: %s", exc)
            await interaction.response.send_message(embed=build_embed("Failed to send", "Could not deliver the plain message.", error=True, ctx=interaction), ephemeral=True)


class MessagePanelView(discord.ui.View):
    def __init__(self, guild: discord.Guild, invoker: discord.User):
        super().__init__(timeout=300)
        self.guild = guild
        self.invoker = invoker
        self.selected_channel_id: Optional[int] = None
        self.selected_role_id: Optional[int] = None

        # populate channel select with text channels
        text_channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
        if text_channels:
            self.add_item(_ChannelSelect(text_channels))

        # populate role select
        roles = [r for r in guild.roles if not r.is_default()]
        if roles:
            self.add_item(_RoleSelect(roles))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message("⛔ This panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Embed message", style=discord.ButtonStyle.primary, row=2)
    async def embed_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Open modal to gather embed title/description
        await interaction.response.send_modal(EmbedModal(self))

    @discord.ui.button(label="Normal message", style=discord.ButtonStyle.secondary, row=2)
    async def normal_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Open modal to gather plain text message
        await interaction.response.send_modal(NormalModal(self))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=3)
    async def close_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass


# -----------------------
# Cog
# -----------------------
class WelcomeCog(commands.Cog, name="Welcome/Leave"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.welcome_channels = _load(WELCOME_FILE)
        self.leave_channels = _load(LEAVE_FILE)

    # Hybrid: set welcome channel (slash + prefix)
    @command_error_reporter
    @commands.hybrid_command(name="setwelcome", description="Set the welcome channel.")
    @app_commands.describe(channel="Channel to post welcome messages in.")
    @commands.has_guild_permissions(manage_guild=True)
    async def setwelcome(self, ctx: commands.Context, channel: discord.TextChannel):
        gid = str(ctx.guild.id)
        self.welcome_channels[gid] = channel.id
        _save(WELCOME_FILE, self.welcome_channels)
        await ctx.send(embed=build_embed("✅ Welcome Channel Set", f"Welcome messages will be sent to {channel.mention}.", success=True, ctx=ctx))

    # Hybrid: set leave channel
    @command_error_reporter
    @commands.hybrid_command(name="setleave", description="Set the leave channel.")
    @app_commands.describe(channel="Channel to post leave messages in.")
    @commands.has_guild_permissions(manage_guild=True)
    async def setleave(self, ctx: commands.Context, channel: discord.TextChannel):
        gid = str(ctx.guild.id)
        self.leave_channels[gid] = channel.id
        _save(LEAVE_FILE, self.leave_channels)
        await ctx.send(embed=build_embed("✅ Leave Channel Set", f"Leave messages will be sent to {channel.mention}.", success=True, ctx=ctx))

    # Hybrid: set both channels at once
    @command_error_reporter
    @commands.hybrid_command(name="setgate", description="Set both welcome and leave channels.")
    @app_commands.describe(welcome_channel="Channel for welcomes", leave_channel="Channel for leaves")
    @commands.has_guild_permissions(manage_guild=True)
    async def setgate(self, ctx: commands.Context, welcome_channel: discord.TextChannel, leave_channel: discord.TextChannel):
        gid = str(ctx.guild.id)
        self.welcome_channels[gid] = welcome_channel.id
        self.leave_channels[gid] = leave_channel.id
        _save(WELCOME_FILE, self.welcome_channels)
        _save(LEAVE_FILE, self.leave_channels)
        await ctx.send(embed=build_embed("✅ Gate Config Updated",
                                       f"Welcome: {welcome_channel.mention}\nLeave: {leave_channel.mention}", success=True, ctx=ctx))

    # Hybrid: clear welcome
    @command_error_reporter
    @commands.hybrid_command(name="clearwelcome", description="Remove the welcome channel config.")
    @commands.has_guild_permissions(manage_guild=True)
    async def clearwelcome(self, ctx: commands.Context):
        self.welcome_channels.pop(str(ctx.guild.id), None)
        _save(WELCOME_FILE, self.welcome_channels)
        await ctx.send(embed=build_embed("🗑 Welcome Channel Cleared", "Welcome messages are now disabled for this server.", warning=True, ctx=ctx))

    # Hybrid: clear leave
    @command_error_reporter
    @commands.hybrid_command(name="clearleave", description="Remove the leave channel config.")
    @commands.has_guild_permissions(manage_guild=True)
    async def clearleave(self, ctx: commands.Context):
        self.leave_channels.pop(str(ctx.guild.id), None)
        _save(LEAVE_FILE, self.leave_channels)
        await ctx.send(embed=build_embed("🗑 Leave Channel Cleared", "Leave messages are now disabled for this server.", warning=True, ctx=ctx))

    # Hybrid: view config
    @command_error_reporter
    @commands.hybrid_command(name="gateconfig", description="View current welcome/leave config.")
    async def gateconfig(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        wid = self.welcome_channels.get(gid)
        lid = self.leave_channels.get(gid)
        w_val = f"<#{wid}>" if wid else "❌ Not set"
        l_val = f"<#{lid}>" if lid else "❌ Not set"
        e = build_embed("⚙️ Gate Configuration", f"👋 Welcome Channel: {w_val}\n🚪 Leave Channel: {l_val}", ctx=ctx)
        await ctx.send(embed=e)

    # Hybrid: preview with interactive buttons
    @command_error_reporter
    @commands.hybrid_command(name="previewgate", description="Preview welcome and leave embeds.")
    async def previewgate(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        view = GatePreviewView(member, ctx.guild, ctx.author)
        e = build_embed("Preview Gate Embeds", "Use the buttons to send sample welcome/leave embeds.", ctx=ctx)
        await ctx.send(embed=e, view=view)

    # New: Panel to send messages (embed or normal) via UI
    @command_error_reporter
    @commands.hybrid_command(name="messagepanel", description="Open a panel to send an embed or normal message via the bot.")
    @commands.has_guild_permissions(manage_guild=True)
    async def messagepanel(self, ctx: commands.Context):
        view = MessagePanelView(ctx.guild, ctx.author)
        # Build the embedded panel message (buttons live in the view)
        desc = "Use the selects to choose a channel and optionally a role.\nThen use the buttons below to send an embed or a normal message.\n\nNote: the footer of sent messages will show who sent them."
        embed = discord.Embed(title="Message Panel", description=desc, color=EMBED_COLOR, timestamp=discord.utils.utcnow())
        try:
            embed.set_footer(text=f"Panel opened by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        except Exception:
            embed.set_footer(text=f"Panel opened by {ctx.author}")
        await ctx.send(embed=embed, view=view)

    # Events
    @command_error_reporter
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cid = self.welcome_channels.get(str(member.guild.id))
        if not cid:
            return
        channel = member.guild.get_channel(cid)
        if not channel:
            return
        try:
            await channel.send(embed=create_welcome_embed(member, member.guild))
        except Exception as exc:
            log.exception("Failed to send welcome message: %s", exc)

    @command_error_reporter
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cid = self.leave_channels.get(str(member.guild.id))
        if not cid:
            return
        channel = member.guild.get_channel(cid)
        if not channel:
            return
        try:
            await channel.send(embed=create_leave_embed(member, member.guild))
        except Exception as exc:
            log.exception("Failed to send leave message: %s", exc)

    # App command error handler
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(embed=build_embed("⛔ Missing Permissions", "You need **Manage Guild** to use this command.", error=True, ctx=interaction), ephemeral=True)
        else:
            # Let the decorator handle other errors where applicable
            log.exception("Unhandled app command error: %s", error)


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
