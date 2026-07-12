"""Frontend registration for the Nursery Soother card."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_FILENAME = "nursery-soother-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"
CARD_PATH = Path(__file__).parent / "frontend" / CARD_FILENAME
CARD_MODULE_VERSION = "1"
CARD_MODULE_URL = f"{CARD_URL}?v={CARD_MODULE_VERSION}"
FRONTEND_DOMAIN = frontend.DOMAIN

_DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve and load the Nursery Soother card once per Home Assistant run."""
    if hass.data.get(_DATA_FRONTEND_REGISTERED):
        return

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL,
                str(CARD_PATH),
                cache_headers=False,
            )
        ]
    )
    frontend.add_extra_js_url(hass, CARD_MODULE_URL)
    hass.data[_DATA_FRONTEND_REGISTERED] = True
