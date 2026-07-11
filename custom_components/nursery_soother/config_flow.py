"""Config flow for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import DOMAIN, ENTITY_DOMAINS, NAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(config_key): EntitySelector(
            EntitySelectorConfig(domain=expected_domain)
        )
        for config_key, expected_domain in ENTITY_DOMAINS.items()
    }
)


def _normalize_config_data(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve selector values to canonical entity IDs and validate domains."""
    registry = er.async_get(hass)
    normalized_data: dict[str, str] = {}
    errors: dict[str, str] = {}

    for config_key, expected_domain in ENTITY_DOMAINS.items():
        try:
            entity_id = er.async_validate_entity_id(registry, user_input[config_key])
        except vol.Invalid:
            errors[config_key] = "invalid_entity"
            continue

        if entity_id.partition(".")[0] != expected_domain:
            errors[config_key] = "invalid_entity_domain"
            continue

        normalized_data[config_key] = entity_id

    return normalized_data, errors


class NurserySootherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Nursery Soother config flow."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the entities used by Nursery Soother."""
        if user_input is not None:
            normalized_data, errors = _normalize_config_data(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=NAME, data=normalized_data)

            return self.async_show_form(
                step_id="user",
                data_schema=self.add_suggested_values_to_schema(
                    USER_DATA_SCHEMA, user_input
                ),
                errors=errors,
            )

        return self.async_show_form(step_id="user", data_schema=USER_DATA_SCHEMA)
