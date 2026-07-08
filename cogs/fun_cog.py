import asyncio
import functools
import logging
import random
import traceback
from datetime import datetime, timezone
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Colors
EMBED_COLOR = 0x27272F
EMBED_SUCCESS = 0x57F287
EMBED_WARNING = 0xFEE75C
EMBED_ERROR = 0xED4245

# Custom Emoji IDs
CUSTOM_EMOJIS = {
    "hypesquad": "<:hypesquad:1516047025052057770>",
    "hypesquad1": "<:hypesquad1:1516047023730589726>",
    "hypesquad2": "<:hypesquad2:1516047022590001162>",
    "mod": "<:mod:1516047020853301390>",
    "hypesquad3": "<:hypesquad3:1516047019427500032>",
    "partner": "<:partner:1516047018156626010>",
    "dev": "<:dev:1516047017036480654>",
    "publicserver": "<:publicserver:1516047015505826003>",
    "serverrules": "<:serverrules:1516047014058655765>",
    "dev2": "<:dev2:1516047012645310556>",
    "refresh": "<:refresh:1516047011609313290>",
    "subscrbtion": "<:subscrbtion:1516047010288111688>",
    "report": "<:report:1516047009164038184>",
    "nitro": "<:nitro:1516047007737974916>",
    "staff": "<:staff:1516047006127095899>",
    "preview": "<:preview:1516047005019934720>",
    "serverguide": "<:serverguide:1516047003740540990>",
    "owner": "<:owner:1516047003006537799>",
    "verified": "<:verified:1516047001354108938>",
    "guest": "<:guest:1516046999223537725>",
    "newbie": "<:newbie:1516046996727664791>",
    "member": "<:member:1516046994945081406>",
    "botlabel": "<:botlabel:1516046994249093191>",
    "leftserver": "<:leftserver:1516046992717906012>",
    "slashcommand": "<:slashcommand:1516046991547830362>",
    "joinedserver": "<:joinedserver:1516046990369226853>",
    "application": "<:application:1516046988838305793>",
    "betalabel": "<:betalabel:1516046988255166565>",
    "link": "<:link:1516046987185754152>",
    "id": "<:id:1516046985671479397>",
    "automod": "<:automod:1516046984073445577>",
    "announncment": "<:announncment:1516046982718685184>",
    "warning": "<:warning:1516046980969664542>",
    "boosticon": "<:boosticon:1516046979640332319>",
    "sparkels": "<:sparkels:1516046978188967966>",
    "modaction": "<:modaction:1516046976720961627>",
    "arrowtoright": "<:arrowtoright:1516046975446028310>",
    "cross": "<:cross:1516046974543990825>",
    "checkmark": "<:checkmark:1516046972765732954>",
    "serverbooster": "<:serverbooster:1516046970249285653>",
    "padlock": "<:padlock:1516046969158762558>",
    "arrowtoleft": "<:arrowtoleft:1516046967963127908>",
    "serverstats": "<:serverstats:1516046966541385851>",
    "vcconnected": "<:vcconnected:1516046964628918303>",
}

