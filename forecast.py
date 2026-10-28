#!/usr/bin/env python3
"""Previsão de pesca a partir do HTML público do SurfGuru.

O site embute, em cada praia, a atmosfera (GFS), a onda (WaveWatch III)
e a tábua (Marinha do Brasil / xTide). Este módulo só lê esse pacote e
calcula o rank. Não inventa estação, maré nem fase da lua.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
SURF = "https://surfguru.com.br"
UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
KN_TO_KMH = 1.852
# Sizígia típica, em metros. O SurfGuru publica alturas, não o coeficiente.
# Espírito Santo permanece em 1,6 m, a referência original do app.
SPRING_RANGE_M = 1.6
SPRING_BY_STATE = {
    "alagoas": 2.0,
    "amapa": 4.0,
    "bahia": 2.2,
    "ceara": 2.6,
    "espirito-santo": 1.6,
    "fernando-de-noronha": 2.4,
    "maranhao": 6.0,
    "para": 3.6,
    "paraiba": 2.2,
    "parana": 1.8,
    "pernambuco": 2.3,
    "piaui": 3.0,
    "rio-de-janeiro": 1.3,
    "rio-grande-do-norte": 2.6,
    "rio-grande-do-sul": 0.5,
    "santa-catarina": 1.0,
    "sao-paulo": 1.3,
    "sergipe": 2.0,
}
COEF_AT_SPRING = 95

WEIGHTS = (
    ("mare", "Maré", 22),
    ("vento", "Vento", 18),
    ("clima", "Clima", 14),
    ("lua", "Lua", 12),
    ("chuva", "Chuva", 10),
    ("coef", "Coeficiente", 10),
    ("pressao", "Pressão", 8),
    ("onda", "Onda", 6),
)

DIRS = (
    "norte",
    "norte-nordeste",
    "nordeste",
    "lés-nordeste",
    "leste",
    "lés-sudeste",
    "sudeste",
    "sul-sudeste",
    "sul",
    "sul-sudoeste",
    "sudoeste",
    "oeste-sudoeste",
    "oeste",
    "oeste-noroeste",
    "noroeste",
    "norte-noroeste",
)

WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
WEEKDAYS_SHORT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
MONTHS = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


class ForecastError(Exception):
    pass


def fetch_text(url: str, timeout: float = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise ForecastError(f"SurfGuru respondeu {exc.code} em {url}") from exc
    except urllib.error.URLError as exc:
        raise ForecastError(f"Não cheguei ao SurfGuru: {exc.reason}") from exc
    return raw.decode(charset, "replace")


def extract_js_object(html: str, var_name: str) -> dict:
    key = f"var {var_name}"
    i = html.find(key)
    if i < 0:
        raise ForecastError(f"A página não trouxe {var_name}.")
    i = html.find("{", i)
    if i < 0:
        raise ForecastError(f"A página não trouxe o objeto {var_name}.")
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i : j + 1])
                except json.JSONDecodeError as exc:
                    raise ForecastError(f"Não consegui ler {var_name}.") from exc
    raise ForecastError(f"Objeto {var_name} incompleto.")


def _num(html: str, name: str, default: float | None = None) -> float | None:
    m = re.search(rf"var {name}\s*=\s*(-?\d+(?:\.\d+)?)", html)
    if not m:
        return default
    return float(m.group(1))


def _str(html: str, name: str, default: str = "") -> str:
    m = re.search(rf"var {name}\s*=\s*['\"]([^'\"]*)['\"]", html)
    return m.group(1) if m else default


def _year(value) -> int:
    y = int(value)
    return y + 2000 if y < 100 else y


def _series(raw: str) -> list[list[float | None]]:
    parts = raw.split(";") if raw else []

    def nums(idx: int) -> list[float | None]:
        if idx >= len(parts) or parts[idx] == "":
            return [None] * 8
        out: list[float | None] = []
        for token in parts[idx].split(":")[:8]:
            try:
                out.append(float(token))
            except ValueError:
                out.append(None)
        while len(out) < 8:
            out.append(None)
        return out

    return [nums(i) for i in range(22)]


def _is_tides(s: str) -> bool:
    return s in ("", "0") or (len(s) >= 6 and len(s) % 6 == 0 and s.isdigit())


def _is_astros(s: str) -> bool:
    return s == "" or (len(s) >= 5 and len(s) % 5 == 0 and s.isdigit())


def _is_age(s: str) -> bool:
    return s.isdigit() and 0 <= int(s) <= 40


def _tail(raw: str) -> tuple[str, str, str]:
    parts = raw.split(";") if raw else []
    if (
        len(parts) >= 3
        and _is_age(parts[-1])
        and _is_astros(parts[-2])
        and _is_tides(parts[-3])
    ):
        return parts[-3], parts[-2], parts[-1]
    if len(parts) > 25 and _is_tides(parts[23]):
        return parts[23], parts[24] if len(parts) > 24 else "", parts[25]
    return "", "", ""


def _parse_tides(raw: str) -> list[tuple[int, int, float]]:
    if not raw or raw == "0":
        return []
    out = []
    for i in range(0, len(raw) - 5, 6):
        chunk = raw[i : i + 6]
        if not chunk.isdigit():
            continue
        hh, mm, dm = int(chunk[0:2]), int(chunk[2:4]), int(chunk[4:6])
        if hh > 23 or mm > 59:
            continue
        out.append((hh, mm, dm / 10))
    return out


def _parse_astros(raw: str) -> list[tuple[int, int, int]]:
    if not raw:
        return []
    out = []
    for i in range(0, len(raw) - 4, 5):
        chunk = raw[i : i + 5]
        if not chunk.isdigit():
            continue
        hh, mm, kind = int(chunk[0:2]), int(chunk[2:4]), int(chunk[4])
        if hh > 23 or mm > 59 or kind > 3:
            continue
        out.append((kind, hh, mm))
    return out


def _days_from(block: dict, prefix: str) -> list[tuple[date, str]]:
    dados = block.get("dados") or {}
    try:
        start = date(_year(dados["ano"]), int(dados["mes"]), int(dados["dia"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastError("Data inicial da previsão ausente.") from exc
    found = []
    for key, value in dados.items():
        if not key.startswith(prefix) or not key[len(prefix) :].isdigit():
            continue
        if not isinstance(value, str):
            continue
        idx = int(key[len(prefix) :])
        found.append((idx, value))
    found.sort()
    return [(start + timedelta(days=idx), value) for idx, value in found]


def title_place(html: str) -> tuple[str, str, str]:
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if not m:
        return "Praia", "", ""
    bits = [b.strip() for b in m.group(1).split(" - ") if b.strip()]
    praia = bits[0] if bits else "Praia"
    praia = re.sub(r"^praia\s+", "", praia, flags=re.I).strip() or "Praia"
    cidade = bits[1] if len(bits) > 1 else ""
    estado = bits[2] if len(bits) > 2 else ""
    if estado.lower().startswith("brasil"):
        estado = ""
    return praia, cidade, estado


def parse_page(html: str) -> dict:
    ondas = extract_js_object(html, "ondas")
    meteoro = extract_js_object(html, "meteoro")
    ocean_rows = []
    for day, raw in _days_from(ondas, "v"):
        series = _series(raw)
        tides_raw, astros_raw, age_raw = _tail(raw)
        ocean_rows.append(
            {
                "date": day,
                "height": series[0],
                "period": series[1],
                "tides": _parse_tides(tides_raw),
                "astros": _parse_astros(astros_raw),
                "moonAge": int(age_raw) if _is_age(age_raw) else None,
            }
        )
    surf_by_date = {}
    dados = ondas.get("dados") or {}
    start = ocean_rows[0]["date"] if ocean_rows else None
    if start is not None:
        for key, value in dados.items():
            if key.startswith("s") and key[1:].isdigit() and isinstance(value, str):
                idx = int(key[1:])
                parts = value.split(";")
                heights: list[float | None] = []
                tokens = parts[0].split(":") if parts and parts[0] else []
                for token in tokens[:8]:
                    try:
                        heights.append(float(token))
                    except ValueError:
                        heights.append(None)
                while len(heights) < 8:
                    heights.append(None)
                surf_by_date[start + timedelta(days=idx)] = heights

    meteo_rows = []
    for day, raw in _days_from(meteoro, "v"):
        cols = _series(raw)
        meteo_rows.append(
            {
                "date": day,
                "windKt": cols[0],
                "windDir": cols[1],
                "gustKt": cols[2],
                "pressure": [None if v is None else v + 850 for v in cols[4]],
                "temp": cols[5],
                "clouds": cols[6],
                "rain": cols[7],
            }
        )
    try:
        tsm = float((ondas.get("dados") or {}).get("tsm"))
    except (TypeError, ValueError):
        tsm = None
    if tsm is not None and (tsm <= 0 or tsm >= 45):
        tsm = None
    praia, cidade, estado = title_place(html)
    fuso = _num(html, "fuso_horario", -3)
    return {
        "praia": praia,
        "cidade": cidade,
        "estado": estado,
        "fuso": -3 if fuso is None else int(fuso),
        "ocean": ocean_rows,
        "surf": surf_by_date,
        "meteo": meteo_rows,
        "tsm": tsm,
        "lat": _num(html, "praia_lat"),
        "lon": _num(html, "praia_lon"),
        "orientacao": _num(html, "orientacao", 90) or 90,
        "anguloTerral": _num(html, "AnguloTerral", 125) or 125,
        "estacao": _str(html, "estacaoMare", ""),
        "estacaoId": _num(html, "idEstacaoMare"),
    }


def _comma(value: float, digits: int = 0) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _hm(dt: datetime) -> str:
    return f"{dt.hour}h{dt.minute:02d}"


def _hh(hour: int) -> str:
    return f"{hour}h"


def direction_name(deg: float | None) -> str:
    if deg is None:
        return "direção indefinida"
    return DIRS[int(((deg % 360) + 11.25) / 22.5) % 16]


def wind_type(orientacao: float, wind_from: float, angulo_terral: float) -> str:
    diff = abs(orientacao - wind_from) % 360
    if diff > 180:
        diff = 360 - diff
    if diff > angulo_terral:
        return "terral"
    if diff > 65:
        return "cruzado"
    return "maral"


def tide_coefficient(range_m: float, spring_m: float = SPRING_RANGE_M) -> int:
    spring = spring_m if spring_m and spring_m > 0 else SPRING_RANGE_M
    raw = range_m / spring * COEF_AT_SPRING
    return int(round(max(18, min(120, raw))))


def clock_label(fuso: int) -> str:
    if fuso == -3:
        return "horário de Brasília"
    if fuso == -2:
        return "horário de Fernando de Noronha"
    if fuso == -4:
        return "horário do Amazonas"
    if fuso == -5:
        return "horário do Acre"
    return f"horário local (UTC{fuso:+d})"


def coef_words(coef: int) -> str:
    if coef < 40:
        return "maré morta"
    if coef < 55:
        return "coeficiente médio"
    if coef < 75:
        return "maré boa"
    if coef < 90:
        return "maré grande"
    return "sizígia"


def moon_name(age: int | None) -> str:
    if not age:
        return "sem leitura de lua"
    if age == 1 or age >= 29:
        return "lua nova"
    if age < 8:
        return "crescente"
    if age == 8:
        return "quarto crescente"
    if age < 16:
        return "gibosa crescente"
    if age == 16:
        return "lua cheia"
    if age < 24:
        return "gibosa minguante"
    if age == 24:
        return "quarto minguante"
    return "minguante"


def _phase_factor(age: int | None) -> float:
    if not age:
        return 0.5

    def dist(a: int, b: int) -> int:
        d = abs(a - b) % 29
        return min(d, 29 - d)

    d = min(dist(age, 1), dist(age, 16))
    if d <= 1:
        return 1.0
    if d <= 3:
        return 0.85
    if d <= 5:
        return 0.7
    if d <= 8:
        return 0.48
    return 0.4


def _clima(moment: datetime, sunrise: datetime | None, sunset: datetime | None, temp: float | None) -> float:
    if sunrise is None or sunset is None:
        sunrise = moment.replace(hour=5, minute=15, second=0, microsecond=0)
        sunset = moment.replace(hour=18, minute=5, second=0, microsecond=0)

    def minutes(target: datetime) -> float:
        return abs((moment - target).total_seconds()) / 60

    near = min(minutes(sunrise), minutes(sunset))
    if near <= 60:
        base = 1.0
    elif near <= 120:
        base = 0.84
    elif sunrise <= moment <= sunset:
        mid = sunrise + (sunset - sunrise) / 2
        base = 0.42 if minutes(mid) < 100 else 0.6
    else:
        base = 0.34
    if temp is None:
        return base
    if temp >= 34:
        return base * 0.72
    if temp >= 31:
        return base * 0.86
    if temp < 17:
        return base * 0.78
    return base


def _vento(kmh: float | None, gust: float | None, tipo: str | None) -> float:
    if kmh is None:
        return 0.55
    if kmh < 4:
        return 1.0
    if kmh <= 8:
        speed = 1.0
    elif kmh <= 15:
        speed = 0.9
    elif kmh <= 22:
        speed = 0.72
    elif kmh <= 32:
        speed = 0.45
    elif kmh <= 45:
        speed = 0.25
    else:
        speed = 0.1
    if kmh <= 12:
        mult = {"terral": 1.0, "cruzado": 0.95, "maral": 0.9}.get(tipo or "", 0.9)
    else:
        mult = {"terral": 1.0, "cruzado": 0.88, "maral": 0.72}.get(tipo or "", 0.85)
    if gust is not None and gust - kmh > 12:
        mult *= 0.88
    return min(1.0, speed * mult)


def _chuva(mm: float | None) -> float:
    if mm is None:
        return 0.7
    if mm <= 0:
        return 1.0
    if mm < 0.5:
        return 0.85
    if mm < 2:
        return 0.65
    if mm < 5:
        return 0.4
    if mm < 12:
        return 0.22
    return 0.1


def _mare(rate_cm_h: float | None, rising: bool | None) -> float:
    if rate_cm_h is None:
        return 0.55
    spd = abs(rate_cm_h)
    if spd < 4:
        score = 0.25
    elif spd < 10:
        score = 0.68
    elif spd <= 50:
        score = 1.0
    else:
        score = 0.82
    if rising and spd >= 4:
        score = min(1.0, score * 1.06)
    return score


def _coef_score(coef: int | None) -> float:
    if coef is None:
        return 0.55
    if coef < 35:
        return 0.38
    if coef < 50:
        return 0.7
    if coef < 80:
        return 1.0
    if coef <= 100:
        return 0.82
    return 0.64


def _pressao(hpa: float | None, delta3: float | None) -> float:
    if hpa is None:
        return 0.6
    if 1013 <= hpa <= 1022:
        level = 1.0
    elif 1008 <= hpa <= 1026:
        level = 0.84
    elif 1003 <= hpa <= 1030:
        level = 0.62
    else:
        level = 0.4
    if delta3 is None:
        trend = 0.75
    elif -0.6 <= delta3 <= 1.5:
        trend = 0.95
    elif 1.5 < delta3 <= 3:
        trend = 1.0
    elif delta3 > 3:
        trend = 0.72
    elif -2.2 <= delta3 < -0.6:
        trend = 0.55
    else:
        trend = 0.3
    return 0.4 * level + 0.6 * trend


def _onda(meters: float | None) -> float:
    if meters is None:
        return 0.7
    if meters < 0.25:
        return 0.78
    if meters <= 0.9:
        return 1.0
    if meters <= 1.3:
        return 0.74
    if meters <= 1.8:
        return 0.46
    return 0.22


def _lua(age: int | None, major: bool, minor: bool) -> float:
    base = _phase_factor(age)
    extra = 1.0 if major else 0.65 if minor else 0.22
    return 0.6 * base + 0.4 * extra


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def _lerp(a: float | None, b: float | None, t: float) -> float | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a + (b - a) * t


def _circ(a: float | None, b: float | None, t: float) -> float | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    delta = (b - a + 540) % 360 - 180
    return (a + delta * t) % 360


def _slot(rows: dict, day: date, index: int, field: str) -> float | None:
    row = rows.get(day)
    if not row:
        return None
    values = row[field]
    if index < 0 or index >= len(values):
        return None
    return values[index]


def _model_value(rows: dict, moment: datetime, field: str, circular: bool = False) -> float | None:
    day = moment.date()
    hour = moment.hour
    if hour % 3 == 0:
        return _slot(rows, day, hour // 3, field)
    lo = (hour // 3) * 3
    frac = (hour - lo) / 3
    v1 = _slot(rows, day, lo // 3, field)
    if lo + 3 >= 24:
        v2 = _slot(rows, day + timedelta(days=1), 0, field)
    else:
        v2 = _slot(rows, day, (lo + 3) // 3, field)
    return _circ(v1, v2, frac) if circular else _lerp(v1, v2, frac)


def _rain_value(rows: dict, moment: datetime) -> float | None:
    lo = (moment.hour // 3) * 3
    return _slot(rows, moment.date(), lo // 3, "rain")


def _tide_state(moment: datetime, extrema: list[dict]) -> dict:
    if len(extrema) < 2:
        return {"height": None, "rate": None, "rising": None, "slack": None, "label": "sem tábua nesta hora"}
    prev = None
    nxt = None
    for item in extrema:
        if item["dt"] <= moment:
            prev = item
        elif nxt is None:
            nxt = item
            break
    if prev is None or nxt is None:
        return {"height": None, "rate": None, "rising": None, "slack": None, "label": "fora da tábua"}
    span_h = (nxt["dt"] - prev["dt"]).total_seconds() / 3600
    if span_h <= 0:
        return {"height": prev["height"], "rate": 0, "rising": None, "slack": True, "label": "virada"}
    frac = (moment - prev["dt"]).total_seconds() / 3600 / span_h
    frac = max(0.0, min(1.0, frac))
    height = prev["height"] + (nxt["height"] - prev["height"]) * (1 - math.cos(math.pi * frac)) / 2
    rate_m_h = (nxt["height"] - prev["height"]) * math.pi * math.sin(math.pi * frac) / (2 * span_h)
    rate_cm = rate_m_h * 100
    nearest = prev if abs((moment - prev["dt"]).total_seconds()) <= abs((nxt["dt"] - moment).total_seconds()) else nxt
    slack = abs(rate_cm) < 4 or abs((moment - nearest["dt"]).total_seconds()) < 20 * 60
    high = nearest["height"] >= (prev["height"] if nearest is nxt else nxt["height"])
    if slack:
        label = "Virada de preamar" if high else "Virada de baixamar"
        phrase = f"{label}, {_comma(height, 1)} m"
        return {"height": height, "rate": rate_cm, "rising": rate_m_h > 0, "slack": True, "label": phrase}
    if rate_m_h >= 0:
        phrase = f"Enchente, {_comma(height, 1)} m, sobe uns {abs(rate_cm):.0f} cm/h"
    else:
        phrase = f"Vazante, {_comma(height, 1)} m, desce uns {abs(rate_cm):.0f} cm/h"
    return {"height": height, "rate": rate_cm, "rising": rate_m_h > 0, "slack": False, "label": phrase}


def _sky(clouds: float | None, rain: float | None) -> str:
    if rain and rain >= 0.5:
        return ""
    if clouds is None:
        return ""
    if clouds >= 80:
        return "céu fechado"
    if clouds >= 50:
        return "nublado"
    if clouds >= 20:
        return "parcialmente aberto"
    return "céu limpo"


def _note(score: int, moment: datetime, sunrise, sunset, major: bool, rain: float | None, wind: float | None, slack: bool) -> str:
    def near(target, window: float) -> bool:
        return target is not None and abs((moment - target).total_seconds()) <= window * 60

    if rain is not None and rain >= 5:
        return "Chuva atrapalha a diversão."
    if wind is not None and wind >= 40:
        return "Vento forte demais."
    if near(sunrise, 75) and score >= 70:
        return "Alvorada a favor."
    if near(sunset, 75) and score >= 70:
        return "Entardecer a favor."
    if major and score >= 65:
        return "Janela de lua."
    if slack and score < 78:
        return "Virada de maré."
    if score >= 82:
        return "Hora forte do dia."
    if score >= 68:
        return "Dá para ir."
    if score >= 52:
        return "Meio-termo."
    return "Melhor outro horário."


def _verdict(score: int) -> str:
    if score >= 85:
        return "excelente para sair"
    if score >= 70:
        return "bom para pescar"
    if score >= 55:
        return "regular"
    if score >= 40:
        return "fraco"
    return "ruim para diversão"


def _join_hours(hours: list[int]) -> str:
    labels = [_hh(h) for h in hours]
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " e " + labels[-1]


def _pressure_trend(delta: float | None) -> str:
    if delta is None:
        return "sem tendência"
    if delta >= 1.5:
        return "subindo"
    if delta <= -1.5:
        return "caindo"
    return "estável"


def _station_name(raw: str) -> str:
    if not raw or raw == "-":
        return ""
    small = {"do", "da", "de", "dos", "das", "e"}
    words = []
    for i, word in enumerate(raw.lower().split()):
        words.append(word if i and word in small else word.capitalize())
    return " ".join(words)


def build_forecast(parsed: dict, source_url: str) -> dict:
    ocean = {row["date"]: row for row in parsed["ocean"]}
    meteo = {row["date"]: row for row in parsed["meteo"]}
    if not ocean or not meteo:
        raise ForecastError("Esta praia veio sem dias de previsão.")

    extrema = []
    astro_events = []
    for day, row in ocean.items():
        for hh, mm, height in row["tides"]:
            kind = "extremo"
            extrema.append({"dt": _at(day, hh, mm), "height": height, "kind": kind})
        for kind, hh, mm in row["astros"]:
            astro_events.append({"dt": _at(day, hh, mm), "kind": kind})
    extrema.sort(key=lambda item: item["dt"])
    for i, item in enumerate(extrema):
        prev_h = extrema[i - 1]["height"] if i else None
        next_h = extrema[i + 1]["height"] if i + 1 < len(extrema) else None
        ref = next_h if next_h is not None else prev_h
        item["high"] = ref is not None and item["height"] >= ref

    moon_marks = []
    for event in astro_events:
        if event["kind"] == 2:
            moon_marks.append((event["dt"], "major-source"))
        elif event["kind"] == 3:
            moon_marks.append((event["dt"], "set"))
    windows = []
    rises = [e["dt"] for e in astro_events if e["kind"] == 2]
    sets = [e["dt"] for e in astro_events if e["kind"] == 3]
    for rise in rises:
        nxt = next((s for s in sets if s > rise), None)
        if nxt is None:
            continue
        dur = (nxt - rise).total_seconds()
        if not 3600 <= dur <= 18 * 3600:
            continue
        mid = rise + (nxt - rise) / 2
        windows.append((mid, "major"))
        windows.append((mid + timedelta(hours=12), "major"))
    for event in astro_events:
        if event["kind"] in (2, 3):
            windows.append((event["dt"], "minor"))

    def near_window(moment: datetime, kind: str, minutes: float) -> bool:
        limit = minutes * 60
        return any(
            mark == kind and abs((moment - when).total_seconds()) <= limit
            for when, mark in windows
        )

    days = []
    all_dates = sorted(set(ocean) & set(meteo))
    for day in all_dates:
        o = ocean[day]
        sunrises = [e["dt"] for e in astro_events if e["kind"] == 0 and e["dt"].date() == day]
        sunsets = [e["dt"] for e in astro_events if e["kind"] == 1 and e["dt"].date() == day]
        sunrise = sunrises[0] if sunrises else None
        sunset = sunsets[0] if sunsets else None
        day_extrema = [e for e in extrema if e["dt"].date() == day]
        swings = []
        for left, right in zip(extrema, extrema[1:]):
            mid = left["dt"] + (right["dt"] - left["dt"]) / 2
            if mid.date() == day:
                swings.append(abs(right["height"] - left["height"]))
        range_m = max(swings) if swings else None
        spring = parsed.get("spring") or SPRING_RANGE_M
        coef = tide_coefficient(range_m, spring) if range_m is not None else None
        age = o["moonAge"]
        hours = []
        for hour in range(24):
            moment = _at(day, hour)
            wind_kt = _model_value(meteo, moment, "windKt")
            gust_kt = _model_value(meteo, moment, "gustKt")
            wind_dir = _model_value(meteo, moment, "windDir", circular=True)
            temp = _model_value(meteo, moment, "temp")
            clouds = _model_value(meteo, moment, "clouds")
            pressure = _model_value(meteo, moment, "pressure")
            earlier = _model_value(meteo, moment - timedelta(hours=3), "pressure")
            pressure = None if pressure is None else round(pressure)
            earlier = None if earlier is None else round(earlier)
            delta_p = None if pressure is None or earlier is None else pressure - earlier
            rain = _rain_value(meteo, moment)
            wave = _wave_at(parsed["surf"], ocean, day, hour)
            period = _wave_period(ocean, day, hour)
            tide = _tide_state(moment, extrema)
            kmh = None if wind_kt is None else wind_kt * KN_TO_KMH
            gust = None if gust_kt is None else gust_kt * KN_TO_KMH
            tipo = wind_type(parsed["orientacao"], wind_dir, parsed["anguloTerral"]) if wind_dir is not None and kmh and kmh >= 4 else None
            major = near_window(moment, "major", 75)
            minor = near_window(moment, "minor", 45)
            parts = {
                "mare": _mare(tide["rate"], tide["rising"]) * 22,
                "vento": _vento(kmh, gust, tipo) * 18,
                "clima": _clima(moment, sunrise, sunset, temp) * 14,
                "lua": _lua(age, major, minor) * 12,
                "chuva": _chuva(rain) * 10,
                "coef": _coef_score(coef) * 10,
                "pressao": _pressao(pressure, delta_p) * 8,
                "onda": _onda(wave) * 6,
            }
            score = int(round(max(0, min(100, sum(parts.values())))))
            if kmh is None:
                wind_phrase = "Vento sem leitura"
            elif kmh < 4:
                wind_phrase = "Vento quase parado"
            else:
                wind_phrase = f"Vento {tipo} de {direction_name(wind_dir)}, {kmh:.0f} km/h"
                if gust is not None and gust > kmh + 8:
                    wind_phrase += f", rajada {gust:.0f}"
            if rain is None:
                rain_phrase = "chuva sem leitura"
            elif rain <= 0:
                rain_phrase = "sem chuva"
            elif rain < 1:
                rain_phrase = "chuvisco no bloco de 3 h"
            elif rain < 4:
                rain_phrase = f"chuva fraca, {_comma(rain, 0)} mm no bloco de 3 h"
            elif rain < 10:
                rain_phrase = f"chuva moderada, {_comma(rain, 0)} mm no bloco de 3 h"
            else:
                rain_phrase = f"chuva forte, {_comma(rain, 0)} mm no bloco de 3 h"
            if pressure is None:
                pressure_phrase = "pressão sem leitura"
            else:
                pressure_phrase = f"pressão {pressure:.0f} hPa, {_pressure_trend(delta_p)}"
            temp_phrase = "" if temp is None else f", ar {temp:.0f}°C"
            text = f"{tide['label']}. {wind_phrase}{temp_phrase}, {rain_phrase}, {pressure_phrase}."
            note = _note(score, moment, sunrise, sunset, major, rain, kmh, bool(tide["slack"]))
            sky = _sky(clouds, rain)
            meta_bits = []
            if wave is not None:
                bit = f"Onda na praia {_comma(wave, 1)} m"
                if period is not None:
                    bit += f" · {_comma(period, 0)} s"
                meta_bits.append(bit)
            if parsed["tsm"] is not None:
                meta_bits.append(f"água {_comma(parsed['tsm'], 0)}°C")
            if sky:
                meta_bits.append(sky)
            hours.append(
                {
                    "hour": hour,
                    "score": score,
                    "note": note,
                    "text": text,
                    "meta": " · ".join(meta_bits),
                    "windKmh": None if kmh is None else round(kmh),
                    "rainMm": None if rain is None else round(rain, 1),
                    "pressure": None if pressure is None else round(pressure),
                    "temp": None if temp is None else round(temp),
                    "tideM": None if tide["height"] is None else round(tide["height"], 2),
                    "waveM": None if wave is None else round(wave, 2),
                    "periodS": None if period is None else round(period),
                    "clouds": None if clouds is None else round(clouds),
                    "major": major,
                    "minor": minor,
                    "parts": parts,
                }
            )
        days.append(
            _finish_day(
                day,
                hours,
                coef,
                range_m,
                age,
                day_extrema,
                sunrise,
                sunset,
                astro_events,
                windows,
                parsed,
            )
        )
    if not days:
        raise ForecastError("Não há dia com clima e maré juntos.")
    now = datetime.now(TZ)
    return {
        "place": {
            "praia": parsed["praia"],
            "cidade": parsed["cidade"],
            "estado": parsed.get("estado") or "",
            "estacao": _station_name(parsed["estacao"]),
            "lat": parsed["lat"],
            "lon": parsed["lon"],
            "agua": None if parsed["tsm"] is None else round(parsed["tsm"], 1),
        },
        "sourceUrl": source_url,
        "tideUrl": (
            f"{SURF}/previsao/mare/{int(parsed['estacaoId'])}"
            if parsed["estacaoId"]
            else ""
        ),
        "clockLabel": clock_label(int(parsed.get("fuso") or -3)),
        "fetchedAt": now.isoformat(),
        "today": now.date().isoformat(),
        "nowHour": now.hour,
        "weights": [{"key": k, "label": label, "max": weight} for k, label, weight in WEIGHTS],
        "days": days,
    }


def _wave_at(surf: dict, ocean: dict, day: date, hour: int) -> float | None:
    moment = _at(day, hour)
    rows = {}
    field = "wave"
    use_surf = day in surf and any(v is not None for v in surf[day])
    if use_surf:
        for d, values in surf.items():
            rows[d] = {field: values}
        dm = _model_value(rows, moment, field)
        return None if dm is None else dm / 10
    for d, row in ocean.items():
        rows[d] = {field: row["height"]}
    dm = _model_value(rows, moment, field)
    return None if dm is None else dm / 10


def _wave_period(ocean: dict, day: date, hour: int) -> float | None:
    rows = {d: {"period": row["period"]} for d, row in ocean.items()}
    ds = _model_value(rows, _at(day, hour), "period")
    if ds is None:
        return None
    seconds = ds / 10
    if seconds <= 0 or seconds > 30:
        return None
    return seconds


def _finish_day(day, hours, coef, range_m, age, day_extrema, sunrise, sunset, astro_events, windows, parsed) -> dict:
    sunrise_hour = (sunrise.hour + sunrise.minute / 60) if sunrise else 5.5
    best_start = 0
    best_avg = -1.0
    for start in range(0, 22):
        avg = sum(hours[h]["score"] for h in range(start, start + 3)) / 3
        center = start + 1
        if avg > best_avg + 0.05 or (
            abs(avg - best_avg) <= 0.05 and abs(center - sunrise_hour) < abs((best_start + 1) - sunrise_hour)
        ):
            best_avg = avg
            best_start = start
    best_hours = [best_start, best_start + 1, best_start + 2]
    for item in hours:
        item["best"] = item["hour"] in best_hours
    breakdown = []
    for key, label, weight in WEIGHTS:
        got = sum(hours[h]["parts"][key] for h in best_hours) / 3
        breakdown.append({"key": key, "label": label, "score": round(got, 1), "max": weight})
    for item in hours:
        item.pop("parts", None)
    rank = int(round(best_avg))
    outside = [item["score"] for item in hours if item["hour"] not in best_hours]
    outside_mean = round(sum(outside) / len(outside)) if outside else rank
    daylight = [item["score"] for item in hours if 5 <= item["hour"] <= 18]
    daylight_mean = round(sum(daylight) / len(daylight)) if daylight else rank

    def rng(field: str, lo: int, hi: int):
        vals = [item[field] for item in hours if lo <= item["hour"] <= hi and item[field] is not None]
        return vals

    winds = rng("windKmh", 5, 18)
    temps = rng("temp", 5, 18)
    press = rng("pressure", 0, 23)
    waves = rng("waveM", 5, 18)
    rain_slots = []
    for hour in range(0, 24, 3):
        value = hours[hour]["rainMm"]
        if value is not None:
            rain_slots.append(value)
    rain_sum = round(sum(rain_slots), 1)

    tide_bits = []
    for item in day_extrema:
        name = "preamar" if item.get("high") else "baixamar"
        tide_bits.append(f"{name} {_hm(item['dt'])} ({_comma(item['height'], 1)} m)")

    majors = sorted({when for when, kind in windows if kind == "major" and when.date() == day})
    moon_events = []
    for event in astro_events:
        if event["dt"].date() != day or event["kind"] not in (2, 3):
            continue
        verb = "nasce" if event["kind"] == 2 else "se põe"
        moon_events.append((event["dt"], verb))
    moon_events.sort()

    wd = WEEKDAYS[day.weekday()]
    place = f"{parsed['praia']}, {parsed['cidade']}"
    if parsed.get("estado"):
        place = f"{place}, {parsed['estado']}"
    p1 = f"{wd.capitalize()}, {day.day} de {MONTHS[day.month]}, em {place}."
    turns = [item["hour"] for item in hours if item["text"].startswith("Virada de preamar") and item["score"] < 75]
    if turns:
        p1 += f" Nas preamares ({_join_hours(turns)}) a água para e o rank cai."
    elif outside_mean <= rank - 12:
        p1 += f" Fora dela, a média das outras horas fica em {outside_mean}."
    p1 += f" Entre 5h e 18h a média é {daylight_mean}."

    p2_bits = [f"Lua {moon_name(age)}."]
    if coef is not None and range_m is not None:
        p2_bits.append(
            f"Coeficiente {coef}, {coef_words(coef)}, maior variação de {_comma(range_m, 1)} m entre uma maré e a seguinte."
        )
    if tide_bits:
        listed = ", ".join(tide_bits[:-1]) + " e " + tide_bits[-1] if len(tide_bits) > 1 else tide_bits[0]
        p2_bits.append(listed[:1].upper() + listed[1:] + ".")
    if sunrise and sunset:
        p2_bits.append(f"Sol nasce {_hm(sunrise)} e se põe {_hm(sunset)}.")
    if moon_events:
        p2_bits.append("Lua " + " e ".join(f"{verb} {_hm(when)}" for when, verb in moon_events) + ".")
    if majors:
        nomes = " e ".join(_hm(m) for m in majors[:3])
        p2_bits.append("Pico de lua por volta de " + nomes + ".")
    station = _station_name(parsed["estacao"])
    if station:
        p2_bits.append(f"Tábua de {station}.")

    p3 = []
    if winds:
        p3.append(f"Vento de {min(winds):.0f} a {max(winds):.0f} km/h entre 5h e 18h.")
    if rain_sum <= 0:
        p3.append("Sem chuva prevista nos blocos do dia.")
    else:
        p3.append(f"Chuva somando cerca de {_comma(rain_sum, 0)} mm nos blocos de 3 h.")
    if press:
        net = press[-1] - press[0] if len(press) >= 2 else 0
        if net <= -3:
            move = "em queda"
        elif net >= 3:
            move = "em alta"
        else:
            move = "estável"
        p3.append(f"Pressão {move}, entre {min(press):.0f} e {max(press):.0f} hPa.")
    if temps:
        p3.append(f"Ar entre {min(temps):.0f} e {max(temps):.0f}°C.")
    if parsed["tsm"] is not None:
        p3.append(f"Água do mar cerca de {_comma(parsed['tsm'], 0)}°C.")
    clouds = [item["clouds"] for item in hours if 6 <= item["hour"] <= 18 and item.get("clouds") is not None]
    if clouds and min(clouds) >= 75:
        p3.append("Céu fechado.")
    elif clouds and max(clouds) <= 25:
        p3.append("Céu limpo.")
    if waves:
        lo, hi = min(waves), max(waves)
        if hi - lo < 0.15:
            wave_bit = f"Onda na praia por volta de {_comma((lo + hi) / 2, 1)} m"
        else:
            wave_bit = f"Onda na praia entre {_comma(lo, 1)} e {_comma(hi, 1)} m"
        periods = [item["periodS"] for item in hours if 5 <= item["hour"] <= 18 and item.get("periodS")]
        if periods:
            wave_bit += f", período perto de {round(sum(periods) / len(periods))} s"
        p3.append(wave_bit + ".")

    # Breakdown of the best window, averaged from the same weights.
    # Recomputed visually from stored hour scores is enough; parts live in the sentence.
    return {
        "date": day.isoformat(),
        "weekday": wd,
        "weekdayShort": WEEKDAYS_SHORT[day.weekday()],
        "day": day.day,
        "month": MONTHS[day.month],
        "score": rank,
        "verdict": _verdict(rank),
        "bestHours": best_hours,
        "daylightMean": daylight_mean,
        "outsideMean": outside_mean,
        "coef": coef,
        "coefLabel": coef_words(coef) if coef is not None else "",
        "rangeM": None if range_m is None else round(range_m, 2),
        "moon": moon_name(age),
        "moonAge": age,
        "paragraphs": [p1, " ".join(p2_bits), " ".join(p3)],
        "tides": [
            {
                "time": _hm(item["dt"]),
                "height": round(item["height"], 2),
                "high": bool(item.get("high")),
            }
            for item in day_extrema
        ],
        "sunrise": _hm(sunrise) if sunrise else "",
        "sunset": _hm(sunset) if sunset else "",
        "breakdown": breakdown,
        "hours": hours,
    }


def beach_url(estado: str, municipio: str, praia: str) -> str:
    return f"{SURF}/previsao/brasil/{estado}/{municipio}/{praia}"


def load_beach(estado: str, municipio: str, praia: str) -> dict:
    url = beach_url(estado, municipio, praia)
    html = fetch_text(url)
    if "var ondas" not in html or "var meteoro" not in html:
        raise ForecastError("O SurfGuru não entregou a previsão deste ponto.")
    page = parse_page(html)
    page["spring"] = SPRING_BY_STATE.get(estado, SPRING_RANGE_M)
    return build_forecast(page, url)


def _nice(name: str, slug: str) -> str:
    text = (name or slug).strip()
    small = {"de", "da", "do", "das", "dos", "e"}
    if text.upper() == text:
        words = []
        for i, word in enumerate(text.split()):
            low = word.lower()
            words.append(low if i and low in small else low.capitalize())
        return " ".join(words)
    return text


def _places(url: str) -> list[dict]:
    data = json.loads(fetch_text(url))
    out = []
    for item in data:
        slug = item.get("slug") or ""
        if not slug:
            continue
        out.append({"slug": slug, "nome": _nice(item.get("nome") or "", slug)})
    return out


def list_estados() -> list[dict]:
    return _places(f"{SURF}/locais/estados/brasil")


def list_municipios(estado: str) -> list[dict]:
    return _places(f"{SURF}/locais/municipios/brasil/{estado}")


def list_praias(estado: str, municipio: str) -> list[dict]:
    return _places(f"{SURF}/locais/praias/brasil/{estado}/{municipio}")


def _self_test() -> None:
    assert tide_coefficient(1.6) == 95
    assert tide_coefficient(0.4) < 40
    assert wind_type(150, 150, 125) == "maral"
    assert wind_type(150, 330, 125) == "terral"
    assert wind_type(150, 80, 125) == "cruzado"
    assert moon_name(20) == "gibosa minguante"
    assert moon_name(16) == "lua cheia"
    a = _at(date(2026, 10, 28), 3, 0)
    b = _at(date(2026, 10, 28), 9, 0)
    mid = _at(date(2026, 10, 28), 6, 0)
    state = _tide_state(mid, [{"dt": a, "height": 0.2, "high": False}, {"dt": b, "height": 1.8, "high": True}])
    assert abs(state["height"] - 1.0) < 0.05
    assert state["rising"] is True
    assert "Enchente" in state["label"]
    assert sum(w for _, _, w in WEIGHTS) == 100


if __name__ == "__main__":
    import sys

    _self_test()
    print("self-test ok")
    if "--check" in sys.argv:
        report = load_beach("espirito-santo", "vitoria", "praia-camburi")
        today = next(d for d in report["days"] if d["date"] == report["today"])
        assert today["hours"] and len(today["hours"]) == 24
        assert 0 <= today["score"] <= 100
        for hour in today["hours"]:
            assert 0 <= hour["score"] <= 100
            if hour["pressure"] is not None:
                assert 980 <= hour["pressure"] <= 1050
            if hour["windKmh"] is not None:
                assert 0 <= hour["windKmh"] <= 150
            if hour["tideM"] is not None:
                assert -0.5 <= hour["tideM"] <= 4
        other = load_beach("espirito-santo", "vila-velha", "oceanica")
        assert other["days"]
        print(json.dumps({"place": report["place"], "today": report["today"], "day": {k: today[k] for k in today if k != "hours"}}, ensure_ascii=False, indent=2))
        for hour in today["hours"]:
            print(f"{hour['hour']:02d} {hour['score']:3d}  {hour['note']}  {hour['text']}  {hour['meta']}")
