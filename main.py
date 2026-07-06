import discord
from discord.ext import commands
from datetime import datetime, timezone
import asyncio, logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

EMBED_COLOR = 0x27272f  # modern dark embed background color

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents, help_command=None)
bot.start_time = datetime.now(timezone.utc)


@bot.event
async def setup_hook():
    # NOTE: dashboard_sync and nsfw_guard removed as requested

    # ── 1. Info cog ───────────────────────────────────────────────────────
    from info_cog import setup as info_setup
    await info_setup(bot)

    # ── 2. Welcome / Leave cog ────────────────────────────────────────────
    from welcome_cog import setup as gate_setup
    await gate_setup(bot)

    # ── 3. Ticket cog ─────────────────────────────────────────────────────
    from ticket_cog import setup as ticket_setup
    await ticket_setup(bot)

    # ── Sync slash commands ───────────────────────────────────────────────
    # Remove guild= to sync globally (takes up to 1 hour to propagate).
    # Use guild=discord.Object(id=YOUR_GUILD_ID) for instant testing.
    await bot.tree.sync()
    print("✅ All modules loaded. Slash commands synced.")


@bot.event
async def on_ready():
    print(f"⚡ {bot.user} online | {len(bot.guilds)} guild(s)")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="the server | /help"
        )
    )


# ── IMPORTANT: single on_message handler that calls process_commands ──
# All other modules use bot.listen("on_message") which doesn't override this.
@bot.event
async def on_message(message):
    # Allow bot messages to be processed by commands (for things like UI follow-ups)
    if message.author.bot:
        await bot.process_commands(message)
        return
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ You lack permission.", delete_after=5)
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(
            f"❌ Missing: `{error.param.name}`  —  Usage: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`",
            delete_after=10,
        )
    # Let other errors bubble so they can be logged/handled by cogs
    raise error


