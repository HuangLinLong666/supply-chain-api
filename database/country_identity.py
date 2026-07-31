from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, Iterable

from babel import Locale


COUNTRY_NAMING_VERSION = "iso3166-1-alpha2-cldr-v1"

ISO_ALPHA2_CODES = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT
    JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA
    RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ
    VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split()
)

COMMON_ALIASES = {
    "america": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "usa": "US",
    "united states of america": "US",
    "uk": "GB",
    "u.k.": "GB",
    "britain": "GB",
    "great britain": "GB",
    "uae": "AE",
    "south korea": "KR",
    "republic of korea": "KR",
    "north korea": "KP",
    "democratic people's republic of korea": "KP",
    "russia": "RU",
    "russian federation": "RU",
    "turkiye": "TR",
    "türkiye": "TR",
    "turkey": "TR",
    "czech republic": "CZ",
    "ivory coast": "CI",
    "vietnam": "VN",
    "viet nam": "VN",
    "laos": "LA",
    "brunei": "BN",
    "bolivia": "BO",
    "tanzania": "TZ",
    "venezuela": "VE",
    "iran": "IR",
    "syria": "SY",
    "moldova": "MD",
    "drc": "CD",
    "democratic republic of the congo": "CD",
    "congo kinshasa": "CD",
    "republic of the congo": "CG",
    "congo brazzaville": "CG",
}


def normalize_country_alias(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    ascii_value = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", ascii_value).strip()


@lru_cache(maxsize=1)
def _locales() -> tuple[Locale, Locale]:
    return Locale.parse("en"), Locale.parse("zh_Hans")


def canonical_country_names(code: str) -> tuple[str, str]:
    normalized_code = str(code or "").strip().upper()
    if normalized_code not in ISO_ALPHA2_CODES:
        raise ValueError(f"无效的 ISO 3166-1 alpha-2 国家/地区代码: {code!r}")
    english, chinese = _locales()
    return str(english.territories[normalized_code]), str(chinese.territories[normalized_code])


@lru_cache(maxsize=1)
def country_alias_index() -> dict[str, str]:
    english, chinese = _locales()
    index: dict[str, str] = {}
    for code in sorted(ISO_ALPHA2_CODES):
        for value in (code, english.territories.get(code), chinese.territories.get(code)):
            normalized = normalize_country_alias(value)
            if normalized:
                index[normalized] = code
    for alias, code in COMMON_ALIASES.items():
        index[normalize_country_alias(alias)] = code
    return index


def resolve_country_code(*values: Any) -> str | None:
    aliases = country_alias_index()
    for value in values:
        raw_value = str(value or "").strip()
        direct_code = raw_value.upper()
        if direct_code in ISO_ALPHA2_CODES:
            return direct_code
        matched_code = aliases.get(normalize_country_alias(raw_value))
        if matched_code:
            return matched_code
    return None


def aliases_for_country(code: str, values: Iterable[Any] = ()) -> list[str]:
    english_name, chinese_name = canonical_country_names(code)
    aliases: list[str] = []
    for value in (code, english_name, chinese_name, *values):
        text = str(value or "").strip()
        if text and text not in aliases:
            aliases.append(text)
    for alias, alias_code in COMMON_ALIASES.items():
        if alias_code == code and alias not in aliases:
            aliases.append(alias)
    return aliases


def canonical_country_fields(properties: dict[str, Any]) -> dict[str, Any] | None:
    code = resolve_country_code(
        properties.get("country_code"),
        properties.get("iso2"),
        properties.get("country"),
        properties.get("country_name_en"),
        properties.get("country_name_zh"),
    )
    if code is None:
        return None
    english_name, chinese_name = canonical_country_names(code)
    existing_aliases = properties.get("country_aliases") or []
    if isinstance(existing_aliases, str):
        existing_aliases = [existing_aliases]
    return {
        "country_code": code,
        "country": english_name,
        "country_name_en": english_name,
        "country_name_zh": chinese_name,
        "country_aliases": aliases_for_country(
            code,
            (
                properties.get("country"),
                properties.get("country_name_en"),
                properties.get("country_name_zh"),
                *existing_aliases,
            ),
        ),
        "country_naming_version": COUNTRY_NAMING_VERSION,
    }
