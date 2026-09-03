from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dofus_pvp_bot.analysis.team_balance import ImageEvidence, TeamBalanceDetector
from dofus_pvp_bot.application.leaderboards import LeaderboardService
from dofus_pvp_bot.application.submissions import SubmissionService
from dofus_pvp_bot.config import BotSettings
from dofus_pvp_bot.discord_app.presentation import (
    build_leaderboard_embed,
    build_review_embed,
    submission_reference,
)
from dofus_pvp_bot.discord_app.review_images import prepare_review_images
from dofus_pvp_bot.discord_app.views import ReviewView, StartSubmissionView
from dofus_pvp_bot.domain.leaderboard import MonthPeriod
from dofus_pvp_bot.domain.models import (
    DetectionResult,
    Submission,
    SubmissionLane,
    SubmissionStatus,
)

LOGGER = logging.getLogger(__name__)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _is_image(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    return Path(attachment.filename).suffix.lower() in IMAGE_SUFFIXES


class DofusPvpBot(commands.Bot):
    def __init__(
        self,
        settings: BotSettings,
        service: SubmissionService,
        leaderboard_service: LeaderboardService,
        detector: TeamBalanceDetector,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.service = service
        self.leaderboard_service = leaderboard_service
        self.detector = detector

        @self.tree.command(
            name="classement",
            description="Affiche le classement PvP mensuel.",
        )
        @app_commands.describe(mois="Mois au format AAAA-MM (mois courant par défaut)")
        async def classement(
            interaction: discord.Interaction,
            mois: str | None = None,
        ) -> None:
            await self._show_leaderboard(interaction, mois)

        @classement.autocomplete("mois")
        async def classement_months(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            del interaction
            return self._month_choices(current)

    async def setup_hook(self) -> None:
        await self.service.repository.initialize()

        drafts = await self.service.repository.list_by_status(SubmissionStatus.DRAFT)
        for submission in drafts:
            if submission.prompt_message_id is not None:
                self.add_view(
                    StartSubmissionView(self.service, self.settings, submission.id),
                    message_id=submission.prompt_message_id,
                )

        pending = await self.service.repository.list_by_status(SubmissionStatus.PENDING_REVIEW)
        for submission in pending:
            if submission.review_message_id is not None:
                self.add_view(
                    ReviewView(
                        self.service,
                        self.settings,
                        submission.id,
                        can_approve=(
                            submission.fight_balance is not None
                            and submission.points is not None
                        ),
                    ),
                    message_id=submission.review_message_id,
                )
        LOGGER.info(
            "Vues restaurées : %s brouillon(s), %s validation(s)",
            len(drafts),
            len(pending),
        )
        await self._sync_guild_commands()
        if not self.close_monthly_leaderboards.is_running():
            self.close_monthly_leaderboards.start()

    async def _sync_guild_commands(self) -> None:
        channel = await self.fetch_channel(self.settings.review_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise RuntimeError("Le salon de vérification doit être un salon textuel.")
        guild = discord.Object(id=channel.guild.id)
        self.tree.copy_global_to(guild=guild)
        commands_synced = await self.tree.sync(guild=guild)
        LOGGER.info("Commandes Discord synchronisées : %s", len(commands_synced))

    async def _show_leaderboard(
        self,
        interaction: discord.Interaction,
        month_value: str | None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Cette commande doit être utilisée sur le serveur.", ephemeral=True
            )
            return
        try:
            period = (
                MonthPeriod.parse(month_value)
                if month_value
                else MonthPeriod.current(self.settings.leaderboard_timezone)
            )
            leaderboard = await self.leaderboard_service.get(interaction.guild_id, period)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_leaderboard_embed(leaderboard),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _month_choices(self, current: str) -> list[app_commands.Choice[str]]:
        period = MonthPeriod.current(self.settings.leaderboard_timezone)
        choices: list[app_commands.Choice[str]] = []
        search = current.casefold().strip()
        for _ in range(18):
            if not search or search in period.key or search in period.label.casefold():
                choices.append(app_commands.Choice(name=period.label, value=period.key))
            period = period.previous()
        return choices[:25]

    @tasks.loop(minutes=5)
    async def close_monthly_leaderboards(self) -> None:
        guild_ids = await self.service.repository.list_leaderboard_guild_ids()
        for guild_id in guild_ids:
            await self.leaderboard_service.close_due_months(guild_id)

    @close_monthly_leaderboards.error
    async def close_monthly_leaderboards_error(self, error: BaseException) -> None:
        LOGGER.exception("Échec de la clôture automatique des classements", exc_info=error)

    @close_monthly_leaderboards.before_loop
    async def before_close_monthly_leaderboards(self) -> None:
        await self.wait_until_ready()

    async def close(self) -> None:
        self.close_monthly_leaderboards.cancel()
        await super().close()

    async def on_ready(self) -> None:
        if self.user is not None:
            LOGGER.info("Bot connecté : %s (%s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        lane_by_channel = {
            self.settings.normal_submission_channel_id: SubmissionLane.NORMAL,
            self.settings.sng_submission_channel_id: SubmissionLane.SNG,
        }
        lane = lane_by_channel.get(message.channel.id)
        if lane is None:
            return

        screenshots = [attachment for attachment in message.attachments if _is_image(attachment)]
        if not screenshots:
            return
        if len(screenshots) > self.settings.max_screenshots:
            await message.reply(
                f"❌ Maximum {self.settings.max_screenshots} capture(s) par soumission.",
                mention_author=False,
                delete_after=20,
            )
            return
        oversized = [
            attachment
            for attachment in screenshots
            if attachment.size > self.settings.max_image_bytes
        ]
        if oversized:
            limit_mb = self.settings.max_image_bytes / (1024 * 1024)
            await message.reply(
                f"❌ Chaque capture doit faire au maximum {limit_mb:g} Mo.",
                mention_author=False,
                delete_after=20,
            )
            return

        existing = await self.service.repository.get_by_source_message_id(message.id)
        if existing is not None:
            return

        try:
            images = [
                ImageEvidence(
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    content=await attachment.read(),
                )
                for attachment in screenshots
            ]
            detection = await self.detector.detect(images)
        except (discord.DiscordException, ValueError) as exc:
            LOGGER.warning("Analyse impossible pour le message %s : %s", message.id, exc)
            detection = DetectionResult(
                fight_balance=None,
                detail="L’analyse automatique n’a pas pu être effectuée.",
            )

        submission = await self.service.create_draft(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            message_id=message.id,
            submitter_id=message.author.id,
            lane=lane,
            detection=detection,
        )
        if submission.fight_balance is None:
            analysis_text = (
                "L’analyse n’a pas pu comparer les effectifs. "
                "Le staff les renseignera dans le salon de validation."
            )
        else:
            counts = ""
            if (
                submission.detected_allies_count is not None
                and submission.detected_opponents_count is not None
            ):
                counts = (
                    f"**{submission.detected_allies_count} allié(s) contre "
                    f"{submission.detected_opponents_count} adversaire(s)** · "
                )
            analysis_text = (
                f"Résultat détecté : {counts}**{submission.fight_balance.label}**."
            )
        prompt_embed = discord.Embed(
            title="Nouvelle soumission détectée",
            description=(
                f"{analysis_text}\n"
                "L’auteur peut maintenant sélectionner les joueurs auxquels attribuer les points."
            ),
            colour=discord.Colour.blue(),
        )
        prompt_embed.set_footer(text=f"Référence {submission_reference(submission)}")
        prompt = await message.reply(
            embed=prompt_embed,
            view=StartSubmissionView(self.service, self.settings, submission.id),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.service.set_prompt_message(submission.id, prompt.id)
        try:
            await message.add_reaction("⏳")
        except discord.DiscordException:
            LOGGER.warning("Impossible d’ajouter une réaction au message %s", message.id)

    async def publish_for_review(self, submission: Submission) -> None:
        review_channel = self.get_channel(self.settings.review_channel_id)
        if review_channel is None:
            review_channel = await self.fetch_channel(self.settings.review_channel_id)
        if not isinstance(review_channel, (discord.TextChannel, discord.Thread)):
            raise RuntimeError("Le salon de vérification doit être un salon textuel.")

        source_channel = self.get_channel(submission.source_channel_id)
        if source_channel is None:
            source_channel = await self.fetch_channel(submission.source_channel_id)
        if not isinstance(source_channel, (discord.TextChannel, discord.Thread)):
            raise RuntimeError("Le salon source doit être un salon textuel.")
        source_message = await source_channel.fetch_message(submission.source_message_id)
        image_attachments = [
            attachment for attachment in source_message.attachments if _is_image(attachment)
        ]
        if not image_attachments:
            raise RuntimeError("La capture d’origine est introuvable.")

        source_images = [
            ImageEvidence(
                filename=attachment.filename,
                content_type=attachment.content_type,
                content=await attachment.read(),
            )
            for attachment in image_attachments
        ]
        try:
            prepared_images = await asyncio.to_thread(
                prepare_review_images,
                source_images,
                max_total_bytes=self.settings.max_review_upload_bytes,
            )
        except ValueError as exc:
            LOGGER.warning(
                "Copies de validation impossibles pour la soumission %s : %s",
                submission.id,
                exc,
            )
            prepared_images = []
        files = [
            discord.File(BytesIO(image.content), filename=image.filename)
            for image in prepared_images
        ]
        points = (
            self.service.calculate(submission)
            if submission.fight_balance is not None
            else None
        )
        submission.points = points
        review_embed = build_review_embed(submission)
        if files:
            review_embed.set_image(url=f"attachment://{files[0].filename}")
        review_view = ReviewView(
            self.service,
            self.settings,
            submission.id,
            can_approve=points is not None,
        )
        review_content = (
            f"Soumission de <@{submission.submitter_id}> · "
            f"[ouvrir la capture d’origine]({source_message.jump_url})"
        )
        try:
            review_message = await review_channel.send(
                content=review_content,
                embed=review_embed,
                files=files,
                view=review_view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            if exc.code != 40005:
                raise
            LOGGER.warning(
                "Pièces jointes refusées par Discord pour la soumission %s ; "
                "envoi de la validation avec le lien d’origine.",
                submission.id,
            )
            review_message = await review_channel.send(
                content=(
                    f"{review_content}\n"
                    "⚠️ Les copies des captures étaient trop volumineuses pour Discord. "
                    "Utilise le lien ci-dessus pour consulter les originaux."
                ),
                embed=build_review_embed(submission),
                view=review_view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        try:
            await self.service.mark_pending(submission.id, review_message.id, points)
        except Exception:
            await review_message.delete()
            raise

        try:
            await source_message.add_reaction("📨")
        except discord.DiscordException:
            LOGGER.warning("Impossible de marquer la soumission %s comme envoyée", submission.id)
