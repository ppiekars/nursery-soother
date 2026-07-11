"""Config flow for the Nursery Soother foundation."""

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, NAME


class NurserySootherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Nursery Soother foundation config flow."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create an inert entry used to verify installation."""
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})

        return self.async_show_form(step_id="user")
