from __future__ import annotations

import logging

from dotenv import load_dotenv

from dofus_pvp_bot.analysis.rapidocr_detector import RapidOcrTeamBalanceDetector
from dofus_pvp_bot.application.leaderboards import LeaderboardService
from dofus_pvp_bot.application.submissions import SubmissionService
from dofus_pvp_bot.config import BotSettings
from dofus_pvp_bot.discord_app.bot import DofusPvpBot
from dofus_pvp_bot.domain.scoring import ScoringEngine, ScoringRules
from dofus_pvp_bot.storage.sqlite import SQLiteSubmissionRepository


def main() -> None:
    load_dotenv()
    settings = BotSettings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    rules = ScoringRules.from_file(settings.scoring_config_path)
    repository = SQLiteSubmissionRepository(settings.database_path)
    service = SubmissionService(repository, ScoringEngine(rules))
    leaderboard_service = LeaderboardService(repository, settings.leaderboard_timezone)
    detector = RapidOcrTeamBalanceDetector(max_image_pixels=settings.max_image_pixels)
    bot = DofusPvpBot(settings, service, leaderboard_service, detector)
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
