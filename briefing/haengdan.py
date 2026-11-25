# -*- coding: utf-8 -*-
"""
행단골식당(자과캠 학생회관 1층) 천원의 아침밥 읽기
-----------------------------------------------
학교 홈페이지 식당 메뉴 페이지 (조식, 주간 보기):
    https://www.skku.edu/skku/campus/support/welfare_11_m.do
        ?mode=info&srDt=YYYY-MM-DD&srCategory=B&conspaceCd=20201104&srResId=3&srShowTime=W

구조 (2026-11-26 저장본 기준) - 그 주 월~금이 한 페이지에 나온다
    <div class="weeListWrap">
      <div class="weeListTit"> 목 (11.26) </div>
      <div class="weeListCont">
        <h6>1F 피키피커스 <span>코너</span></h6>
        <ul><li><span>메뉴</span><pre>
            [♥천원의아침밥:일반식♥]
            참치김치찌개
            부추전 ...
            [♥천원의아침밥:컵밥♥]
            제육컵밥
        </pre></li></ul>
      </div>
    </div>
식당 안내 문구에 운영시간이 있다: "○학기 중: 조식-08:00~09:00,중식-..."
"""

import re
import unicodedata
from datetime import date

from bs4 import BeautifulSoup

import web

PAGE_URL = "https://www.skku.edu/skku/campus/support/welfare_11_m.do"
TOTAL_FETCH_SECONDS = 300

DAY_RE = re.compile(r"\((\d{1,2})\.(\d{1,2})\)")
HEADER_RE = re.compile(r"^\[(.*)\]$")
TIME_RE = re.compile(r"조식\s*[-:]?\s*(\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})")

# 메인으로 치지 않을 항목 (밥, 김치류)
NOT_MAIN = re.compile(r"^(쌀밥|밥|잡곡밥|흑미밥|현미밥)(/.*)?$|김치$|깍두기$|단무지$")


class MenuPageError(Exception):
    """페이지는 받았지만 식단 구조를 찾지 못함"""


def menu_url(day: date) -> str:
    return (f"{PAGE_URL}?mode=info&srDt={day.isoformat()}&srCategory=B"
            "&conspaceCd=20201104&srResId=3&srShowTime=W")


def fetch(day: date, log=print) -> str:
    return web.fetch_html(menu_url(day), TOTAL_FETCH_SECONDS, log=log)


def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", unicodedata.normalize("NFC", text)).strip()


def parse_sections(pre_text: str) -> dict:
    """'[♥천원의아침밥:일반식♥]\\n참치김치찌개\\n...' -> {"일반식": [...], "컵밥": [...]}"""
    sections, current = {}, None
    for raw in pre_text.splitlines():
        line = _clean(raw)
        if not line:
            continue
        m = HEADER_RE.match(line)
        if m:
            name = m.group(1).replace("♥", "").strip()
            name = name.split(":")[-1].strip() or name  # "천원의아침밥:일반식" -> "일반식"
            current = name
            sections.setdefault(current, [])
            continue
        sections.setdefault(current or "메뉴", []).append(line)
    return sections


def main_item(items: list) -> str:
    for it in items:
        if not NOT_MAIN.search(it.replace(" ", "")):
            return it
    return items[0] if items else ""


def parse(html: str, day: date) -> dict:
    """
    반환: {"time": "08:00~09:00" | "", "items": [메인, 컵밥] (없으면 []), "sections": {...}}
    그날 칸이 없으면(주말·공휴일·미운영) items 가 빈 목록.
    """
    soup = BeautifulSoup(html, "html.parser")
    days = soup.select(".weeListWrap")
    if not days:
        if soup.select_one(".weekly_list") is not None or soup.select_one(".cafeteria_info") is not None:
            days = []  # 그 주 식단이 아예 없음 (방학 등)
        else:
            raise MenuPageError("식단 목록(.weeListWrap)을 찾지 못함")

    info = soup.select_one(".cafeteria_info")
    m = TIME_RE.search(info.get_text(" ") if info else "")
    time_str = re.sub(r"\s+", "", m.group(1)) if m else ""

    for wrap in days:
        tit = wrap.select_one(".weeListTit")
        dm = DAY_RE.search(tit.get_text(" ") if tit else "")
        if not dm or (int(dm.group(1)), int(dm.group(2))) != (day.month, day.day):
            continue
        sections = {}
        for cont in wrap.select(".weeListCont"):
            for pre in cont.select("pre"):
                for k, v in parse_sections(pre.get_text("\n")).items():
                    sections.setdefault(k, []).extend(v)
        items = []
        normal = next((v for k, v in sections.items() if "일반" in k), None)
        cup = next((v for k, v in sections.items() if "컵밥" in k), None)
        if normal:
            items.append(main_item(normal))
        if cup:
            items.append(cup[0])
        if not items:  # 구분 표시가 없을 때: 첫 메뉴 하나
            first = next((v for v in sections.values() if v), [])
            if first:
                items.append(main_item(first))
        return {"time": time_str, "items": items, "sections": sections}

    return {"time": time_str, "items": [], "sections": {}}
