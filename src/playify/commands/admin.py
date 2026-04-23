"""Administrative and setup slash commands."""

from ..core import *
from ..helpers.common import *
from ..ui.controller import update_controller

# /kaomoji command
@bot.tree.command(name="kaomoji", description="Enable/disable kawaii mode")
@app_commands.default_permissions(administrator=True)
async def toggle_kawaii(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    if state.locale == Locale.EN_X_KAWAII:
        state.locale = Locale.EN_US
    else:
        state.locale = Locale.EN_X_KAWAII
    state = (
        get_messages("kawaii_state_enabled", guild_id)
        if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
        else get_messages("kawaii_state_disabled", guild_id)
    )

    embed = Embed(
        description=get_messages("kawaii_toggle", guild_id, state=state),
        color=(
            0xFFB6C1
            if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
            else discord.Color.blue()
        ),
    )
    await interaction.response.send_message(
        silent=SILENT_MESSAGES, embed=embed, ephemeral=True
    )

@app_commands.default_permissions(administrator=True)
class SetupCommands(app_commands.Group):
    """Commands for setting up the bot on the server."""

    def __init__(self, bot: commands.Bot):
        super().__init__(
            name="setup",
            description="Set up bot features for the server.",
            default_permissions=discord.Permissions(administrator=True),
        )
        self.bot = bot

    @app_commands.command(
        name="controller",
        description="Sets a channel for the persistent music controller.",
    )
    @app_commands.describe(
        channel="The text channel for the controller. Defaults to the current channel if not specified."
    )
    async def controller(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Sets or updates the channel for the music controller."""
        if not interaction.guild:
            await interaction.response.send_message(
                get_messages("command.error.guild_only", interaction.guild.id),
                ephemeral=True,
                silent=SILENT_MESSAGES,
            )
            return

        target_channel = channel or interaction.channel
        guild_id = interaction.guild.id

        if get_guild_state(guild_id).controller_message_id:
            try:
                old_channel_id = get_guild_state(guild_id).controller_channel_id
                if old_channel_id:
                    old_channel = self.bot.get_channel(old_channel_id)
                    if old_channel:
                        old_message = await old_channel.fetch_message(
                            get_guild_state(guild_id).controller_message_id
                        )
                        await old_message.delete()
                        logger.info(
                            f"Deleted old controller message in guild {guild_id}"
                        )
            except (discord.NotFound, discord.Forbidden):
                pass

        get_guild_state(guild_id).controller_channel_id = target_channel.id
        get_guild_state(guild_id).controller_message_id = None

        await interaction.response.send_message(
            get_messages(
                "setup.controller.success",
                guild_id,
                channel_mention=target_channel.mention,
            ),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        await update_controller(self.bot, guild_id)

    @app_commands.command(
        name="allowlist", description="Restricts bot commands to specific channels."
    )
    @app_commands.describe(
        reset="Type 'default' to allow commands in all channels again.",
        channel1="The first channel to allow.",
        channel2="An optional second channel to allow.",
        channel3="An optional third channel to allow.",
        channel4="An optional fourth channel to allow.",
        channel5="An optional fifth channel to allow.",
    )
    async def allowlist(
        self,
        interaction: discord.Interaction,
        reset: Optional[str] = None,
        channel1: Optional[discord.TextChannel] = None,
        channel2: Optional[discord.TextChannel] = None,
        channel3: Optional[discord.TextChannel] = None,
        channel4: Optional[discord.TextChannel] = None,
        channel5: Optional[discord.TextChannel] = None,
    ):

        guild_id = interaction.guild.id
        state = get_guild_state(guild_id)
        is_kawaii = state.locale == Locale.EN_X_KAWAII

        # Case 1: Reset the allowlist
        if reset and reset.lower() == "default":
            state = get_guild_state(guild_id)
            if state.allowed_channels:
                state.allowed_channels.clear()
                logger.info(
                    f"Command channel allowlist has been RESET for guild {guild_id}."
                )

            embed = discord.Embed(
                description=get_messages("setup.allowlist.reset_success", guild_id),
                color=0xB5EAD7 if is_kawaii else discord.Color.green(),
            )
            await interaction.response.send_message(
                embed=embed, ephemeral=True, silent=True
            )
            return

        # Case 2: Set the allowlist
        channels = [
            ch
            for ch in [channel1, channel2, channel3, channel4, channel5]
            if ch is not None
        ]

        if channels:
            allowed_ids = {ch.id for ch in channels}
            get_guild_state(guild_id).allowed_channels = allowed_ids

            channel_mentions = ", ".join([ch.mention for ch in channels])
            logger.info(
                f"Command channel allowlist for guild {guild_id} set to: {allowed_ids}"
            )

            embed = discord.Embed(
                description=get_messages(
                    "setup.allowlist.set_success", guild_id, channels=channel_mentions
                ),
                color=0xB5EAD7 if is_kawaii else discord.Color.green(),
            )
            await interaction.response.send_message(
                embed=embed, ephemeral=True, silent=True
            )
            return

        # Case 3: Invalid arguments
        embed = discord.Embed(
            description=get_messages("setup.allowlist.invalid_args", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=True
        )
