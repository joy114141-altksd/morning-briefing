# -*- coding: utf-8 -*-
"""
학교 홈페이지 식당 메뉴 읽기 (자과캠 행단골·해오름 등 같은 페이지 틀)
-----------------------------------------------------------------
    https://www.skku.edu/skku/campus/support/welfare_11_m.do
        ?mode=info&srDt=YYYY-MM-DD&srCategory=<B|L|D>&conspaceCd=<건물>&srResId=<식당>&srShowTime=W
    srCategory: B 조식 / L 중식 / D 석식,  srShowTime=W: 그 주 월~금이 한 페이지에

    행단골식당  conspaceCd=20201104  srResId=3
    해오름식당  conspaceCd=20201251  srResId=12   (공대식당, 제2공학관 지하1층)

구조 (2026-11-26 행단골 조식 저장본 기준)
    <div class="cafeteria_info"> ... <pre>○학기 중: 조식-08:00~09:00,중식-11:00~13:30 ...</pre>
    <div class="weeListWrap">
      <div class="weeListTit"> 목 (11.26) </div>
      <div class="weeListCont">                     <- 코너 하나
        <h6>1F 피키피커스 <span>코너</span></h6>
        <ul><li><span>메뉴</span><pre>
            [♥천원의아침밥:일반식♥]                 <- 구분 표시 (없는 식당도 있음)
            참치김치찌개
            ...
        </pre></li></ul>
      </div>
    </div>
"""

import re
import unicodedata
from datetime import date

from bs4 import BeautifulSoup

import web

PAGE_URL = "https://www.skku.edu/skku/campus/support/welfare_11_m.do"
TOTAL_FETCH_SECONDS = 180

DAY_RE = re.compile(r"\((\d{1,2})\.(\d{1,2})\)")
HEADER_RE = re.compile(r"^\[(.*)\]$")
TIME_WORDS = {"B": "조식|아침", "L": "중식|점심", "D": "석식|저녁"}

# 메인으로 치지 않을 항목 (밥, 국물 곁들임, 김치류)
NOT_MAIN = re.compile(r"^(쌀밥|밥|백미밥|잡곡밥|흑미밥|현미밥|기장밥|보리밥)(/.*)?$|김치$|깍두기$|단무지$|숭늉$")
ORIGIN_RE = re.compile(r"\s*[\[(（][^\])）]*(국내산|산\b|:)[^\])）]*[\])）]")  # "(돈육:국내산)" 같은 원산지 표기


class MenuPageError(Exception):
    """페이지는 받았지만 식단 구조를 찾지 못함"""


def menu_url(day: date, category: str, conspace_cd: str, res_id: str) -> str:
    return (f"{PAGE_URL}?mode=info&srDt={day.isoformat()}&srCategory={category}"
            f"&conspaceCd={conspace_cd}&srResId={res_id}&srShowTime=W")


def fetch(url: str, log=print) -> str:
    return web.fetch_html(url, TOTAL_FETCH_SECONDS, log=log)


def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", unicodedata.normalize("NFC", text)).strip()


def parse_sections(pre_text: str) -> dict:
    """'[♥천원의아침밥:일반식♥]\\n참치김치찌개\\n...' -> {"일반식": [...], "컵밥": [...]}.  구분 표시가 없으면 {"메뉴": [...]}"""
    sections, current = {}, None
    for raw in pre_text.splitlines():
        line = _clean(raw)
        if not line:
            continue
        m = HEADER_RE.match(line)
        if m:
            name = m.group(1).replace("♥", "").strip()
            current = name.split(":")[-1].strip() or name  # "천원의아침밥:일반식" -> "일반식"
            sections.setdefault(current, [])
            continue
        sections.setdefault(current or "메뉴", []).append(line)
    return sections


HANGUL_RE = re.compile(r"[가-힣]")


def main_item(items: list) -> str:
    for it in items:
        if NOT_MAIN.search(it.replace(" ", "")):
            continue
        if not HANGUL_RE.search(it):
            continue  # "Burger Day" 같은 영문 행사 이름은 메뉴가 아님
        return ORIGIN_RE.sub("", it).strip()
    return ORIGIN_RE.sub("", items[0]).strip() if items else ""


def corner_name(cont) -> str:
    h6 = cont.find("h6")
    if not h6:
        return ""
    for span in h6.find_all("span"):
        span.extract()  # "<span>코너</span>" 같은 꾸밈 글자 제거
    name = _clean(h6.get_text(" "))
    return re.sub(r"^\d+F\s*", "", name)  # "1F 피키피커스" -> "피키피커스"


def meal_time(soup, category: str) -> str:
    info = soup.select_one(".cafeteria_info")
    text = info.get_text(" ") if info else ""
    m = re.search(rf"(?:{TIME_WORDS.get(category, category)})\s*[-:]?\s*(\d{{1,2}}:\d{{2}}\s*~\s*\d{{1,2}}:\d{{2}})", text)
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def parse(html: str, day: date, category: str) -> dict:
    """
    반환: {"time": "11:00~13:30" | "",
           "corners": [{"corner": "한식", "sections": {"메뉴": [...]}}, ...]}  (그날 메뉴가 없으면 corners=[])
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".cafeteria_info") is None and soup.select_one(".weekly_list") is None:
        raise MenuPageError("식당 정보(.cafeteria_info)를 찾지 못함")

    result = {"time": meal_time(soup, category), "corners": []}
    for wrap in soup.select(".weeListWrap"):
        tit = wrap.select_one(".weeListTit")
        dm = DAY_RE.search(tit.get_text(" ") if tit else "")
        if not dm or (int(dm.group(1)), int(dm.group(2))) != (day.month, day.day):
            continue
        for cont in wrap.select(".weeListCont"):
            sections = {}
            for pre in cont.select("pre"):
                for k, v in parse_sections(pre.get_text("\n")).items():
                    sections.setdefault(k, []).extend(v)
            if any(sections.values()):
                result["corners"].append({"corner": corner_name(cont), "sections": sections})
        break
    return result


# ---------------------------------------------------------
# 식당별로 보낼 메뉴 고르기
# ---------------------------------------------------------
def pick_breakfast(parsed: dict) -> list:
    """천원의 아침밥: 일반식 메인 1개 + 컵밥 1개"""
    sections = {}
    for c in parsed["corners"]:
        for k, v in c["sections"].items():
            sections.setdefault(k, []).extend(v)
    items = []
    normal = next((v for k, v in sections.items() if "일반" in k), None)
    cup = next((v for k, v in sections.items() if "컵밥" in k), None)
    if normal:
        items.append(main_item(normal))
    if cup:
        items.append(main_item(cup))
    if not items:  # 구분 표시가 없을 때: 첫 메뉴 하나
        first = next((v for v in sections.values() if v), [])
        if first:
            items.append(main_item(first))
    return items


def pick_corner_mains(parsed: dict, only_corners: list = None) -> list:
    """코너마다 메인 1개: [("한식", "제육볶음"), ...]  only_corners 가 있으면 그 코너만 (이름 일부만 같아도 됨)"""
    out = []
    for c in parsed["corners"]:
        if only_corners and not any(w in c["corner"] for w in only_corners):
            continue
        items = [it for v in c["sections"].values() for it in v]
        main = main_item(items)
        if main:
            out.append((c["corner"], main))
    # 코너 이름이 전부 같으면(예: 공대식당은 모두 "한식"으로 나옴) 이름은 빼고 메뉴만
    if len({corner for corner, _ in out}) <= 1 and len(out) > 1:
        out = [("", main) for _, main in out]
    return out
