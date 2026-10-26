"""Реестр инструментов: паспорт ассистента перечисляет их имена в tools."""
from __future__ import annotations

import logging

from app.config import Passport
from app.tools.base import Tool, ToolContext
from app.tools.handoff import TOOL as HANDOFF

__all__ = ["REGISTRY", "Tool", "ToolContext", "tools_for"]

log = logging.getLogger(__name__)

REGISTRY: dict[str, Tool] = {HANDOFF.name: HANDOFF}


def tools_for(passport: Passport) -> list[Tool]:
    """Инструменты ассистента. Еще не реализованные (интервью — позже) пропускаются."""
    tools = []
    for name in passport.tools:
        if name in REGISTRY:
            tools.append(REGISTRY[name])
        else:
            log.debug("Инструмент %s для %s еще не реализован — пропущен", name, passport.id)
    return tools
