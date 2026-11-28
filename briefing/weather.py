# -*- coding: utf-8 -*-
"""
오늘 날씨 한 마디 (우산·눈)
---------------------------
Open-Meteo(무료, API 키 필요 없음)에서 학교(자연과학캠퍼스) 시간별 예보를 받아
비나 눈이 올 것 같은 날만 날짜 옆에 짧게 붙인다.

    ☔70%      낮 동안 강수확률이 기준 이상이거나 비가 1mm 이상 올 때 (숫자는 최고 강수확률)
    ❄️        눈이 올 때 (비와 같이 오면 ❄️☔70%)
    (없음)     맑거나 흐리기만 한 날

날씨를 못 받아와도 식단은 그대로 보낸다 (표시만 빠짐).
"""

import json
from datetime import date
from pathlib import Path

import requests

API_URL = "https://api.open-meteo.com/v1/forecast"


def fetch(day: date, cfg: dict, log=print) -> dict:
    params = {
        "latitude": cfg.get("latitude", 37.2937),
        "longitude": cfg.get("longitude", 126.9746),
        "hourly": "precipitation_probability,precipitation,snowfall",
        "timezone": "Asia/Seoul",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
    }
    error = None
    for attempt in range(2):
        try:
            resp = requests.get(API_URL, params=params, timeout=(10, 15))
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            error = e
            log(f"  ! 날씨 {attempt + 1}번째 시도 실패: {type(e).__name__}")
    raise error


def summarize(data: dict, cfg: dict) -> str:
    """시간별 예보 -> '☔70%' / '❄️' / '' """
    start, end = cfg.get("hours", [7, 21])  # 이 시간대(KST)만 본다 (밤사이 비는 무시)
    hourly = data["hourly"]

    def values(key):
        """그 시간대의 값들 (빠진 값은 0)"""
        vals = hourly.get(key) or []
        return [(vals[i] if i < len(vals) else 0) or 0
                for i, t in enumerate(hourly["time"]) if start <= int(t[11:13]) <= end]  # '2026-11-28T14:00'

    prob_max = int(max(values("precipitation_probability"), default=0))
    rain_mm = sum(values("precipitation"))   # 눈도 녹인 양으로 포함됨
    snow_cm = sum(values("snowfall"))

    out = ""
    if snow_cm >= cfg.get("snow_cm", 0.1):
        out += "❄️"
    if prob_max >= cfg.get("rain_probability", 50) or rain_mm >= cfg.get("rain_mm", 1.0):
        out += f"☔{prob_max}%" if prob_max else "☔"
    elif out and prob_max:
        out += f"{prob_max}%"
    return out


def get(day: date, cfg: dict, json_path: str = None, log=print) -> str:
    """실패하면 빈 문자열 (식단 메시지는 그대로 나가게)"""
    if not cfg or not cfg.get("enabled", True):
        return ""
    try:
        if json_path:
            data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        else:
            log(f"[날씨] {day}")
            data = fetch(day, cfg, log=log)
        text = summarize(data, cfg)
        log(f"  -> 날씨 표시: {text or '없음'}")
        return text
    except Exception as e:
        log(f"  ! 날씨 확인 실패 (표시 없이 보냄): {type(e).__name__}: {str(e)[:200]}")
        return ""