# ----------------------- Interactive help command -----------------------
class HelpView(discord.ui.View):
    def __init__(self, bot, ctx, pages, author_id, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.ctx = ctx
        self.pages = pages
        self.index = 0
        self.author_id = author_id
        self.message: discord.Message | None = None

        # Dropdown showing commands on current page
        self.select = discord.ui.Select(
            placeholder="View command details...",
            min_values=1,
            max_values=1,
            options=self._options_for_page(0),
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

        # Visual separator (discord.py 2.7+)
        try:
            self.add_item(discord.ui.Separator())
        except Exception:
            # Older versions may not have Separator; ignore
            pass

        # Navigation buttons
        self.previous_button = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
        self.close_button = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger)

        self.previous_button.callback = self.previous
        self.next_button.callback = self.next
        self.close_button.callback = self.close

        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self.add_item(self.close_button)

    def _options_for_page(self, page_index: int):
        options = []
        for cmd in self.pages[page_index]["commands"]:
            desc = cmd.short_doc or cmd.help or "No description"
            options.append(
                discord.SelectOption(
                    label=cmd.name,
                    description=(desc[:90] + "...") if len(desc) > 90 else desc,
                    value=cmd.name,
                )
            )
        # If no commands on page, add a disabled placeholder option
        if not options:
            options.append(discord.SelectOption(label="(no commands)", value="none", description="", default=True))
        return options

    async def _send_ephemeral(self, interaction: discord.Interaction, *, embed: discord.Embed | None = None, content: str | None = None):
        """Try to send an ephemeral response; fall back to DM if interaction is invalid."""
        try:
            # Always acknowledge quickly to avoid Unknown interaction
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
                await interaction.followup.send(embed=embed, content=content, ephemeral=True)
            else:
                # If response already completed, send a followup (may not be ephemeral)
                await interaction.followup.send(embed=embed, content=content)
        except discord.NotFound:
            # Interaction unknown/expired: fallback to DM so user still gets help
            try:
                await interaction.user.send(embed=embed, content=content)
            except Exception:
                logging.exception("Failed to deliver help embed to user via DM.")
        except Exception:
            logging.exception("Unexpected error while sending ephemeral help response.")

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)

        # Defer early to avoid 'Unknown interaction' if processing takes time
        # We'll send the actual embed via followup
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            # ignore if already deferred/acknowledged
            pass

        name = interaction.data.get("values", [None])[0]
        if not name:
            return await interaction.followup.send("No command selected.", ephemeral=True)

        cmd = discord.utils.get(self.bot.commands, name=name)
        if not cmd:
            return await interaction.followup.send("Command not found.", ephemeral=True)

        embed = discord.Embed(title=f"Help: {cmd.qualified_name}", color=EMBED_COLOR)
        embed.add_field(name="Description", value=cmd.help or "No description provided.", inline=False)
        usage = f"{self.ctx.prefix}{cmd.qualified_name} {cmd.signature}" if cmd.signature else f"{self.ctx.prefix}{cmd.qualified_name}"
        embed.add_field(name="Usage", value=f'`{usage}`', inline=False)
        if cmd.aliases:
            embed.add_field(name="Aliases", value=", ".join(cmd.aliases), inline=False)
        embed.add_field(name="Category", value=cmd.cog_name or "No Category", inline=True)
        embed.set_footer(text="Displayed privately to you.")

        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.NotFound:
            # fallback to DM
            try:
                await interaction.user.send(embed=embed)
            except Exception:
                logging.exception("Failed to deliver help embed to user via DM.")
        except Exception:
            logging.exception("Unexpected error while sending command help followup.")

    async def previous(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        self.index = (self.index - 1) % len(self.pages)
        embed = self.pages[self.index]["embed"]
        # update select options for new page
        self.select.options = self._options_for_page(self.index)
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.NotFound:
            # message gone
            try:
                await interaction.response.send_message("Original help message is no longer available.", ephemeral=True)
            except Exception:
                pass

    async def next(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        self.index = (self.index + 1) % len(self.pages)
        embed = self.pages[self.index]["embed"]
        self.select.options = self._options_for_page(self.index)
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.NotFound:
            try:
                await interaction.response.send_message("Original help message is no longer available.", ephemeral=True)
            except Exception:
                pass

    async def close(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(content="Help session closed.", embed=None, view=self)
        except Exception:
            pass

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Help session expired.", view=self)
            except Exception:
                pass


def chunked(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


@bot.command(name="help")
async def help_command(ctx, *, command_name: str = None):
    """Show this help message or details for a specific command."""
    if command_name:
        cmd = discord.utils.get(bot.commands, name=command_name)
        if not cmd:
            return await ctx.send("❌ Command not found.")
        embed = discord.Embed(title=f"Help: {cmd.qualified_name}", color=EMBED_COLOR)
        embed.add_field(name="Description", value=cmd.help or "No description provided.", inline=False)
        usage = f"{ctx.prefix}{cmd.qualified_name} {cmd.signature}" if cmd.signature else f"{ctx.prefix}{cmd.qualified_name}"
        embed.add_field(name="Usage", value=f'`{usage}`', inline=False)
        if cmd.aliases:
            embed.add_field(name="Aliases", value=", ".join(cmd.aliases), inline=False)
        embed.add_field(name="Category", value=cmd.cog_name or "No Category", inline=True)
        return await ctx.send(embed=embed)

    # Build list of visible commands grouped by cog
    visible_cmds = [c for c in bot.commands if not c.hidden and getattr(c, "enabled", True)]
    # sort and group by cog
    visible_cmds.sort(key=lambda c: (c.cog_name or "", c.name))

    pages = []
    page_size = 6  # number of commands per page
    for chunk in chunked(visible_cmds, page_size):
        embed = discord.Embed(color=EMBED_COLOR)
        if bot.user and getattr(bot.user, "avatar", None):
            try:
                avatar_url = bot.user.avatar.url
            except Exception:
                avatar_url = None
        else:
            avatar_url = None
        embed.set_author(name="Bot Help", icon_url=avatar_url)
        for c in chunk:
            name = f"{ctx.prefix}{c.name} {c.signature}" if c.signature else f"{ctx.prefix}{c.name}"
            desc = c.short_doc or c.help or "No description"
            embed.add_field(name=name, value=(desc if len(desc) < 250 else desc[:247] + "..."), inline=False)
        pages.append({"embed": embed, "commands": chunk})

    if not pages:
        return await ctx.send("No commands available.")

    view = HelpView(bot, ctx, pages, ctx.author.id)
    message = await ctx.send(embed=pages[0]["embed"], view=view)
    view.message = message


# Run the bot using an environment variable for the token (safer than hardcoding)
if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN environment variable not set. Exiting.")
    else:
        bot.run(TOKEN)
