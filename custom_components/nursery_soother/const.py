"""Constants for Nursery Soother."""

from typing import Final

from homeassistant.const import Platform

CONF_CAMERA = "camera"
CONF_CRY_SENSOR = "cry_sensor"
CONF_MEDIA_PLAYER = "media_player"
DOMAIN = "nursery_soother"
ENTITY_DOMAINS: Final = {
    CONF_CRY_SENSOR: Platform.BINARY_SENSOR,
    CONF_CAMERA: Platform.CAMERA,
    CONF_MEDIA_PLAYER: Platform.MEDIA_PLAYER,
}
NAME = "Nursery Soother"
