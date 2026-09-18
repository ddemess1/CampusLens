"""Настройки сервиса. Все значения можно переопределить переменными окружения."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# Wikimedia требует осмысленный User-Agent с контактом:
# https://meta.wikimedia.org/wiki/User-Agent_policy
USER_AGENT = os.getenv(
    "USER_AGENT",
    "CampusLens/1.0 (LOCUS hackathon project; contact: team@example.com)",
)

# Общий бюджет времени на один профиль (секунды). Требование кейса — 30 с.
TOTAL_DEADLINE_S = _float("TOTAL_DEADLINE_S", 26.0)
HTTP_TIMEOUT_S = _float("HTTP_TIMEOUT_S", 6.0)
HTTP_CONCURRENCY = _int("HTTP_CONCURRENCY", 12)

# Размер превью Commons. 500 входит в стандартный ряд размеров Wikimedia.
THUMB_WIDTH = _int("THUMB_WIDTH", 500)

# Сколько кандидатов скачиваем для перцептивного хеша
MAX_HASH_CANDIDATES = _int("MAX_HASH_CANDIDATES", 90)
# Сколько подкатегорий Commons обходим
MAX_SUBCATEGORIES = _int("MAX_SUBCATEGORIES", 14)
# Сколько фото максимум показываем в одной категории
MAX_PER_CATEGORY = _int("MAX_PER_CATEGORY", 12)
# Радиус геопоиска вокруг кампуса, метры (Commons разрешает до 10000)
CAMPUS_GEO_RADIUS_M = _int("CAMPUS_GEO_RADIUS_M", 800)
CITY_GEO_RADIUS_M = _int("CITY_GEO_RADIUS_M", 1500)

# Порог расстояния dHash (из 64 бит), ниже которого снимки считаются дублями
DHASH_MAX_DISTANCE = _int("DHASH_MAX_DISTANCE", 6)

# Пороги статусов
VERIFIED_THRESHOLD = _float("VERIFIED_THRESHOLD", 0.75)
LIKELY_THRESHOLD = _float("LIKELY_THRESHOLD", 0.50)

# Официальный сайт: показываем og:image как превью со ссылкой
INCLUDE_OFFICIAL_SITE = os.getenv("INCLUDE_OFFICIAL_SITE", "true").lower() == "true"

# Необязательная AI-проверка изображений через Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
VISION_MAX_IMAGES = _int("VISION_MAX_IMAGES", 30)
VISION_TIMEOUT_S = _float("VISION_TIMEOUT_S", 11.0)

# Кэш готовых профилей (секунды)
CACHE_TTL_S = _int("CACHE_TTL_S", 6 * 3600)
