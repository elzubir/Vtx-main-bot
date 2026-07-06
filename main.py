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
    raise error


# ----------------------- Interactive help command -----------------------
class HelpView(discord.ui.View):
    def __init__(self, bot, ctx, pages, author_id):
        super().__init__(timeout=120)
        self.bot = bot
        self.ctx = ctx
        self.pages = pages
        self.index = 0
        self.author_id = author_id

        # create a select for the first page's commands
        self.select = discord.ui.Select(placeholder="View command details...", min_values=1, max_values=1, options=self._options_for_page(0))
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def _options_for_page(self, page_index: int):
        options = []
        for cmd in self.pages[page_index]["commands"]:
            desc = cmd.short_doc or cmd.help or "No description"
            # discord.SelectOption requires max length limits handled by library
            options.append(discord.SelectOption(label=cmd.name, description=(desc[:90] + "...") if len(desc) > 90 else desc, value=cmd.name))
        return options

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        name = interaction.data["values"][0]
        cmd = discord.utils.get(self.bot.commands, name=name)
        if not cmd:
            return await interaction.response.send_message("Command not found.", ephemeral=True)

        embed = discord.Embed(title=f"Help: {cmd.qualified_name}", color=discord.Color.blurple())
        embed.add_field(name="Description", value=cmd.help or "No description provided.", inline=False)
        usage = f"{self.ctx.prefix}{cmd.qualified_name} {cmd.signature}" if cmd.signature else f"{self.ctx.prefix}{cmd.qualified_name}"
        embed.add_field(name="Usage", value=f'`{usage}`', inline=False)
        if cmd.aliases:
            embed.add_field(name="Aliases", value=", ".join(cmd.aliases), inline=False)
        embed.add_field(name="Category", value=cmd.cog_name or "No Category", inline=True)
        embed.set_footer(text="This message is ephemeral and visible only to you.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        self.index = (self.index - 1) % len(self.pages)
        embed = self.pages[self.index]["embed"]
        # update select options for new page
        self.select.options = self._options_for_page(self.index)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        self.index = (self.index + 1) % len(self.pages)
        embed = self.pages[self.index]["embed"]
        self.select.options = self._options_for_page(self.index)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This help session belongs to someone else.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Help session closed.", embed=None, view=self)


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
        embed = discord.Embed(title=f"Help: {cmd.qualified_name}", color=discord.Color.blurple())
        embed.add_field(name="Description", value=cmd.help or "No description provided.", inline=False)
        usage = f"{ctx.prefix}{cmd.qualified_name} {cmd.signature}" if cmd.signature else f"{ctx.prefix}{cmd.qualified_name}"
        embed.add_field(name="Usage", value=f'`{usage}`', inline=False)
        if cmd.aliases:
            embed.add_field(name="Aliases", value=", ".join(cmd.aliases), inline=False)
        embed.add_field(name="Category", value=cmd.cog_name or "No Category", inline=True)
        return await ctx.send(embed=embed)

    # Build list of visible commands grouped by cog
    visible_cmds = [c for c in bot.commands if not c.hidden]
    # sort and group by cog
    visible_cmds.sort(key=lambda c: (c.cog_name or "", c.name))

    pages = []
    page_size = 6  # number of commands per page
    for chunk in chunked(visible_cmds, page_size):
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name="Bot Help", icon_url=bot.user.avatar.url if bot.user and bot.user.avatar else None)
        for c in chunk:
            name = f"{ctx.prefix}{c.name} {c.signature}" if c.signature else f"{ctx.prefix}{c.name}"
            desc = c.short_doc or c.help or "No description"
            embed.add_field(name=name, value=(desc if len(desc) < 250 else desc[:247] + "..."), inline=False)
        pages.append({"embed": embed, "commands": chunk})

    if not pages:
        return await ctx.send("No commands available.")

    view = HelpView(bot, ctx, pages, ctx.author.id)
    message = await ctx.send(embed=pages[0]["embed"], view=view)


# Run the bot using an environment variable for the token (safer than hardcoding)
if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN environment variable not set. Exiting.")
    else:
        bot.run(TOKEN)
