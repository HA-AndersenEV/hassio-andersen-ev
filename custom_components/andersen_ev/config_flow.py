"""Config flow for Andersen EV integration."""

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

# Import the konnect module from the local directory
from .konnect.client import KonnectClient
from .konnect.exceptions import AndersenAuthError, AndersenConnectionError, AndersenError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(_hass: HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    # Validate the credentials by attempting to sign in
    try:
        client = KonnectClient(data[CONF_EMAIL], data[CONF_PASSWORD])
        await client.authenticate_user()
        devices = await client.getDevices()

        if not devices:
            raise CannotConnect("No Andersen EV devices found")

        # Return info to be stored in the config entry
        return {"title": f"Andersen EV ({data[CONF_EMAIL]})"}
    except AndersenAuthError as e:
        _LOGGER.error("Authentication error: %s", e)
        raise InvalidAuth from e
    except (AndersenConnectionError, AndersenError) as e:
        _LOGGER.error("Connection error: %s", e)
        raise CannotConnect from e
    except Exception as e:
        _LOGGER.error("Unexpected error during validation: %s", e)
        raise CannotConnect from e


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # pylint: disable=abstract-method
    """Handle a config flow for Andersen EV."""

    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle reauth when credentials become invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        """Handle reauth confirmation step - user re-enters password."""
        errors = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await validate_input(
                    self.hass,
                    {
                        CONF_EMAIL: reauth_entry.data[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": reauth_entry.data[CONF_EMAIL]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Handle reconfiguration - update the password without removing the integration."""
        errors = {}
        reconfigure_entry = self._get_reconfigure_entry()
        if user_input is not None:
            try:
                await validate_input(
                    self.hass,
                    {
                        CONF_EMAIL: reconfigure_entry.data[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": reconfigure_entry.data[CONF_EMAIL]},
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
