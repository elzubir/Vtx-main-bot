import asyncio
import functools
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Colors
EMBED_COLOR = 0x27272F
EMBED_SUCCESS = 0x57F287
EMBED_WARNING = 0xFEE75C
EMBED_ERROR = 0xED4245

# Custom Emojis
CUSTOM_EMOJIS = {
    "sparkels": "<:sparkels:1516046978188967966>",
    "checkmark": "<:checkmark:1516046972765732954>",
    "cross": "<:cross:1516046974543990825>",
    "warning": "<:warning:1516046980969664542>",
    "botlabel": "<:botlabel:1516046994249093191>",
}

# AI Configuration - Using Ollama (free, local)
OLLAMA_ENDPOINT = "http://localhost:11434"  # Default Ollama endpoint
OLLAMA_MODEL = "mistral"  # Free lightweight model, alternatives: "neural-chat", "orca-mini"

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
# AI Chat Cog
# ========================

class AICog(commands.Cog, name="AI"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ai_enabled = False
        self.check_ai_availability.start()

    def cog_unload(self):
        self.check_ai_availability.cancel()

    @commands.Cog.listener()
    async def check_ai_availability(self):
        """Background task to check if Ollama is available"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(f"{OLLAMA_ENDPOINT}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        self.ai_enabled = resp.status == 200
                except Exception:
                    self.ai_enabled = False
        except ImportError:
            self.ai_enabled = False
            log.warning("aiohttp not installed. AI features disabled. Install with: pip install aiohttp")

    async def query_ollama(self, prompt: str) -> Optional[str]:
        """Query Ollama for AI response"""
        if not self.ai_enabled:
            return None

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.7,
                }
                async with session.post(
                    f"{OLLAMA_ENDPOINT}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("response", "").strip()
                    else:
                        log.warning(f"Ollama API returned status {resp.status}")
                        return None
        except ImportError:
            log.warning("aiohttp not installed")
            return None
        except asyncio.TimeoutError:
            return "⏱️ Request timed out. The AI is thinking too hard!"
        except Exception as exc:
            log.exception("Error querying Ollama", exc_info=exc)
            return None

    @command_error_reporter
    @commands.hybrid_command(name="ask", description="Ask the AI a question")
    @app_commands.describe(question="Your question for the AI")
    async def ask_ai(self, ctx: commands.Context, *, question: str):
        """Ask the AI a question - requires Ollama running locally"""
        if not self.ai_enabled:
            embed = build_embed(
                f"{CUSTOM_EMOJIS['warning']} AI Not Available",
                f"The AI system is not available. Please ensure Ollama is installed and running.\n"
                f"Install Ollama from: https://ollama.ai\n\n"
                f"Then run: `ollama run {OLLAMA_MODEL}`",
                warning=True,
                ctx=ctx
            )
            return await ctx.send(embed=embed)

        # Show thinking indicator
        async with ctx.typing():
            response = await self.query_ollama(question)

        if not response:
            embed = build_embed(
                f"{CUSTOM_EMOJIS['cross']} AI Error",
                "Failed to get a response from the AI. Make sure Ollama is running!",
                error=True,
                ctx=ctx
            )
            return await ctx.send(embed=embed)

        # Truncate if too long
        if len(response) > 2000:
            embed = build_embed(
                f"{CUSTOM_EMOJIS['sparkels']} AI Response (Truncated)",
                response[:1900] + "\n\n*[Response truncated - too long]*",
                ctx=ctx
            )
        else:
            embed = build_embed(
                f"{CUSTOM_EMOJIS['sparkels']} AI Response",
                response,
                ctx=ctx
            )

        embed.add_field(name="Question", value=question[:256], inline=False)
        embed.set_footer(text=f"Powered by {OLLAMA_MODEL.title()} • Requested by {ctx.author}")
        await ctx.send(embed=embed)

    @command_error_reporter
    @commands.hybrid_command(name="chat", description="Chat with the AI in a thread")
    async def chat_ai(self, ctx: commands.Context):
        """Start a chat session with the AI"""
        if not self.ai_enabled:
            embed = build_embed(
                f"{CUSTOM_EMOJIS['warning']} AI Not Available",
                f"The AI system is not available. Please ensure Ollama is installed and running.\n"
                f"Install Ollama from: https://ollama.ai",
                warning=True,
                ctx=ctx
            )
            return await ctx.send(embed=embed)

        embed = build_embed(
            f"{CUSTOM_EMOJIS['sparkels']} AI Chat Started",
            f"A chat thread has been created. Reply to messages to chat with the AI!",
            ctx=ctx,
            success=True
        )
        embed.add_field(name="Info", value=f"Model: {OLLAMA_MODEL}\nEndpoint: {OLLAMA_ENDPOINT}", inline=False)
        
        msg = await ctx.send(embed=embed)
        
        try:
            thread = await msg.create_thread(name=f"AI Chat - {ctx.author.display_name}")
            welcome = await thread.send(
                embed=build_embed(
                    f"{CUSTOM_EMOJIS['sparkels']} Welcome to AI Chat",
                    f"Hi {ctx.author.mention}! I'm here to chat with you. Ask me anything!",
                )
            )
        except Exception as exc:
            log.exception("Failed to create chat thread", exc_info=exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages in AI chat threads"""
        if message.author.bot:
            return

        # Check if this is a thread with "AI Chat" in the name
        if isinstance(message.channel, discord.Thread) and "ai chat" in message.channel.name.lower():
            if len(message.content) < 5:
                return

            async with message.channel.typing():
                response = await self.query_ollama(message.content)

            if response:
                if len(response) > 2000:
                    response = response[:1900] + "\n*[truncated]*"
                await message.reply(response, mention_author=False)
            else:
                await message.reply(
                    f"{CUSTOM_EMOJIS['cross']} Sorry, I couldn't generate a response. Make sure Ollama is running!",
                    mention_author=False
                )

    @command_error_reporter
    @commands.hybrid_command(name="aistatus", description="Check AI system status")
    async def ai_status(self, ctx: commands.Context):
        """Check the status of the AI system"""
        status = "🟢 Online" if self.ai_enabled else "🔴 Offline"
        
        embed = build_embed(
            f"{CUSTOM_EMOJIS['botlabel']} AI System Status",
            f"**Status:** {status}\n**Model:** {OLLAMA_MODEL}\n**Endpoint:** {OLLAMA_ENDPOINT}",
            ctx=ctx,
            success=self.ai_enabled
        )
        
        if not self.ai_enabled:
            embed.description += "\n\n**To enable:**\n1. Install Ollama: https://ollama.ai\n2. Run: `ollama run mistral`"
        
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AICog(bot))
    print("[ai_cog] Loaded — AI chat features (Ollama integration)")
