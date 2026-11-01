import asyncio
import os
import sqlite3
from pathlib import Path

import discord
from embed_theme import make_embed


# Keep track of close requests while the bot is running.
active_closures = set()
finished_requests = set()

database_path = Path(__file__).parent / "handoffs.sqlite3"


def has_active_handoff(channel_id: int) -> bool:
    """Check whether human support has been requested."""
    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            "SELECT 1 FROM handoffs WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()

    return result is not None


def is_configured_ticket(channel) -> bool:
    """Check whether the channel is in the configured test category."""
    if not isinstance(channel, discord.TextChannel):
        return False

    guild_id = os.getenv("TEST_GUILD_ID")
    category_id = os.getenv("TICKET_CATEGORY_ID")

    return (
        str(channel.guild.id) == guild_id
        and str(channel.category_id) == category_id
    )


def warning_embed(title: str, description: str) -> discord.Embed:
    """Create a warning embed."""
    return make_embed(
        title=title,
        description=description,
        color=discord.Color.orange(),
    )


class CloseConfirmationView(discord.ui.View):
    """Ask the question author to confirm ticket deletion."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id

    @discord.ui.button(
        label="Confirm close and delete",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """Send the Ticket Tool commands after confirmation."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Not available",
                    "Only the person who asked the question can confirm this.",
                ),
                ephemeral=True,
            )
            return

        channel = interaction.channel

        if not is_configured_ticket(channel):
            await interaction.response.send_message(
                embed=warning_embed(
                    "Ticket not available",
                    "This action is only available in a configured test ticket.",
                ),
                ephemeral=True,
            )
            return

        channel_id = channel.id

        try:
            handoff_active = has_active_handoff(channel_id)
        except sqlite3.Error:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Unable to check ticket status",
                    "Please contact the support team.",
                ),
                ephemeral=True,
            )
            return

        if handoff_active:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Human support requested",
                    "The support team is handling this ticket. "
                    "Please ask them before closing it.",
                ),
                ephemeral=True,
            )
            return

        if channel_id in active_closures or channel_id in finished_requests:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Request already started",
                    "A close request has already been started for this ticket.",
                ),
                ephemeral=True,
            )
            return

        # Prevent simultaneous requests while this process is running.
        active_closures.add(channel_id)

        try:
            processing_embed = make_embed(
                title="Processing ticket",
                description=(
                    "The bot will send the close, transcript, "
                    "and delete commands to Ticket Tool."
                ),
                color=discord.Color.blue(),
            )

            await interaction.response.edit_message(
                embed=processing_embed,
                view=None,
            )

            await channel.send("$close")
            await asyncio.sleep(4)

            await channel.send("$transcript")
            await asyncio.sleep(4)

            await channel.send("$delete")

            # This records that commands were sent, not that they succeeded.
            finished_requests.add(channel_id)
            print(
                f"Ticket Tool commands sent for channel {channel_id}. "
                "Check Ticket Tool and the transcript channel."
            )

        except discord.Forbidden:
            print(
                f"Ticket action stopped for channel {channel_id}: "
                "missing channel permissions."
            )

            try:
                await interaction.followup.send(
                    embed=warning_embed(
                        "Ticket action stopped",
                        "The bot could not send a command. "
                        "Please contact the support team.",
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

        except discord.HTTPException as error:
            print(f"Ticket action stopped for channel {channel_id}: {error}")

            try:
                await interaction.followup.send(
                    embed=warning_embed(
                        "Ticket action stopped",
                        "Discord returned an error. "
                        "Please contact the support team.",
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

        finally:
            active_closures.discard(channel_id)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """Cancel the close request."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Not available",
                    "Only the person who asked the question can cancel this.",
                ),
                ephemeral=True,
            )
            return

        embed = make_embed(
            title="Close request cancelled",
            description="The ticket has not been deleted by this action.",
            color=discord.Color.blue(),
        )

        await interaction.response.edit_message(embed=embed, view=None)


class ResolvedTicketView(discord.ui.View):
    """Show a button for the person who asked the question."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id

    @discord.ui.button(
        label="Thanks, my question was answered",
        style=discord.ButtonStyle.success,
    )
    async def resolved(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """Ask for confirmation before sending ticket commands."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Not available",
                    "Only the person who asked the question can use this button.",
                ),
                ephemeral=True,
            )
            return

        embed = warning_embed(
            title="Close and delete this ticket?",
            description=(
                "If you confirm, the bot will ask Ticket Tool to close "
                "the ticket, request a transcript, and delete the channel.\n\n"
                "**Transcript delivery is not automatically verified.** "
                "Only continue with a test ticket."
            ),
        )

        await interaction.response.send_message(
            embed=embed,
            view=CloseConfirmationView(owner_id=self.owner_id),
            ephemeral=True,
        )