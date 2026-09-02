from __future__ import annotations

import discord

from dofus_pvp_bot.application.submissions import SubmissionService
from dofus_pvp_bot.domain.leaderboard import MonthlyLeaderboard
from dofus_pvp_bot.domain.models import Submission, SubmissionStatus


def _check(value: bool) -> str:
    return "✅" if value else "⏳"


def submission_reference(submission: Submission) -> str:
    identifier = submission.id.upper()
    return f"TOP-{identifier[:4]}-{identifier[4:8]}-{identifier[8:14]}"


def _detection_text(submission: Submission) -> str:
    if submission.fight_balance is None:
        return "Effectifs à déterminer par le staff."
    text = submission.fight_balance.label
    if (
        submission.allies_count is not None
        and submission.opponents_count is not None
    ):
        text += (
            f" · **{submission.allies_count}v{submission.opponents_count}** "
            "(hors percepteur/prisme)"
        )
    if submission.detection_method is not None:
        text += f" · {submission.detection_method.label}"
    if submission.detection_confidence is not None:
        text += f" ({submission.detection_confidence:.0%})"
    return text


def build_draft_embed(
    submission: Submission,
    service: SubmissionService,
) -> discord.Embed:
    ready = bool(submission.participant_ids)
    colour = discord.Colour.green() if ready else discord.Colour.blue()
    embed = discord.Embed(
        title="Attribution des points",
        description=(
            "Sélectionne uniquement les joueurs qui doivent recevoir les points. "
            "Le staff vérifiera l’analyse des effectifs avant validation."
        ),
        colour=colour,
    )
    embed.add_field(
        name="Analyse automatique",
        value=(
            f"{_check(submission.fight_balance is not None)} {_detection_text(submission)}\n"
            "Cette information est indicative et sera contrôlée par le staff."
        ),
        inline=False,
    )

    members = " ".join(f"<@{user_id}>" for user_id in submission.participant_ids)
    member_text = f"{len(submission.participant_ids)}/4 sélectionné(s)"
    if members:
        member_text += f" · {members}"
    embed.add_field(
        name="Joueurs bénéficiaires",
        value=f"{_check(bool(submission.participant_ids))} {member_text}",
        inline=False,
    )

    multiplier = service.scoring.rules.lane_multipliers[submission.lane]
    embed.add_field(
        name="Salon",
        value=f"{submission.lane.label} · multiplicateur ×{multiplier}",
        inline=False,
    )

    estimate = "⏳ En attente"
    if submission.fight_balance is not None:
        points = service.scoring.calculate(submission.fight_balance, submission.lane)
        estimate = f"✅ **{points.total_points} point(s) par joueur**"
    embed.add_field(name="Estimation", value=estimate, inline=False)
    embed.add_field(
        name="Statut",
        value=(
            "✅ Prête à être envoyée au staff"
            if ready
            else "⏳ Sélectionne au moins un bénéficiaire"
        ),
        inline=False,
    )
    embed.add_field(name="Référence", value=submission_reference(submission), inline=False)
    embed.add_field(
        name="Note",
        value=submission.note or "Aucune note ajoutée",
        inline=False,
    )
    embed.set_footer(text=f"Barème {service.scoring.rules.version}")
    return embed


def build_review_embed(submission: Submission, *, final: bool = False) -> discord.Embed:
    colour = discord.Colour.orange()
    title_status = "En attente de validation"
    if final and submission.status is SubmissionStatus.APPROVED:
        colour = discord.Colour.green()
        title_status = "Validée"
    elif final and submission.status is SubmissionStatus.REJECTED:
        colour = discord.Colour.red()
        title_status = "Refusée"

    embed = discord.Embed(
        title=f"{submission.lane.label} · {title_status}",
        colour=colour,
    )
    embed.add_field(
        name="Effectifs",
        value=_detection_text(submission),
        inline=False,
    )
    if (
        submission.detection_method is not None
        and submission.detection_method.value == "manual"
        and submission.detected_allies_count is not None
        and submission.detected_opponents_count is not None
    ):
        embed.add_field(
            name="Résultat OCR initial",
            value=(
                f"{submission.detected_allies_count}v"
                f"{submission.detected_opponents_count} (conservé pour contrôle)"
            ),
            inline=False,
        )
    embed.add_field(
        name="Participants",
        value=" ".join(f"<@{user_id}>" for user_id in submission.participant_ids),
        inline=False,
    )
    if submission.points is None:
        embed.add_field(
            name="Calcul",
            value="⏳ En attente : le staff doit renseigner les effectifs.",
            inline=False,
        )
        embed.add_field(
            name="Points attribués par participant",
            value="⏳ En attente",
            inline=False,
        )
    else:
        embed.add_field(
            name="Calcul",
            value="\n".join(f"• {line}" for line in submission.points.explanation),
            inline=False,
        )
        embed.add_field(
            name="Points attribués par participant",
            value=f"**{submission.points.total_points}**",
            inline=False,
        )
    if submission.detection_detail:
        embed.add_field(
            name="Détail de la détection",
            value=submission.detection_detail,
            inline=False,
        )
    if submission.note:
        embed.add_field(name="Note", value=submission.note, inline=False)
    if submission.rejection_reason:
        embed.add_field(name="Motif du refus", value=submission.rejection_reason, inline=False)
    embed.add_field(name="Référence", value=submission_reference(submission), inline=False)
    if submission.points is not None:
        embed.set_footer(text=f"Barème {submission.points.rule_version}")
    return embed


def build_leaderboard_embed(leaderboard: MonthlyLeaderboard) -> discord.Embed:
    if leaderboard.finalized:
        colour = discord.Colour.gold()
        status = "🔒 Classement définitif"
    elif leaderboard.pending_submission_count:
        colour = discord.Colour.orange()
        status = (
            "⏳ Clôture en attente de "
            f"{leaderboard.pending_submission_count} soumission(s) du mois"
        )
    else:
        colour = discord.Colour.blue()
        status = "📊 Classement provisoire"

    lines: list[str] = []
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for entry in leaderboard.entries:
        prefix = medals.get(entry.rank, f"**{entry.rank}.**")
        combat_label = "combat" if entry.submission_count == 1 else "combats"
        lines.append(
            f"{prefix} <@{entry.user_id}> — **{entry.total_points} pts** "
            f"· {entry.submission_count} {combat_label}"
        )

    embed = discord.Embed(
        title=f"Classement PvP · {leaderboard.period.label}",
        description="\n".join(lines) if lines else "Aucun point validé pour cette période.",
        colour=colour,
    )
    embed.add_field(name="Statut", value=status, inline=False)
    embed.set_footer(
        text=(
            f"{leaderboard.approved_submission_count} soumission(s) validée(s) · "
            "les ex æquo partagent leur rang"
        )
    )
    return embed
