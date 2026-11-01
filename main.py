import asyncio
import json
import os
import sqlite3
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import discord
from discord import app_commands
from dotenv import load_dotenv

from embed_theme import make_embed
from feedback import (
    FeedbackButton,
    save_ticket_owner,
    send_feedback_request,
    setup_feedback_tables,
)
from gemini_backend import ask_gemini
from groq_backend import ask_groq
from knowledge_search import detect_smalltalk, search_knowledge
from ticket_actions import ResolvedTicketView


# Load the configuration from the local .env file.
load_dotenv()
token = os.getenv("DISCORD_TOKEN")

if not token:
    raise ValueError("Discord token not found. Check your .env file.")

# Locate the project files.
project_path = Path(__file__).parent
knowledge_path = project_path / "knowledge.json"
database_path = project_path / "handoffs.sqlite3"

# Load the knowledge base.
with knowledge_path.open("r", encoding="utf-8") as file:
    knowledge_base = json.load(file)

# Create the database table if it does not exist yet.
with sqlite3.connect(database_path) as connection:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS handoffs (
            channel_id INTEGER PRIMARY KEY
        )
        """
    )

# Create the feedback tables if they do not exist yet.
setup_feedback_tables()

# Create the Discord client and command tree.
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Keep the feedback buttons working after the bot restarts.
client.add_dynamic_items(FeedbackButton)

# Track tickets that are waiting for an AI response.
busy_channels = set()

# Define the embed colors.
COLOR_INFO = discord.Color.blue()
COLOR_SUCCESS = discord.Color.green()
COLOR_WARNING = discord.Color.orange()


def find_answer(question: str) -> str | None:
    """Find the best FAQ answer for a single question."""
    matches = search_knowledge(
        question,
        knowledge_base,
        max_questions=1,
        require_question=False,
    )

    if not matches:
        return None

    return matches[0].entry["answer"]


def is_ticket_channel(channel) -> bool:
    """Check whether a channel belongs to the test ticket category."""
    if not isinstance(channel, discord.TextChannel):
        return False

    test_guild_id = os.getenv("TEST_GUILD_ID")
    ticket_category_id = os.getenv("TICKET_CATEGORY_ID")

    return (
        str(channel.guild.id) == test_guild_id
        and str(channel.category_id) == ticket_category_id
    )


def has_handoff(channel_id: int) -> bool:
    """Check whether human support was requested in a channel."""
    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            "SELECT 1 FROM handoffs WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()

    return result is not None


def get_ai_provider() -> str:
    """Read the selected AI provider from the .env file."""
    return os.getenv("AI_PROVIDER", "groq").strip().casefold()


def ask_local_model(question: str, reference: str) -> str:
    """Generate an experimental answer using the selected AI provider."""
    provider = get_ai_provider()

    if provider == "groq":
        return ask_groq(question, reference)

    if provider == "gemini":
        return ask_gemini(question, reference)

    raise URLError(
        "AI_PROVIDER must be set to either 'groq' or 'gemini'."
    )


@client.event
async def on_ready():
    """Sync commands to the test server when the bot connects."""
    guild_id = os.getenv("TEST_GUILD_ID")

    if not guild_id or not guild_id.isdigit():
        print("A valid TEST_GUILD_ID was not found in the .env file.")
        return

    test_guild = discord.Object(id=int(guild_id))
    tree.copy_global_to(guild=test_guild)
    synced_commands = await tree.sync(guild=test_guild)

    print(f"Bot is online as: {client.user}")
    print(f"Synced {len(synced_commands)} commands to the test server.")


@client.event
async def on_guild_channel_create(channel):
    """Welcome users when a new ticket channel is created."""
    if not is_ticket_channel(channel):
        return

    # Give Discord a moment to finish creating the channel.
    await asyncio.sleep(2)

    embed = make_embed(
        title="Welcome to Support",
        description=(
            "Hello! I'm an experimental support bot.\n\n"
            "Send your question in this ticket and I'll check "
            "the local knowledge base.\n\n"
            "Need a team member? Use **/request-human** "
            "or click the button below."
        ),
    )
    embed.set_footer(text="Experimental support assistant")

    view = discord.ui.View(timeout=None)
    view.add_item(RequestHumanButton())

    try:
        await channel.send(embed=embed, view=view)
        print(f"Welcome message sent in: {channel.name}")
    except discord.Forbidden:
        print(
            f"Could not send a welcome message in {channel.name}: "
            "missing channel permissions."
        )
    except discord.HTTPException as error:
        print(f"Could not send a welcome message: {error}")


@client.event
async def on_guild_channel_delete(channel):
    """Ask the person who opened a ticket for feedback when it is deleted."""
    if not is_ticket_channel(channel):
        return

    await send_feedback_request(client, channel.id)


@client.event
async def on_message(message):
    """Answer ticket messages from non-staff users."""
    # Remember who opened a ticket from the Ticket Tool welcome message.
    ticket_tool_id = os.getenv("TICKET_TOOL_BOT_ID", "557628352828014614")

    if (
        message.guild is not None
        and str(message.author.id) == ticket_tool_id
        and is_ticket_channel(message.channel)
    ):
        opener = next((user for user in message.mentions if not user.bot), None)

        if opener is not None:
            save_ticket_owner(message.channel.id, opener.id, message.channel.name)

    # Ignore bots and direct messages.
    if message.author.bot or message.guild is None:
        return

    if not is_ticket_channel(message.channel):
        return

    # Ignore messages from members with the configured staff role.
    staff_role_id = os.getenv("STAFF_ROLE_ID")

    if not staff_role_id or not staff_role_id.isdigit():
        print("A valid STAFF_ROLE_ID was not found in the .env file.")
        return

    if not isinstance(message.author, discord.Member):
        return

    is_staff = any(
        role.id == int(staff_role_id)
        for role in message.author.roles
    )

    if is_staff:
        return

    channel_id = message.channel.id

    # Stop automated replies after a human has been requested.
    if has_handoff(channel_id):
        return

    question = message.content.strip()

    if not question:
        return

    if channel_id in busy_channels:
        embed = make_embed(
            title="One moment",
            description=(
                "I'm still working on the previous question. "
                "Please wait for my response."
            ),
            color=COLOR_WARNING,
        )
        await message.channel.send(embed=embed)
        return

    # Look for known questions anywhere in the message.
    matches = search_knowledge(question, knowledge_base)

    if not matches:
        # Answer greetings and thanks in a friendly way without the AI.
        smalltalk = detect_smalltalk(question)

        if smalltalk == "greeting":
            embed = make_embed(
                title="Hello!",
                description=(
                    "Please send your question and I'll try to answer it. "
                    "If you'd rather talk to a team member, use "
                    "**/request-human**."
                ),
            )
            await message.channel.send(embed=embed)
            return

        if smalltalk == "thanks":
            embed = make_embed(
                title="You're welcome!",
                description=(
                    "If you have more questions, just send them here. "
                    "Have a nice day!"
                ),
                color=COLOR_SUCCESS,
            )
            await message.channel.send(embed=embed)
            return

        embed = make_embed(
            title="No matching answer found",
            description=(
                "I couldn't find a clear match in the knowledge base. "
                "Try asking in a different way, or use "
                "**/request-human** to ask the team for help."
            ),
            color=COLOR_WARNING,
        )
        await message.channel.send(embed=embed)
        return

    busy_channels.add(channel_id)

    try:
        # Show the matching FAQ entries as the primary answer.
        faq_embeds = []

        for match in matches:
            faq_embed = make_embed(
                title="Answer from the FAQ",
                description=match.entry["answer"][:4000],
            )
            faq_embed.set_footer(
                text=f"Source: {match.entry.get('source', 'local knowledge base')}"
            )
            faq_embeds.append(faq_embed)

        # The AI only receives the found questions and entries, not the whole message.
        ai_question = "\n".join(
            f"{number}. {match.question}"
            for number, match in enumerate(matches, start=1)
        )
        reference = "\n\n".join(match.entry["answer"] for match in matches)

        await message.channel.send(
            embeds=faq_embeds,
            view=ResolvedTicketView(owner_id=message.author.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

        # Do not start the AI response if a handoff is already active.
        if has_handoff(channel_id):
            return

        # Generate a separate experimental answer with the selected AI provider.
        async with message.channel.typing():
            ai_answer = await asyncio.to_thread(
                ask_local_model,
                ai_question,
                reference,
            )

        # A user may have requested human support while the model was working.
        if has_handoff(channel_id):
            return

        if not ai_answer:
            print("The AI provider returned an empty response.")
            return

        provider_name = {
            "groq": "Groq",
            "gemini": "Gemini",
        }.get(get_ai_provider(), "AI")

        ai_description = ai_answer[:3700]

        # Point to the team if the AI could not answer from the FAQ.
        normalized_answer = ai_answer.casefold().replace("’", "'")

        if any(
            phrase in normalized_answer
            for phrase in ("i don't know", "i do not know")
        ):
            ai_description += (
                "\n\nIf you need more help, use **/request-human** "
                "to ask the support team."
            )

        ai_embed = make_embed(
            title=f"Experimental AI response ({provider_name})",
            description=ai_description,
        )
        ai_embed.set_footer(
            text="Experimental: the AI response may be incorrect."
        )

        await message.channel.send(
            embed=ai_embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        # The FAQ answer has already been sent if the model request fails.
        print(f"AI request failed: {error}")

    except discord.Forbidden:
        print(
            f"Could not send a message in {message.channel.name}: "
            "missing channel permissions."
        )

    except discord.HTTPException as error:
        print(f"Could not send a Discord message: {error}")

    finally:
        busy_channels.discard(channel_id)


@tree.command(name="ping", description="Check if the support bot is online.")
async def ping(interaction: discord.Interaction):
    """Respond to the /ping command."""
    embed = make_embed(
        title="Pong!",
        description="The support bot is online.",
        color=COLOR_SUCCESS,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="ask", description="Ask the local knowledge base a question.")
@app_commands.describe(question="Your question for the support bot")
async def ask(interaction: discord.Interaction, question: str):
    """Search the knowledge base for an answer."""
    answer = find_answer(question)

    if answer:
        embed = make_embed(
            title="Knowledge base answer",
            description=answer[:4000],
        )
        embed.set_footer(text="Source: local knowledge base")
    else:
        embed = make_embed(
            title="No matching answer found",
            description="I couldn't find a clear match in the knowledge base.",
            color=COLOR_WARNING,
        )

    await interaction.response.send_message(
        embed=embed,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def request_human_support(interaction: discord.Interaction):
    """Notify the support team and pause automated ticket replies."""
    channel = interaction.channel

    if not is_ticket_channel(channel):
        embed = make_embed(
            title="Ticket required",
            description="This command can only be used in a support ticket.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if has_handoff(channel.id):
        embed = make_embed(
            title="Request already sent",
            description=(
                "A human support request has already been made "
                "for this ticket."
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    role_id = os.getenv("STAFF_ROLE_ID")

    if not role_id or not role_id.isdigit():
        embed = make_embed(
            title="Support role not configured",
            description="Please contact a moderator for help.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    staff_role = interaction.guild.get_role(int(role_id))

    if staff_role is None:
        embed = make_embed(
            title="Support role not found",
            description="Please contact a moderator for help.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Check whether the bot can mention this role.
    bot_member = interaction.guild.me
    can_mention = (
        bot_member is not None
        and (
            staff_role.mentionable
            or channel.permissions_for(bot_member).mention_everyone
        )
    )

    if not can_mention:
        embed = make_embed(
            title="Unable to notify the team",
            description=(
                "I cannot mention the support role. "
                "Please contact a moderator."
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Save the handoff before sending the notification.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO handoffs (channel_id) VALUES (?)",
            (channel.id,),
        )

    embed = make_embed(
        title="Human support requested",
        description=(
            "A team member has been requested. "
            "Automated replies are now paused in this ticket."
        ),
        color=COLOR_SUCCESS,
    )

    try:
        await interaction.response.send_message(
            content=staff_role.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                roles=[staff_role],
                users=False,
                everyone=False,
            ),
        )
    except discord.HTTPException:
        # Allow the user to retry if the notification could not be sent.
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "DELETE FROM handoffs WHERE channel_id = ?",
                (channel.id,),
            )
        print(f"Could not send the human support request in: {channel.name}")


@tree.command(
    name="request-human",
    description="Request help from a human support team member.",
)
async def request_human(interaction: discord.Interaction):
    """Respond to the /request-human command."""
    await request_human_support(interaction)


class RequestHumanButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"request-human",
):
    """A button that works like the /request-human command."""

    def __init__(self):
        super().__init__(
            discord.ui.Button(
                label="Request human",
                emoji="🙋",
                style=discord.ButtonStyle.secondary,
                custom_id="request-human",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match,
        /,
    ):
        """Rebuild the button after the bot restarts."""
        return cls()

    async def callback(self, interaction: discord.Interaction):
        """Use the same steps as the /request-human command."""
        await request_human_support(interaction)


# Keep the button in old welcome messages working after a restart.
client.add_dynamic_items(RequestHumanButton)


@tree.command(
    name="resume-bot",
    description="Resume automated support replies in this ticket.",
)
async def resume_bot(interaction: discord.Interaction):
    """Allow staff to resume automated replies in a ticket."""
    channel = interaction.channel

    if not is_ticket_channel(channel):
        embed = make_embed(
            title="Ticket required",
            description="This command can only be used in a support ticket.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    role_id = os.getenv("STAFF_ROLE_ID")

    if not role_id or not role_id.isdigit():
        embed = make_embed(
            title="Support role not configured",
            description="The support role is not configured.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    member = interaction.user

    if not isinstance(member, discord.Member):
        embed = make_embed(
            title="Unable to verify membership",
            description="Your server membership could not be verified.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    has_staff_role = any(
        role.id == int(role_id) for role in member.roles
    )

    if not has_staff_role:
        embed = make_embed(
            title="Staff access required",
            description="Only the support team can resume automated replies.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not has_handoff(channel.id):
        embed = make_embed(
            title="Bot already active",
            description="Automated replies are already active in this ticket.",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Remove the handoff state for this ticket.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM handoffs WHERE channel_id = ?",
            (channel.id,),
        )

    embed = make_embed(
        title="Bot support resumed",
        description="Automated support replies have been resumed in this ticket.",
        color=COLOR_SUCCESS,
    )
    await interaction.response.send_message(embed=embed)


# Connect the bot to Discord.
client.run(token)