"""Skills nativas de web-agent (documento maestro §8.11). Sin MCP ni herramientas externas."""

from web_agent.skills.auth_bootstrap import DesktopAuthAndBootstrap
from web_agent.skills.base import Skill, SkillRuntime, SkillSpec
from web_agent.skills.guidance_skills import (
    CanvasFormFill,
    LegacyPopupManager,
    ReportExportTracker,
    ZeroShotDiscovery,
)
from web_agent.skills.self_healing_retry import SelfHealingRetry


def default_skills(max_recovery_attempts: int = 6) -> list[Skill]:
    return [
        DesktopAuthAndBootstrap(),
        CanvasFormFill(),
        LegacyPopupManager(),
        ReportExportTracker(),
        ZeroShotDiscovery(),
        SelfHealingRetry(max_attempts=max_recovery_attempts),
    ]


__all__ = [
    "CanvasFormFill",
    "DesktopAuthAndBootstrap",
    "LegacyPopupManager",
    "ReportExportTracker",
    "SelfHealingRetry",
    "Skill",
    "SkillRuntime",
    "SkillSpec",
    "ZeroShotDiscovery",
    "default_skills",
]
