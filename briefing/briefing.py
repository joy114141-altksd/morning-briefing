# -*- coding: utf-8 -*-
"""
아침 브리핑
-----------
평일 아침마다 오늘의 식단을 텔레그램으로 보낸다.
  - 조식: 행단골식당 천원의 아침밥 (일반식 메인 1개 + 컵밥 1개)
  - 점심·저녁: 기숙사(봉룡학사) 공통 코너 메인 메뉴 + config.json 의 restaurants (공대식당 코너별 메인, 행단골 2층 나폴리폴리 등)
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
    🍚 오늘의 식단 (조식·점심·저녁)
    🍚 오늘은 식단이 없어요            (공휴일 등 모든 식단이 비어 있는 날)
    ⚠️ 확인 못함 + 식단표 링크          (학교 서버 접속 실패, 페이지 구조 변경)
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


def with_price(name: str, price: str) -> str:
    return f"{name} {price}" if price else name


def build_message(day: date, config: dict, breakfast, dorm, restaurants) -> str:
    bcfg, dcfg = config.get("breakfast", {}), config["dorm_menu"]
    use_breakfast = bcfg.get("enabled", True)
    restaurants = restaurants or []

    b_empty = (not use_breakfast) or (isinstance(breakfast, dict) and not breakfast["items"])
    d_empty = isinstance(dorm, dict) and not any(dorm.values())
    r_empty = all(isinstance(v, dict) and not v["items"] for r in restaurants for v in r["meals"].values())
    if b_empty and d_empty and r_empty:
        return f"🍚 <b>{day_label(day)}</b> 오늘은 식단이 없어요"

    lines = [f"🍚 <b>{day_label(day)} 오늘의 식단</b>"]
    links = []

    # 조식
    if use_breakfast:
        lines.append("")
        head = f"{bcfg.get('emoji', '🌅')} <b>조식</b>"
        if isinstance(breakfast, Exception):
            lines += [head, f"⚠️ 확인 못함 ({html_escape(failure_reason(breakfast))})"]
            url = skku_menu.menu_url(day, "B", bcfg["conspaceCd"], bcfg["srResId"])
            links.append(f'<a href="{html_escape(url)}">{html_escape(bcfg.get("name", "행단골"))} 조식 보기</a>')
        elif not breakfast["items"]:
            lines += [head, "없음"]
        else:
            lines.append(f"{head} {breakfast['time']} · {html_escape(bcfg.get('name', '행단골'))}".replace("  ", " "))
            lines += [html_escape(with_price(name, price)) for name, price in breakfast["items"]]

    # 점심·저녁: 기숙사 + 학교 식당들
    dname = dcfg.get("name", "기숙사")
    if isinstance(dorm, Exception):
        links.append(f'<a href="{dorm_menu.PAGE_URL}">{html_escape(dname)} 식단표 보기</a>')
    for meal in dcfg["meals"]:
        lines += ["", f"{meal['emoji']} <b>{meal['name']}</b>"]
        # 기숙사 (사이트에 공통 코너 가격이 없어 가격 없음)
        if isinstance(dorm, Exception):
            lines.append(f"<b>{html_escape(dname)}</b> ⚠️ 확인 못함 ({html_escape(failure_reason(dorm))})")
        else:
            corners = dorm.get(meal["name"], [])
            if not corners:
                lines.append(f"<b>{html_escape(dname)}</b> 없음")
            else:
                lines.append(f"<b>{html_escape(dname)}</b> {corners[0]['time']}".rstrip())
                for c in corners:
                    text = dorm_menu.main_dish(c["items"]) if dcfg.get("main_only", True) else ", ".join(c["items"])
                    prefix = f"{c['corner']} · " if len(dcfg["corners"]) > 1 else ""
                    lines.append(html_escape(prefix + text))
        # 학교 식당들
        for r in restaurants:
            if meal["name"] not in r["meals"]:
                continue
            rcfg, res, rname = r["cfg"], r["meals"][meal["name"]], r["cfg"]["name"]
            if isinstance(res, Exception):
                lines.append(f"<b>{html_escape(rname)}</b> ⚠️ 확인 못함 ({html_escape(failure_reason(res))})")
                url = skku_menu.menu_url(day, meal["skku_category"], rcfg["conspaceCd"], rcfg["srResId"])
                links.append(f'<a href="{html_escape(url)}">{html_escape(rname)} {meal["name"]} 보기</a>')
            elif not res["items"]:
                if not rcfg.get("optional"):  # optional: 메뉴가 있을 때만 표시
                    lines.append(f"<b>{html_escape(rname)}</b> 없음")
            else:
                lines.append(f"<b>{html_escape(rname)}</b> {res['time']}".rstrip())
                for corner, name, price in res["items"]:
                    text = with_price(name, price)
                    lines.append(html_escape(f"{corner} · {text}" if corner else text))

    if links:
        lines += ["", " · ".join(links)]
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