def build_embed(title: str, description: str = "", *, color: int = EMBED_COLOR, ctx=None, 
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
        pass
    return embed


def command_error_reporter(func):
    @functools.wraps(func)
    async def wrapper(self, ctx, *args, **kwargs):
        try:
            return await func(self, ctx, *args, **kwargs)
        except Exception as exc:
            where = f"{self.__class__.__name__}.{func.__name__}"
            log.exception("Unhandled exception in %s", where, exc_info=exc)
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            short_tb = tb if len(tb) < 400 else tb[:397] + "..."
            embed = build_embed("Internal Error",
                              f"An error occurred in `{where}`:\n```py\n{short_tb}\n```",
                              error=True,
                              ctx=ctx if isinstance(ctx, (commands.Context, discord.Interaction)) else None)
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


# ========================
# Fun Game Views (V2 Layout)
# ========================

class RockPaperScissorsView(discord.ui.View):
    """Interactive Rock Paper Scissors game using V2 layout"""
    def __init__(self, player: discord.User):
        super().__init__(timeout=30)
        self.player = player
        self.player_choice = None
        self.result_embed = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                f"{CUSTOM_EMOJIS['warning']} This game belongs to {self.player.mention}!", 
                ephemeral=True)
            return False
        return True

    async def play_game(self, interaction: discord.Interaction, choice: str):
        choices = ["rock", "paper", "scissors"]
        bot_choice = random.choice(choices)
        
        # Determine winner
        if choice == bot_choice:
            result = "It's a tie! 🤝"
            color = EMBED_COLOR
        elif (choice == "rock" and bot_choice == "scissors") or \
             (choice == "paper" and bot_choice == "rock") or \
             (choice == "scissors" and bot_choice == "paper"):
            result = f"You win! {CUSTOM_EMOJIS['checkmark']}"
            color = EMBED_SUCCESS
        else:
            result = f"You lose! {CUSTOM_EMOJIS['cross']}"
            color = EMBED_ERROR

        embed = build_embed("🎮 Rock Paper Scissors", color=color)
        embed.add_field(name=f"{CUSTOM_EMOJIS['member']} Your Choice", value=choice.title(), inline=True)
        embed.add_field(name=f"{CUSTOM_EMOJIS['botlabel']} Bot Choice", value=bot_choice.title(), inline=True)
        embed.add_field(name="Result", value=result, inline=False)
        
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="🪨", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_game(interaction, "rock")

    @discord.ui.button(emoji="📄", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_game(interaction, "paper")

    @discord.ui.button(emoji="✂️", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play_game(interaction, "scissors")


class ChooseOneView(discord.ui.View):
    """Interactive "Choose One" game with V2 layout"""
    def __init__(self, author: discord.User, option1: str, option2: str):
        super().__init__(timeout=30)
        self.author = author
        self.option1 = option1
        self.option2 = option2
        self.votes = {option1: set(), option2: set()}
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author.id != interaction.user.id:
            await interaction.response.send_message("Start your own game!", ephemeral=True)
            return False
        return True

    async def update_embed(self):
        if self.message:
            opt1_votes = len(self.votes[self.option1])
            opt2_votes = len(self.votes[self.option2])
            total = opt1_votes + opt2_votes
            
            embed = build_embed(
                f"{CUSTOM_EMOJIS['sparkels']} Choose One",
                f"**Option 1:** {self.option1}\n**Option 2:** {self.option2}"
            )
            
            if total > 0:
                pct1 = int((opt1_votes / total) * 100)
                pct2 = 100 - pct1
            else:
                pct1 = pct2 = 0
            
            embed.add_field(name="Votes", value=f"1️⃣: {opt1_votes} ({pct1}%)\n2️⃣: {opt2_votes} ({pct2}%)", inline=False)
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    @discord.ui.button(emoji="1️⃣", style=discord.ButtonStyle.secondary)
    async def opt1(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.votes[self.option1].add(interaction.user.id)
        await interaction.response.defer()
        await self.update_embed()

    @discord.ui.button(emoji="2️⃣", style=discord.ButtonStyle.secondary)
    async def opt2(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.votes[self.option2].add(interaction.user.id)
        await interaction.response.defer()
        await self.update_embed()

    @discord.ui.button(emoji="🛑", style=discord.ButtonStyle.danger)
    async def stop_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.defer()
        await self.update_embed()


# ========================
# Fun Cog
# ========================

class FunCog(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @command_error_reporter
    @commands.hybrid_command(name="8ball", description="Ask the magic 8-ball a question")
    @app_commands.describe(question="Your question for the 8-ball")
    async def magic_8ball(self, ctx: commands.Context, *, question: str):
        """Ask the magic 8-ball a question"""
        responses = [
            "Yes, definitely!", "It is certain.", "Most likely.",
            "Not sure yet.", "Ask again later.", "Don't count on it.",
            "No way!", "Absolutely not!", "Better not tell you now.",
            "Outlook good.", "Signs point to yes.", "Chances are slim.",
        ]
        answer = random.choice(responses)
        embed = build_embed(f"{CUSTOM_EMOJIS['sparkels']} Magic 8-Ball", answer, ctx=ctx, success=True)
        embed.add_field(name="Question", value=question, inline=False)
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="coinflip", description="Flip a coin")
    async def coinflip(self, ctx: commands.Context):
        """Flip a coin"""
        result = random.choice(["Heads", "Tails"])
        emoji = "🪙" if result == "Heads" else "🪙"
        embed = build_embed(f"{emoji} Coin Flip", f"**{result}**!", ctx=ctx, success=True)
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="dice", description="Roll a dice (1-6)")
    async def dice_roll(self, ctx: commands.Context, sides: int = 6):
        """Roll a dice with specified sides"""
        if sides < 2 or sides > 100:
            return await ctx.send(
                embed=build_embed("Invalid Sides", "Sides must be between 2 and 100", error=True, ctx=ctx)
            )
        result = random.randint(1, sides)
        embed = build_embed(f"{CUSTOM_EMOJIS['sparkels']} Dice Roll", f"🎲 **{result}** (1-{sides})", ctx=ctx)
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="rps", description="Play Rock Paper Scissors with the bot")
    async def rock_paper_scissors(self, ctx: commands.Context):
        """Play Rock Paper Scissors"""
        view = RockPaperScissorsView(ctx.author)
        embed = build_embed("🎮 Rock Paper Scissors", "Choose your move!", ctx=ctx)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @command_error_reporter
    @commands.hybrid_command(name="choose", description="Let the bot choose between two options")
    @app_commands.describe(option1="First option", option2="Second option")
    async def choose(self, ctx: commands.Context, option1: str, option2: str):
        """Bot chooses between two options"""
        choice = random.choice([option1, option2])
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} I Choose...",
            f"**{choice}**",
            ctx=ctx,
            success=True
        )
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="vote", description="Create a voting game with two options")
    @app_commands.describe(option1="First option", option2="Second option")
    async def vote(self, ctx: commands.Context, option1: str, option2: str):
        """Create a voting game"""
        view = ChooseOneView(ctx.author, option1, option2)
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} Choose One",
            f"**Option 1:** {option1}\n**Option 2:** {option2}\n\n1️⃣ vs 2️⃣"
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @command_error_reporter
    @commands.hybrid_command(name="compliment", description="Get a random compliment")
    @app_commands.describe(member="Person to compliment (optional)")
    async def compliment(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Get a random compliment"""
        compliments = [
            "You're an awesome person!",
            "Your creativity knows no bounds!",
            "You light up the room!",
            "You inspire others to be better!",
            "You have impeccable manners!",
            "You're a gift to those around you!",
            "You're a smart cookie!",
            "Your perspective is refreshing!",
            "You're a joy to be around!",
            "You bring out the best in other people!",
            "You're a treasure!",
            "You deserve a hug right now!",
        ]
        compliment_text = random.choice(compliments)
        target = member or ctx.author
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} Compliment for {target.display_name}",
            compliment_text,
            ctx=ctx,
            success=True
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="joke", description="Tell a random joke")
    async def joke(self, ctx: commands.Context):
        """Tell a random joke"""
        jokes = [
            ("Why don't scientists trust atoms?", "Because they make up everything!"),
            ("What did the ocean say to the beach?", "Nothing, it just waved."),
            ("Why did the scarecrow win an award?", "He was outstanding in his field!"),
            ("What do you call a fake noodle?", "An impasta!"),
            ("Why don't eggs tell jokes?", "They'd crack each other up!"),
            ("What's the best thing about Switzerland?", "I don't know, but the flag is a big plus."),
        ]
        setup, punchline = random.choice(jokes)
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} Joke",
            f"**{setup}**\n\n{punchline}",
            ctx=ctx
        )
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="rate", description="Rate something from 1-10")
    @app_commands.describe(thing="What to rate")
    async def rate(self, ctx: commands.Context, *, thing: str):
        """Rate something"""
        rating = random.randint(1, 10)
        stars = "⭐" * rating
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} Rating: {thing}",
            f"{rating}/10\n{stars}",
            ctx=ctx
        )
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="ship", description="Ship two people together")
    @app_commands.describe(person1="First person", person2="Second person")
    async def ship(self, ctx: commands.Context, person1: discord.Member, person2: discord.Member):
        """Ship two people together and calculate compatibility"""
        if person1 == person2:
            return await ctx.send(embed=build_embed(
                f"{CUSTOM_EMOJIS['warning']} Wait...", 
                "You can't ship someone with themselves!", 
                warning=True, ctx=ctx
            ))
        
        # Calculate "compatibility" based on user IDs (deterministic but seems random)
        compatibility = (person1.id + person2.id) % 101
        hearts = "💕" * (compatibility // 20 + 1)
        
        name = f"{person1.display_name[:5]}{person2.display_name[:5]}".title()
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} Shipping: {person1.display_name} + {person2.display_name}",
            f"**Ship name:** {name}\n**Compatibility:** {compatibility}%\n{hearts}",
            ctx=ctx
        )
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="emojis", description="View all custom server emojis")
    async def list_emojis(self, ctx: commands.Context):
        """List available custom emojis"""
        emoji_list = "\n".join([f"{emoji} `{name}`" for name, emoji in list(CUSTOM_EMOJIS.items())[:25]])
        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} Custom Emojis (1/2)",
            emoji_list,
            ctx=ctx
        )
        embed.set_footer(text=f"Total emojis: {len(CUSTOM_EMOJIS)}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))
    print("[fun_cog] Loaded — entertainment commands and games")
