# -*- coding: utf-8 -*-
"""
봉룡학사(자과캠 기숙사) 식단 읽기
--------------------------------
주간 식단표 페이지(dorm_restaurant_table.jsp)는 달력만 있고, 실제 메뉴는 아래 페이지를 iframe 으로 불러온다.
    https://dorm.skku.edu/_custom/skku/_common/board/schedule_menu/food_menu_page.jsp?date=YYYY-MM-DD&board_no=61&lng=ko

구조 (2026-11-25 저장본 기준)
    <p class="board-menu-date">2026-11-25</p>
    <div id="foodlist01"> 아침 / foodlist02 점심 / foodlist03 저녁
      <ul><li>
        <span class="board-menu-title0N">공통</span>          <- 코너 이름 (신관 / 공통 / Take-out(공통) ...)
        <p>11:30~13:00,부대햄김치덮밥,우동국,김말이튀김,단무지</p>  <- "시간,메뉴1,메뉴2,..."
      </li></ul>
    </div>
"""

import re
import time
import unicodedata
from datetime import date

import requests
from bs4 import BeautifulSoup

MENU_URL = "https://dorm.skku.edu/_custom/skku/_common/board/schedule_menu/food_menu_page.jsp"
PAGE_URL = "https://dorm.skku.edu/dorm_suwon/lifeguide/dorm_restaurant_table.jsp"
BOARD_NO = "61"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

CONNECT_TIMEOUT = 15   # 학교 서버는 연결이 안 될 때 응답 없이 매달리므로 짧게 끊고 재시도
READ_TIMEOUT = 30
TOTAL_FETCH_SECONDS = 480  # 최대 8분까지 재시도 (아침 메시지가 너무 늦지 않게)

# 메인 메뉴로 치지 않을 밥 종류 (첫 항목이 밥이면 그 다음 항목을 메인으로)
RICE = {"밥", "쌀밥", "백미밥", "잡곡밥", "흑미밥", "현미밥", "보리밥", "기장밥", "귀리밥"}

TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2}\s*[~\-]\s*\d{1,2}:\d{2})\s*,?\s*")
PRICE_RE = re.compile(r"\*\s*[\d,]+\s*(원)?\s*\*")  # 특식 가격 표시 "*6,500*"
NO_MENU_RE = re.compile(r"식단이\s*없습니다")       # 식단 없는 날 안내 문구


class MenuPageError(Exception):
    """페이지는 받았지만 식단 구조를 찾지 못함 (사이트 구조 변경, 오류 페이지 등)"""


def menu_url(day: date) -> str:
    return f"{MENU_URL}?date={day.isoformat()}&board_no={BOARD_NO}&lng=ko"


def fetch_menu_html(day: date, log=print) -> str:
    """접속이 될 때까지 TOTAL_FETCH_SECONDS 동안 재시도한다."""
    url = menu_url(day)
    give_up_at = time.monotonic() + TOTAL_FETCH_SECONDS
    session = requests.Session()
    session.headers.update(HEADERS)
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        try:
            resp = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            if attempt > 1:
                log(f"  ({attempt}번째 시도에 접속 성공)")
            return resp.text
        except requests.HTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", 0) or 0
            if 400 <= code < 500:
                raise  # 404 등은 재시도해도 소용없음
            error = e
        except requests.RequestException as e:
            error = e
        log(f"  ! {attempt}번째 시도 실패: {type(error).__name__}")
        if time.monotonic() + 5 >= give_up_at:
            raise error
        # 연결이 바로 거부된 경우엔 조금 쉬고, 시간 초과였으면 바로 다시 시도
        if time.monotonic() - started < CONNECT_TIMEOUT - 1:
            time.sleep(5)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def split_items(text: str) -> list:
    """쉼표로 나누되 괄호 안의 쉼표는 나누지 않는다."""
    items, buf, depth = [], "", 0
    for ch in text:
        if ch in "([{（":
            depth += 1
        elif ch in ")]}）" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    out = []
    for it in items:
        it = it.split(" / ")[0]  # "깍두기 / 1종과일(사과)" -> 뒤쪽 덧붙임 제거
        it = it.strip(" *")
        if it:
            out.append(it)
    return out


def parse_corner_text(text: str) -> dict:
    """'11:30~13:00,*6,500*뚝배기돼지갈비찜,쌀밥,...' -> {"time", "items", "special"}"""
    text = _clean(text)
    time_str = ""
    m = TIME_RE.match(text)
    if m:
        time_str = re.sub(r"\s+", "", m.group(1))
        text = text[m.end():]
    special = bool(PRICE_RE.search(text))
    text = PRICE_RE.sub("", text)
    return {"time": time_str, "items": split_items(text), "special": special}


def main_dish(items: list) -> str:
    for it in items:
        if it.replace(" ", "") not in RICE:
            return it
    return items[0] if items else ""


def parse_menu(html: str, meals: list) -> dict:
    """
    반환: {"date": "YYYY-MM-DD" | None,
           "meals": {"점심": [{"corner", "time", "items", "special"}, ...], ...}}
    meals: config 의 meals 목록 ([{"name": "점심", "tab": "foodlist02"}, ...])
    """
    soup = BeautifulSoup(html, "html.parser")
    date_el = soup.select_one(".board-menu-date")
    page_date = _clean(date_el.get_text()) if date_el else None

    # 식단이 없는 날(주말·공휴일)은 메뉴 대신 "오늘의 식단이 없습니다." 문구만 나온다.
    # 페이지 구조와 상관없이 이 문구가 있으면 '식단 없음'으로 처리한다.
    page_text = _clean(soup.get_text(" "))
    if NO_MENU_RE.search(page_text):
        return {"date": page_date, "meals": {meal["name"]: [] for meal in meals}, "no_menu": True}

    wrap = soup.select_one(".board-foodlist-wrap")
    if wrap is None:
        raise MenuPageError("식단 영역(.board-foodlist-wrap)을 찾지 못함")

    result = {}
    for meal in meals:
        box = soup.find(id=meal["tab"])
        corners = []
        if box is not None:
            for li in box.select("li"):
                title = li.find("span")
                body = li.find("p")
                if not title or not body:
                    continue
                corner = _clean(title.get_text())
                parsed = parse_corner_text(body.get_text(" "))
                if not parsed["items"]:
                    continue
                parsed["corner"] = corner
                corners.append(parsed)
        result[meal["name"]] = corners
    return {"date": page_date, "meals": result, "no_menu": False}


def pick(menu: dict, corners: list) -> dict:
    """원하는 코너만 남긴다 (이름이 정확히 같은 것만: '공통'은 'Take-out(공통)'과 다름)."""
    wanted = {_clean(c) for c in corners}
    return {
        meal: [c for c in items if c["corner"] in wanted]
        for meal, items in menu["meals"].items()
    }
