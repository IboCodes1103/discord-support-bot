import os
import sqlite3
from pathlib import Path

import discord

from embed_theme import make_embed


database_path = Path(__file__).parent / "handoffs.sqlite3"

# Rating value that marks a declined feedback request.
DECLINED = 0
SKIP_CHOICE = "skip"

# Tickets that already received a feedback request while the bot is running.
requested_channels = set()


def setup_feedback_tables() -> None:
    """Create the feedback tables if they do not exist yet."""
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_owners (
                channel_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                channel_name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                channel_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL
            )
            """
        )


def save_ticket_owner(channel_id: int, user_id: int, channel_name: str) -> None:
    """Remember who opened a ticket. The first saved person is kept."""
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO ticket_owners "
            "(channel_id, user_id, channel_name) VALUES (?, ?, ?)",
            (channel_id, user_id, channel_name),
        )


def get_ticket_owner(channel_id: int) -> tuple[int, str] | None:
    """Return the user ID and channel name of a ticket."""
    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            "SELECT user_id, channel_name FROM ticket_owners "
            "WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()

    return result


def has_feedback(channel_id: int) -> bool:
    """Check whether a ticket already has an answer."""
    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            "SELECT 1 FROM feedback WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()

    return result is not None


def save_feedback(channel_id: int, user_id: int, rating: int) -> bool:
    """Save an answer. Returns False if the ticket was already answered."""
    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO feedback (channel_id, user_id, rating) "
            "VALUES (?, ?, ?)",
            (channel_id, user_id, rating),
        )

    return cursor.rowcount == 1


class FeedbackButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"feedback:(?P<channel_id>\d+):(?P<choice>[1-5]|skip)",
):
    """A star button that keeps working after the bot restarts."""

    def __init__(self, channel_id: int, choice: str, chosen: str | None = None):
        if choice == SKIP_CHOICE:
            label, emoji, row = "No thanks", None, 1
        else:
            label, emoji, row = choice, "⭐", 0

        # After an answer, the chosen button turns green and all are locked.
        if chosen is None:
            style = (
                discord.ButtonStyle.secondary
                if choice == SKIP_CHOICE
                else discord.ButtonStyle.primary
            )
        elif choice == chosen:
            style = discord.ButtonStyle.success
        else:
            style = discord.ButtonStyle.secondary

        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"feedback:{channel_id}:{choice}",
                row=row,
                disabled=chosen is not None,
            )
        )
        self.channel_id = channel_id
        self.choice = choice

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match,
        /,
    ):
        """Rebuild the button from its ID after a restart."""
        return cls(int(match["channel_id"]), match["choice"])

    async def callback(self, interaction: discord.Interaction):
        """Save the answer once and lock all buttons."""
        rating = DECLINED if self.choice == SKIP_CHOICE else int(self.choice)
        saved = save_feedback(self.channel_id, interaction.user.id, rating)

        # An empty value locks every button without highlighting one.
        chosen = self.choice if saved else ""
        await interaction.response.edit_message(
            view=build_feedback_view(self.channel_id, chosen=chosen)
        )

        if not saved:
            await interaction.followup.send(
                embed=make_embed(
                    title="Already submitted",
                    description="You have already answered for this ticket.",
                    color=discord.Color.orange(),
                )
            )
            return

        if rating == DECLINED:
            await interaction.followup.send(
                embed=make_embed(
                    title="Thank you anyway!",
                    description="You declined to give feedback. That's okay.",
                )
            )
            return

        await interaction.followup.send(
            embed=make_embed(
                title="Thank you for your feedback!",
                description=f"You rated your support experience {rating}/5.",
                color=discord.Color.green(),
            )
        )
        await post_feedback(interaction.client, self.channel_id, interaction.user, rating)


def build_feedback_view(channel_id: int, chosen: str | None = None) -> discord.ui.View:
    """Create the star buttons and the decline button."""
    view = discord.ui.View(timeout=None)

    for choice in ("1", "2", "3", "4", "5", SKIP_CHOICE):
        view.add_item(FeedbackButton(channel_id, choice, chosen))

    return view


async def send_feedback_request(client: discord.Client, channel_id: int) -> None:
    """Ask the person who opened a ticket for feedback by direct message."""
    if channel_id in requested_channels:
        return

    requested_channels.add(channel_id)
    owner = get_ticket_owner(channel_id)

    if owner is None:
        print(f"No ticket owner is saved for channel {channel_id}.")
        return

    if has_feedback(channel_id):
        return

    user_id, channel_name = owner

    embed = make_embed(
        title="How was your support experience?",
        description=(
            f"Your ticket **{channel_name}** has been closed.\n\n"
            "Please rate the support from 1 to 5 stars. "
            "You can only answer once."
        ),
    )

    try:
        user = await client.fetch_user(user_id)
        await user.send(embed=embed, view=build_feedback_view(channel_id))
    except discord.Forbidden:
        print(f"Could not send the feedback request for {channel_name}: DMs are closed.")
    except discord.HTTPException as error:
        print(f"Could not send the feedback request for {channel_name}: {error}")


async def post_feedback(
    client: discord.Client,
    ticket_channel_id: int,
    user: discord.abc.User,
    rating: int,
) -> None:
    """Post a rating in the feedback channel."""
    feedback_channel_id = os.getenv("FEEDBACK_CHANNEL_ID")

    if not feedback_channel_id or not feedback_channel_id.isdigit():
        print("A valid FEEDBACK_CHANNEL_ID was not found in the .env file.")
        return

    owner = get_ticket_owner(ticket_channel_id)
    ticket_name = owner[1] if owner else str(ticket_channel_id)

    embed = make_embed(
        title="New feedback",
        description=f"{'⭐' * rating}{'☆' * (5 - rating)}  **{rating}/5**",
    )
    embed.add_field(name="Ticket", value=ticket_name)
    embed.add_field(name="From", value=f"<@{user.id}>")

    try:
        channel = client.get_channel(int(feedback_channel_id))

        if channel is None:
            channel = await client.fetch_channel(int(feedback_channel_id))

        await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        print("Could not post feedback: missing channel permissions.")
    except discord.HTTPException as error:
        print(f"Could not post feedback: {error}")
