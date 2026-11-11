"""Константи інтеграції ОРЕЕ РДН."""

DOMAIN = "oree_dam"
NAME = "ОРЕЕ: ціни РДН"

CONF_MARKET = "market"
DEFAULT_MARKET = "DAM"
MARKET_NAMES = {"DAM": "РДН (ринок «на добу наперед»)", "IDM": "ВДР (внутрішньодобовий ринок)"}

UNIT = "UAH/kWh"

# Як часто ходити на oree. Ціни змінюються раз на добу, тож це ввічливо
# до джерела й водночас ловить публікацію цін на завтра протягом пів години.
# Значення за замовчуванням; користувач може змінити його в опціях запису.
UPDATE_INTERVAL_MIN = 30
MIN_UPDATE_INTERVAL_MIN = 5
MAX_UPDATE_INTERVAL_MIN = 1440

# Опції запису (кнопка «Налаштувати» на картці інтеграції).
CONF_UPDATE_INTERVAL = "update_interval_min"
# Час доби «HH:MM[:SS]», коли додатково смикнути оновлення поза інтервалом:
# ціни на завтра oree публікує після 14:00, тому дефолт — 14:05.
# Порожній рядок = користувач свідомо вимкнув тригер; відсутній ключ = дефолт.
CONF_DAILY_REFRESH_AT = "daily_refresh_at"
DEFAULT_DAILY_REFRESH_AT = "14:05"

# Якщо о цей час цін на завтра ще немає (oree спізнився) — перепитувати
# кожні CATCH_UP_EVERY_MIN хвилин, але не пізніше CATCH_UP_UNTIL_HOUR.
CATCH_UP_EVERY_MIN = 10
CATCH_UP_UNTIL_HOUR = 18
REQUEST_TIMEOUT_S = 30
USER_AGENT = "HomeAssistant-oree_dam (+price polling)"

STORAGE_VERSION = 1
# Скільки днів назад тримати в кеші (сьогодні мінус N).
KEEP_PAST_DAYS = 1
