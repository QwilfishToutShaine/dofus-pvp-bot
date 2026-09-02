from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord

from dofus_pvp_bot.application.submissions import SubmissionError, SubmissionService
from dofus_pvp_bot.config import BotSettings
from dofus_pvp_bot.discord_app.presentation import build_draft_embed, build_review_embed
from dofus_pvp_bot.domain.models import Submission, SubmissionStatus

if TYPE_CHECKING:
    from dofus_pvp_bot.discord_app.bot import DofusPvpBot


async def _ephemeral_error(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class StartSubmissionView(discord.ui.View):
    def __init__(
        self,
        service: SubmissionService,
        settings: BotSettings,
        submission_id: str,
    ) -> None:
        super().__init__(timeout=None)
        self.service = service
        self.settings = settings
        self.submission_id = submission_id
        button: discord.ui.Button[StartSubmissionView] = discord.ui.Button(
            label="Attribuer les points",
            emoji="🏆",
            style=discord.ButtonStyle.primary,
            custom_id=f"submission:start:{submission_id}",
        )
        button.callback = self.start  # type: ignore[method-assign]
        self.add_item(button)

    async def start(self, interaction: discord.Interaction) -> None:
        submission = await self.service.require(self.submission_id)
        if interaction.user.id != submission.submitter_id:
            await _ephemeral_error(
                interaction, "Seul l’auteur de la capture peut compléter cette soumission."
            )
            return
        if submission.status is not SubmissionStatus.DRAFT:
            await _ephemeral_error(interaction, "Cette soumission est déjà clôturée.")
            return

        wizard = WizardView(
            service=self.service,
            settings=self.settings,
            submission_id=self.submission_id,
            owner_id=submission.submitter_id,
        )
        await wizard.rebuild()
        await interaction.response.send_message(
            embed=build_draft_embed(submission, self.service),
            view=wizard,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        wizard.message = await interaction.original_response()


class WizardView(discord.ui.View):
    def __init__(
        self,
        *,
        service: SubmissionService,
        settings: BotSettings,
        submission_id: str,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=float(settings.draft_timeout_seconds))
        self.service = service
        self.settings = settings
        self.submission_id = submission_id
        self.owner_id = owner_id
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await _ephemeral_error(interaction, "Ce formulaire appartient à un autre membre.")
        return False

    async def rebuild(self) -> None:
        submission = await self.service.require(self.submission_id)
        self.clear_items()
        self.add_item(
            ParticipantsUserSelect(
                self,
                selected_count=len(submission.participant_ids),
            )
        )
        self.add_item(NoteButton(self))
        self.add_item(
            SubmitButton(
                self,
                disabled=not submission.participant_ids,
            )
        )
        self.add_item(CancelButton(self))

    async def refresh_message(self) -> None:
        await self.rebuild()
        submission = await self.service.require(self.submission_id)
        if self.message is not None:
            await self.message.edit(
                embed=build_draft_embed(submission, self.service),
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )


class ParticipantsUserSelect(discord.ui.UserSelect[WizardView]):
    def __init__(self, wizard: WizardView, *, selected_count: int) -> None:
        self.wizard = wizard
        super().__init__(
            placeholder=f"Joueurs bénéficiaires : {selected_count}/4 sélectionné(s)",
            min_values=1,
            max_values=4,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        invalid_names: list[str] = []
        selected_ids: list[int] = []
        for user in self.values:
            if user.bot:
                invalid_names.append(user.display_name)
                continue
            if self.wizard.settings.member_role_id is not None and (
                not isinstance(user, discord.Member)
                or all(role.id != self.wizard.settings.member_role_id for role in user.roles)
            ):
                invalid_names.append(user.display_name)
                continue
            selected_ids.append(user.id)
        if invalid_names:
            await _ephemeral_error(
                interaction,
                "Sélection refusée pour : " + ", ".join(invalid_names),
            )
            return
        try:
            await self.wizard.service.set_participants(
                self.wizard.submission_id, selected_ids
            )
        except SubmissionError as exc:
            await _ephemeral_error(interaction, str(exc))
            return
        await self.wizard.rebuild()
        submission = await self.wizard.service.require(self.wizard.submission_id)
        await interaction.response.edit_message(
            embed=build_draft_embed(submission, self.wizard.service),
            view=self.wizard,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class NoteButton(discord.ui.Button[WizardView]):
    def __init__(self, wizard: WizardView) -> None:
        self.wizard = wizard
        super().__init__(label="Note", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        submission = await self.wizard.service.require(self.wizard.submission_id)
        await interaction.response.send_modal(NoteModal(self.wizard, submission.note))


class NoteModal(discord.ui.Modal):
    def __init__(self, wizard: WizardView, current: str | None) -> None:
        super().__init__(title="Note de soumission", timeout=300)
        self.wizard = wizard
        self.note: discord.ui.TextInput[NoteModal] = discord.ui.TextInput(
            label="Note facultative pour le staff",
            placeholder="Ajoute un contexte utile à cette soumission",
            default=current,
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.wizard.service.set_note(self.wizard.submission_id, self.note.value)
        await interaction.response.send_message("✅ Note mise à jour.", ephemeral=True)
        await self.wizard.refresh_message()


class SubmitButton(discord.ui.Button[WizardView]):
    def __init__(self, wizard: WizardView, *, disabled: bool) -> None:
        self.wizard = wizard
        super().__init__(
            label="Envoyer en vérification",
            emoji="✅",
            style=discord.ButtonStyle.success,
            disabled=disabled,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot = cast("DofusPvpBot", interaction.client)
        try:
            submission = await self.wizard.service.require(self.wizard.submission_id)
            await bot.publish_for_review(submission)
        except (SubmissionError, discord.DiscordException) as exc:
            await interaction.followup.send(f"❌ Envoi impossible : {exc}", ephemeral=True)
            return
        final_submission = await self.wizard.service.require(self.wizard.submission_id)
        for child in self.wizard.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.wizard.message is not None:
            await self.wizard.message.edit(
                embed=build_draft_embed(final_submission, self.wizard.service),
                view=self.wizard,
            )
        await interaction.followup.send("✅ Soumission envoyée au staff.", ephemeral=True)


class CancelButton(discord.ui.Button[WizardView]):
    def __init__(self, wizard: WizardView) -> None:
        self.wizard = wizard
        super().__init__(label="Annuler", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.wizard.service.cancel(self.wizard.submission_id)
        await interaction.response.edit_message(
            content="Soumission annulée.", embed=None, view=None
        )


class ReviewView(discord.ui.View):
    def __init__(
        self,
        service: SubmissionService,
        settings: BotSettings,
        submission_id: str,
        *,
        can_approve: bool,
    ) -> None:
        super().__init__(timeout=None)
        self.service = service
        self.settings = settings
        self.submission_id = submission_id

        self.approve_button: discord.ui.Button[ReviewView] = discord.ui.Button(
            label="Valider",
            emoji="✅",
            style=discord.ButtonStyle.success,
            disabled=not can_approve,
            custom_id=f"submission:approve:{submission_id}",
        )
        self.approve_button.callback = self.approve  # type: ignore[method-assign]
        self.add_item(self.approve_button)

        correct: discord.ui.Button[ReviewView] = discord.ui.Button(
            label="Corriger les effectifs",
            emoji="🛠️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"submission:correct-counts:{submission_id}",
        )
        correct.callback = self.correct_counts  # type: ignore[method-assign]
        self.add_item(correct)

        reject: discord.ui.Button[ReviewView] = discord.ui.Button(
            label="Refuser",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"submission:reject:{submission_id}",
        )
        reject.callback = self.reject  # type: ignore[method-assign]
        self.add_item(reject)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            await _ephemeral_error(interaction, "Action réservée au staff du serveur.")
            return False
        is_admin = interaction.user.guild_permissions.administrator
        has_role = bool(
            self.settings.reviewer_role_ids
            and any(role.id in self.settings.reviewer_role_ids for role in interaction.user.roles)
        )
        if is_admin or has_role:
            return True
        await _ephemeral_error(interaction, "Tu n’as pas la permission de valider.")
        return False

    async def approve(self, interaction: discord.Interaction) -> None:
        try:
            submission = await self.service.approve(self.submission_id)
        except SubmissionError as exc:
            await _ephemeral_error(interaction, str(exc))
            return
        embed = build_review_embed(submission, final=True)
        if interaction.message is not None and interaction.message.attachments:
            embed.set_image(url=interaction.message.attachments[0].url)
        await interaction.response.edit_message(embed=embed, view=None)
        await _mark_source_message(interaction.client, submission, "✅")

    async def correct_counts(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            await _ephemeral_error(interaction, "Message de validation introuvable.")
            return
        submission = await self.service.require(self.submission_id)
        if submission.status is not SubmissionStatus.PENDING_REVIEW:
            await _ephemeral_error(interaction, "Cette soumission n’est plus modifiable.")
            return
        await interaction.response.send_modal(
            StaffCountsModal(self, interaction.message, submission)
        )

    async def reject(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            await _ephemeral_error(interaction, "Message de validation introuvable.")
            return
        await interaction.response.send_modal(
            RejectModal(self.service, self.submission_id, interaction.message)
        )


class StaffCountsModal(discord.ui.Modal):
    def __init__(
        self,
        review_view: ReviewView,
        review_message: discord.Message,
        submission: Submission,
    ) -> None:
        super().__init__(title="Corriger les effectifs", timeout=300)
        self.review_view = review_view
        self.review_message = review_message
        self.allies: discord.ui.TextInput[StaffCountsModal] = discord.ui.TextInput(
            label="Alliés (hors percepteur/prisme)",
            placeholder="Entre 1 et 4",
            default=str(submission.allies_count) if submission.allies_count is not None else None,
            required=True,
            min_length=1,
            max_length=1,
        )
        self.opponents: discord.ui.TextInput[StaffCountsModal] = discord.ui.TextInput(
            label="Adversaires (hors percepteur/prisme)",
            placeholder="Entre 0 et 4",
            default=(
                str(submission.opponents_count)
                if submission.opponents_count is not None
                else None
            ),
            required=True,
            min_length=1,
            max_length=1,
        )
        self.add_item(self.allies)
        self.add_item(self.opponents)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            allies_count = int(self.allies.value)
            opponents_count = int(self.opponents.value)
        except ValueError:
            await _ephemeral_error(interaction, "Les deux effectifs doivent être des nombres.")
            return
        try:
            submission = await self.review_view.service.set_review_counts(
                self.review_view.submission_id,
                allies_count,
                opponents_count,
            )
        except SubmissionError as exc:
            await _ephemeral_error(interaction, str(exc))
            return
        self.review_view.approve_button.disabled = False
        await interaction.response.send_message(
            f"✅ Effectifs retenus : {allies_count}v{opponents_count}. Points recalculés.",
            ephemeral=True,
        )
        embed = build_review_embed(submission)
        if self.review_message.attachments:
            embed.set_image(url=self.review_message.attachments[0].url)
        await self.review_message.edit(embed=embed, view=self.review_view)


class RejectModal(discord.ui.Modal):
    def __init__(
        self,
        service: SubmissionService,
        submission_id: str,
        review_message: discord.Message,
    ) -> None:
        super().__init__(title="Refuser la soumission", timeout=300)
        self.service = service
        self.submission_id = submission_id
        self.review_message = review_message
        self.reason: discord.ui.TextInput[RejectModal] = discord.ui.TextInput(
            label="Motif du refus",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            submission = await self.service.reject(self.submission_id, self.reason.value)
        except SubmissionError as exc:
            await _ephemeral_error(interaction, str(exc))
            return
        await interaction.response.send_message("Soumission refusée.", ephemeral=True)
        embed = build_review_embed(submission, final=True)
        if self.review_message.attachments:
            embed.set_image(url=self.review_message.attachments[0].url)
        await self.review_message.edit(embed=embed, view=None)
        await _mark_source_message(interaction.client, submission, "❌")


async def _mark_source_message(
    client: discord.Client,
    submission: Submission,
    emoji: str,
) -> None:
    channel = client.get_channel(submission.source_channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    try:
        message = await channel.fetch_message(submission.source_message_id)
        await message.add_reaction(emoji)
    except discord.DiscordException:
        return
