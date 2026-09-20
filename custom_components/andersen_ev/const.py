"""Constants for the Andersen EV integration."""

DOMAIN = "andersen_ev"

# Configuration
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Defaults
DEFAULT_SCAN_INTERVAL = 60  # seconds

# Services
SERVICE_DISABLE_ALL_SCHEDULES = "disable_all_schedules"
SERVICE_GET_DEVICE_INFO = "get_device_info"
SERVICE_GET_DEVICE_STATUS = "get_device_status"
SERVICE_RCM_RESET = "reset_rcm"

# Attributes
ATTR_DEVICE_ID = "device_id"
ATTR_DURATION = "duration"
ATTR_CHARGE_COST_TOTAL = "charge_cost_total"
ATTR_CHARGE_ENERGY_TOTAL = "charge_energy_total"
ATTR_GRID_COST_TOTAL = "grid_cost_total"
ATTR_GRID_ENERGY_TOTAL = "grid_energy_total"
ATTR_SOLAR_ENERGY_TOTAL = "solar_energy_total"
ATTR_SOLAR_COST_TOTAL = "solar_cost_total"
ATTR_SURPLUS_USED_COST_TOTAL = "surplus_used_cost_total"
ATTR_SURPLUS_USED_ENERGY_TOTAL = "surplus_used_energy_total"

# Andersen's API reports an internal product name ("Thurlestone") that is not the name the
# charger is sold under and does not appear anywhere in the Konnect+ app. Map the product
# ids we have confirmed against a real charger onto the marketed model name. The id
# is used as the key rather than the name because it is a stable identifier, while the name
# is free text. Anything not listed here falls back to whatever the API reports.
PRODUCT_NAMES = {
    "30": "A2",
}
