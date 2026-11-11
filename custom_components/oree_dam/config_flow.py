"""Налаштування через UI: вибір ринку та опції опитування."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TimeSelector,
)

from .const import (
    CONF_DAILY_REFRESH_AT,
    CONF_MARKET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_MARKET,
    DOMAIN,
    MARKET_NAMES,
    MAX_UPDATE_INTERVAL_MIN,
    MIN_UPDATE_INTERVAL_MIN,
    UPDATE_INTERVAL_MIN,
)


class OreeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Один запис на кожен ринок (РДН / ВДР)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            market = user_input[CONF_MARKET]
            await self.async_set_unique_id(market)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=MARKET_NAMES[market], data={CONF_MARKET: market})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_MARKET, default=DEFAULT_MARKET): vol.In(MARKET_NAMES)}
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OreeOptionsFlow:
        """Кнопка «Налаштувати» на картці інтеграції."""
        return OreeOptionsFlow()


class OreeOptionsFlow(OptionsFlow):
    """Інтервал опитування та час щоденного примусового оновлення.

    self.config_entry не присвоюємо — HA дає його сам.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            options: dict[str, Any] = {
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL])
            }
            # Порожнє поле часу = щоденного тригера немає.
            if user_input.get(CONF_DAILY_REFRESH_AT):
                options[CONF_DAILY_REFRESH_AT] = user_input[CONF_DAILY_REFRESH_AT]
            return self.async_create_entry(title="", data=options)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=int(current.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_MIN)),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_UPDATE_INTERVAL_MIN,
                            max=MAX_UPDATE_INTERVAL_MIN,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                    vol.Optional(
                        CONF_DAILY_REFRESH_AT,
                        description={
                            "suggested_value": current.get(CONF_DAILY_REFRESH_AT)
                        },
                    ): TimeSelector(),
                }
            ),
        )
