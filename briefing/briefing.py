# -*- coding: utf-8 -*-
"""
아침 브리핑
-----------
평일 아침마다 오늘의 식단을 텔레그램으로 보낸다.
  - 조식: 행단골식당 천원의 아침밥 (일반식 메인 1개 + 컵밥 1개)
  - 점심·저녁: 기숙사(봉룡학사) 공통 코너 메인 메뉴 + config.json 의 restaurants (공대식당 코너별 메인(사이드 제외), 행단골 2층 나폴리폴리 등)
  - 학교 홈페이지에 가격이 있으면 메뉴 옆에 가격
GitHub Actions 에서 실행된다 (cron-job.org 가 평일 07:00 KST 에 workflow_dispatch 를 호출).

사용법:
    python briefing.py                         # 평소 실행: 오늘 식단 전송
    python briefing.py --dry-run               # 전송 없이 메시지만 출력 (점검용)
    python briefing.py --date 2026-11-26       # 특정 날짜 식단
    python briefing.py --date 2026-11-25 --dry-run --html tests/sample_2026-11-25.html \\
        --skku-html B=tests/sample_skku_haengdan_B_2026-11-26.html \\
        --skku-html 공대:L=tests/sample_skku_haeorum_L_2026-11-26.html --skku-html 공대:D=tests/sample_skku_haeorum_D_2026-11-26.html
                                               # 저장해둔 페이지로 점검 (접속 없이)

받게 되는 메시지:
    날짜 + 조식·점심·저녁 한 줄씩 + 점심·저녁 운영시간
    "오늘은 식단이 없어요"            (공휴일 등 모든 식단이 비어 있는 날)
    '?' + 맨 아래 식단표 링크          (학교 서버 접속 실패, 페이지 구조 변경)
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import dorm_menu
import skku_menu

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

KST = timezone(timedelta(hours=9))
WEEKDAYS = "월화수목금토일"


def log(message: str) -> None:
    print(message, flush=True)


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------
def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    for _ in range(3):
        try:
            resp = requests.post(url, data=payload, timeout=20)
            data = resp.json()
            if data.get("ok"):
                return True
            log(f"  ! 텔레그램 전송 실패 응답: {data}")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if retry_after:
                time.sleep(int(retry_after) + 1)
                continue
            return False
        except Exception as e:
            log(f"  ! 텔레그램 전송 중 오류: {e}")
            time.sleep(3)
    return False


# ---------------------------------------------------------
# 식단 모으기 (각각 실패해도 나머지는 보낸다)
# ---------------------------------------------------------
def failure_reason(err: Exception) -> str:
    name = type(err).__name__
    if "Timeout" in name or "ConnectionError" in name:
        return "학교 서버 접속 실패"
    if isinstance(err, (dorm_menu.MenuPageError, skku_menu.MenuPageError)):
        return "페이지 구조가 바뀐 것 같아요"
    return name


def read_skku(day: date, category: str, cfg: dict, html_path: str = None, label: str = ""):
    """학교 홈페이지 식당 페이지 하나 읽기. 반환: parse 결과 또는 Exception"""
    try:
        if html_path:
            html = Path(html_path).read_text(encoding="utf-8")
        else:
            url = skku_menu.menu_url(day, category, cfg["conspaceCd"], cfg["srResId"])
            log(f"[{label}] {url}")
            html = skku_menu.fetch(url, log=log)
        parsed = skku_menu.parse(html, day, category)
        for c in parsed["corners"]:
            for k, v in c["sections"].items():
                log(f"  {label} [{c['corner']}] [{k}] {', '.join(v)}")
            extra = "; ".join(f"{a}={b}" if a else b for a, b in c.get("fields", []))
            log(f"    가격: {c.get('price') or '없음'}" + (f"  (기타 정보: {extra[:200]})" if extra else ""))
        if not parsed["corners"]:
            log(f"  {label}: 그날 메뉴 없음")
        return parsed
    except Exception as e:
        log(f"  ! {label} 확인 실패: {type(e).__name__}: {str(e)[:200]}")
        return e


def get_breakfast(day: date, cfg: dict, html_path: str = None):
    """반환: {"time", "items"} 또는 Exception"""
    parsed = read_skku(day, "B", cfg, html_path, label=f"조식 {cfg.get('name', '')}")
    if isinstance(parsed, Exception):
        return parsed
    items = skku_menu.pick_breakfast(parsed, cfg.get("default_price", ""))
    log(f"  -> 보낼 조식: {items or '없음'}")
    return {"time": parsed["time"], "items": items}


def price_value(price: str) -> int:
    digits = "".join(ch for ch in price if ch.isdigit())
    return int(digits) if digits else 0


def get_restaurants(day: date, restaurants: list, meals: list, html_paths: dict):
    """
    반환: [{"cfg": 식당설정, "meals": {"점심": {"time", "items": [(코너, 메뉴, 가격), ...]} 또는 Exception, ...}}, ...]
    html_paths: 점검용 {"공대:L": 파일, ...}
    """
    cat_of = {m["name"]: m.get("skku_category") for m in meals}
    out = []
    for r in restaurants:
        if not r.get("enabled", True):
            continue
        res = {"cfg": r, "meals": {}}
        for meal_name in r.get("meals", []):
            cat = cat_of.get(meal_name)
            if not cat:
                continue
            path = html_paths.get(f"{r['name']}:{cat}")
            parsed = read_skku(day, cat, r, path, label=f"{meal_name} {r['name']}")
            if isinstance(parsed, Exception):
                res["meals"][meal_name] = parsed
            else:
                items = skku_menu.pick_corner_menus(parsed, r.get("corners") or None, r.get("show") == "all")
                min_price = r.get("min_price", 0)
                if min_price:  # 가격이 이보다 싼 건 사이드 메뉴로 보고 뺀다
                    items = [it for it in items if not it[2] or price_value(it[2]) >= min_price]
                if r.get("drop_unpriced"):  # 가격이 안 적힌 메뉴(행사 메뉴 등)도 뺀다
                    items = [it for it in items if it[2]]
                res["meals"][meal_name] = {"time": parsed["time"], "items": items}
                log(f"  -> 보낼 {meal_name} {r['name']}: {items or '없음'}")
        out.append(res)
    return out


def get_dorm(day: date, cfg: dict, html_path: str = None):
    """반환: {"점심": [...], "저녁": [...]} 또는 Exception"""
    try:
        if html_path:
            html = Path(html_path).read_text(encoding="utf-8")
        else:
            log(f"[기숙사] {dorm_menu.menu_url(day)}")
            html = dorm_menu.fetch_menu_html(day, log=log)
        menu = dorm_menu.parse_menu(html, cfg["meals"])
        if menu["date"] and menu["date"] != day.isoformat():
            raise dorm_menu.MenuPageError(f"요청한 날짜({day})와 페이지 날짜({menu['date']})가 다름")
        for meal, corners in menu["meals"].items():
            for c in corners:
                log(f"  {meal} [{c['corner']}] {c['time']} {', '.join(c['items'])}")
        return dorm_menu.pick(menu, cfg["corners"])
    except Exception as e:
        log(f"  ! 기숙사 식단 확인 실패: {type(e).__name__}: {str(e)[:200]}")
        return e


# ---------------------------------------------------------
# 메시지
# ---------------------------------------------------------
def day_label(day: date) -> str:
    return f"{day.month}/{day.day} ({WEEKDAYS[day.weekday()]})"


PAD = "\u3000"  # 한글 한 글자 폭의 빈칸 (식당 이름 폭 맞추기)


def short_price(price: str) -> str:
    """'6,000원' -> '6.0',  '6,250원' -> '6.25'"""
    n = price_value(price)
    if not n:
        return ""
    return f"{n / 1000:.1f}" if n % 100 == 0 else f"{n / 1000:.2f}"


def visual_len(text: str) -> float:
    """폰 화면에서 차지하는 대략적인 폭 (한글 1, 영문·숫자·기호 약 0.55)"""
    return sum(1.0 if ord(ch) >= 0x1100 else 0.55 for ch in text)


def pack_lines(pieces: list, max_width: float) -> list:
    """메뉴들을 쉼표로 잇되, 한 줄이 max_width 를 넘으면 다음 줄로 (줄 끝에 쉼표)"""
    lines, cur = [], ""
    for piece in pieces:
        cand = f"{cur}, {piece}" if cur else piece
        if cur and visual_len(cand) > max_width:
            lines.append(cur + ",")
            cur = piece
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def name_col(label: str, width: int) -> str:
    """식당 이름을 같은 폭으로: '공대' -> '공대\u3000' (width=3)"""
    return html_escape(label) + PAD * max(0, width - len(label))


def part_result(part: dict, breakfast, dorm, restaurants: list, dcfg: dict):
    """
    layout 의 한 칸(식당의 한 끼)을 같은 모양으로:
      Exception                      : 확인 실패
      None                           : 안 보여줌 (optional 식당에 그날 메뉴가 없을 때)
      {"time", "items": [(이름, 가격)]} : 보여줄 메뉴 (items 가 비면 '없음')
    """
    src, meal = part["source"], part["meal"]
    if src == "breakfast":
        if breakfast is None:
            return None
        if isinstance(breakfast, Exception):
            return breakfast
        return {"time": breakfast["time"], "items": breakfast["items"]}
    if src == "dorm":
        if isinstance(dorm, Exception):
            return dorm
        corners = dorm.get(meal, [])
        items = []
        for c in corners:
            text = dorm_menu.main_dish(c["items"]) if dcfg.get("main_only", True) else ", ".join(c["items"])
            items.append(((f"{c['corner']} " if len(dcfg["corners"]) > 1 else "") + text, ""))
        return {"time": corners[0]["time"] if corners else "", "items": items}
    for r in restaurants:
        if r["cfg"]["name"] != src or meal not in r["meals"]:
            continue
        res = r["meals"][meal]
        if isinstance(res, Exception):
            return res
        if not res["items"] and r["cfg"].get("optional"):
            return None
        return {"time": res["time"],
                "items": [((f"{corner} {name}" if corner else name), price) for corner, name, price in res["items"]]}
    return None  # 꺼져 있는 식당


def part_link(part: dict, day: date, config: dict, restaurants: list) -> str:
    """확인 실패한 칸에 붙일 식단표 링크"""
    src, meal = part["source"], part["meal"]
    if src == "dorm":
        return dorm_menu.PAGE_URL
    cat = {"조식": "B", "점심": "L", "저녁": "D"}.get(meal, "L")
    cfg = config.get("breakfast", {}) if src == "breakfast" else next(
        (r["cfg"] for r in restaurants if r["cfg"]["name"] == src), None)
    if not cfg:
        return ""
    return skku_menu.menu_url(day, cat, cfg["conspaceCd"], cfg["srResId"])


def short_time(t: str) -> str:
    """'11:30~13:00' -> '11:30-13',  '08:00~09:00' -> '8-9'"""
    def one(x):
        h, _, m = x.strip().partition(":")
        h = str(int(h)) if h.isdigit() else h
        return h if m in ("", "00") else f"{h}:{m}"
    if "~" not in t:
        return t
    a, b = t.split("~", 1)
    return f"{one(a)}-{one(b)}"


def build_message(day: date, config: dict, breakfast, dorm, restaurants) -> str:
    """
    11/26 (목)

    참치김치찌개, 제육컵밥 1.0
    사골떡만둣국 / 뚝배기나가사키만두전골 매콤불고기비빔밥 고구마치즈돈까스 6.8 / 뼈해장국 7.5
    돼지불고기 / 돈치마요덮밥

    11:30-13 / 11-13:30
    17:30-18:30 / 17-18:20

    끼니마다 한 줄(조식·점심·저녁), 식당은 layout 순서대로 ' / ' 로 구분 (메뉴 없음 '-', 확인 실패 '?').
    아래는 같은 순서의 운영시간 (조식은 빼고, 이웃한 같은 시간은 하나로).
    """
    dcfg = config["dorm_menu"]
    restaurants = restaurants or []
    rcfg_by_name = {r["cfg"]["name"]: r["cfg"] for r in restaurants}

    menu_lines, time_lines, links, seen_urls = [], [], [], set()
    any_menu = False
    for meal in config["layout"]:
        segs, tsegs = [], []
        for row in meal["rows"]:
            part = {"meal": meal["meal"], "source": row["source"]}
            res = part_result(part, breakfast, dorm, restaurants, dcfg)
            if res is None:
                continue  # 메뉴 없는 날 숨기는 식당(나폴리폴리)
            if isinstance(res, Exception):
                segs.append("?")
                tsegs.append("-")
                url = part_link(part, day, config, restaurants)
                if url and url not in seen_urls:  # 같은 식단표(기숙사 점심·저녁)는 링크 하나만
                    seen_urls.add(url)
                    name = row.get("name", row["source"])
                    if row["source"] != "dorm":
                        name = f"{name} {meal['meal']}"
                    links.append(f'<a href="{html_escape(url)}">{html_escape(name)}</a>')
                any_menu = True
                continue
            if not res["items"]:
                segs.append("-")
                tsegs.append("-")
                continue
            any_menu = True
            tsegs.append(short_time(res.get("time", "")) or "-")

            # 기본 가격(공대 6,000원)과 같으면 가격 생략
            default = price_value(rcfg_by_name.get(row["source"], {}).get("default_price", "")) \
                if row["source"] not in ("breakfast", "dorm") else 0
            items = res["items"]
            joiner = row.get("join", " ")
            prices = {short_price(p) for _, p in items}
            if len(items) > 1 and len(prices) == 1 and not default:
                # 가격이 모두 같으면(천원 조식) 가격은 맨 끝에 한 번
                price = prices.pop()
                text = joiner.join(n for n, _ in items) + (f" {price}" if price else "")
            else:
                parts = []
                for n, p in items:
                    sp = "" if (default and price_value(p) == default) else short_price(p)
                    parts.append(f"{n} {sp}".strip())
                text = joiner.join(parts)
            segs.append(text)
        if segs:
            menu_lines.append(html_escape(" / ".join(segs)))
            if meal.get("show_time", True):
                merged = []
                for t in tsegs:  # 이웃한 같은 시간은 하나로 (공대·나폴리 11-13:30)
                    if not (merged and merged[-1] == t and t != "-"):
                        merged.append(t)
                time_lines.append(html_escape(" / ".join(merged)))

    if not any_menu:
        return f"<b>{day_label(day)}</b> 오늘은 식단이 없어요"

    lines = [f"<b>{day_label(day)}</b>", ""] + menu_lines
    if any(t.strip("-/ ") for t in time_lines):
        lines += [""] + time_lines
    if links:
        lines += ["", "링크: " + " · ".join(links)]
    return "\n".join(lines)


# ---------------------------------------------------------
# 메인
# ---------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="이 날짜(YYYY-MM-DD)의 식단 (기본: 오늘)")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램으로 보내지 않고 메시지만 출력")
    parser.add_argument("--html", help="접속하지 않고 저장해둔 기숙사 식단 페이지로 점검")
    parser.add_argument("--skku-html", action="append", default=[], metavar="분류=파일",
                        help="접속하지 않고 저장해둔 학교 식당 페이지로 점검 (B=조식, 식당이름:L / 식당이름:D)")
    parser.add_argument("--force", action="store_true", help="주말에도 실행")
    args = parser.parse_args()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    day = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    if day.weekday() >= 5 and not args.force and not args.date:
        log(f"{day} 은 주말이라 보내지 않습니다. (--force 로 강제 실행)")
        return 0

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not args.dry_run and (not bot_token or not chat_id):
        log("텔레그램 봇 토큰 / chat_id 가 설정되지 않았습니다 (Secrets 확인).")
        return 1

    log(f"아침 브리핑 {datetime.now(KST):%Y-%m-%d %H:%M} KST, 식단 날짜 {day}")

    skku_files = dict(x.split("=", 1) for x in args.skku_html)
    bcfg = config.get("breakfast", {})
    breakfast = get_breakfast(day, bcfg, skku_files.get("B")) if bcfg.get("enabled", True) else None
    dorm = get_dorm(day, config["dorm_menu"], args.html)
    restaurants = get_restaurants(day, config.get("restaurants", []), config["dorm_menu"]["meals"], skku_files)
    message = build_message(day, config, breakfast, dorm, restaurants)
    failed = (isinstance(breakfast, Exception) or isinstance(dorm, Exception)
              or any(isinstance(v, Exception) for r in restaurants for v in r["meals"].values()))

    if args.dry_run:
        log("[dry-run 전송 생략]\n" + message)
        return 0

    ok = send_telegram_message(bot_token, chat_id, message)
    log("텔레그램 전송 " + ("완료" if ok else "실패"))
    if not ok:
        return 1
    return 2 if failed else 0  # 식단 확인 실패 시 Actions 에도 빨간불이 뜨도록


if __name__ == "__main__":
    sys.exit(main())
