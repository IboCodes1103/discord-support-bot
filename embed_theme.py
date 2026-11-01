import discord


# Use consistent colors across all support embeds.
COLOR_INFO = discord.Color(0x5865F2)
COLOR_SUCCESS = discord.Color(0x2ECC71)
COLOR_WARNING = discord.Color(0xF0A23B)

# Add a recognizable icon to each existing embed title.
TITLE_ICONS = {
    "Welcome to Support": "👋",
    "How was your support experience?": "⭐",
    "Thank you for your feedback!": "💛",
    "Thank you anyway!": "👋",
    "Already submitted": "ℹ️",
    "New feedback": "📝",
    "Hello!": "👋",
    "You're welcome!": "😊",
    "One moment": "⏳",
    "No matching answer found": "🔎",
    "No answer generated": "⚠️",
    "AI model unavailable": "⚠️",
    "Answer from the FAQ": "📚",
    "Experimental AI response (Groq)": "🤖",
    "Experimental AI response (Gemini)": "🤖",
    "Pong!": "🏓",
    "Knowledge base answer": "📚",
    "Ticket required": "🎫",
    "Request already sent": "📨",
    "Support role not configured": "⚠️",
    "Support role not found": "⚠️",
    "Unable to notify the team": "⚠️",
    "Human support requested": "🙋",
    "Unable to verify membership": "⚠️",
    "Staff access required": "🔒",
    "Bot already active": "ℹ️",
    "Bot support resumed": "✅",
    "Not available": "🔒",
    "Ticket not available": "🎫",
    "Unable to check ticket status": "⚠️",
    "Human support requested": "🙋",
    "Request already started": "⏳",
    "Processing ticket": "⚙️",
    "Ticket action stopped": "⚠️",
    "Close request cancelled": "↩️",
    "Close and delete this ticket?": "🗑️",
}


def make_embed(
    title: str,
    description: str,
    color: discord.Color = discord.Color.blue(),
) -> discord.Embed:
    """Create a consistently styled support embed."""
    colors = {
        discord.Color.blue().value: COLOR_INFO,
        discord.Color.green().value: COLOR_SUCCESS,
        discord.Color.orange().value: COLOR_WARNING,
    }

    selected_color = colors.get(color.value, color)
    icon = TITLE_ICONS.get(title, "💬")

    embed = discord.Embed(
        title=f"{icon}  {title}",
        description=description,
        color=selected_color,
    )
    embed.set_footer(text="AzubiVerse Support")

    return embed