"""Слой обновления проекта из GitHub."""

from .inventory import collect_components, diff_components
from .service import UpdateService

__all__ = ["UpdateService", "collect_components", "diff_components"]
