# -*- coding: utf-8 -*-
"""
아침 브리핑
-----------
평일 아침마다 오늘의 식단을 텔레그램으로 보낸다.
  - 조식: 행단골식당 천원의 아침밥 (일반식 메인 1개 + 컵밥 1개)
  - 점심·저녁: 기숙사(봉룡학사) 공통 코너 메인 메뉴 + 공대식당(해오름) 코너별 메인 메뉴 전부
GitHub Actions 에서 실행된다 (cron-job.org 가 평일 07:00 KST 에 workflow_dispatch 를 호출).

사용법:
    python briefing.py                         # 평소 실행: 오늘 식단 전송
    python briefing.py --dry-run               # 전송 없이 메시지만 출력 (점검용)
    python briefing.py --date 2026-11-26       # 특정 날짜 식단
    python briefing.py --date 2026-11-25 --dry-run --html tests/sample_2026-11-25.html \\
        --skku-html B=tests/sample_skku_haengdan_B_2026-11-26.html \\
        --skku-html L=tests/sample_skku_haeorum_L_2026-11-26.html --skku-html D=tests/sample_skku_haeorum_D_2026-11-26.html
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
    items = skku_menu.pick_breakfast(parsed)
    log(f"  -> 보낼 조식: {items or '없음'}")
    return {"time": parsed["time"], "items": items}


def get_engineering(day: date, cfg: dict, meals: list, html_paths: dict):
    """반환: {"점심": {"time", "items": [(코너, 메인), ...]} 또는 Exception, "저녁": ...}"""
    out = {}
    for meal in meals:
        cat = meal.get("skku_category")
        if not cat:
            continue
        parsed = read_skku(day, cat, cfg, html_paths.get(cat), label=f"{meal['name']} {cfg.get('name', '')}")
        out[meal["name"]] = parsed if isinstance(parsed, Exception) else {
            "time": parsed["time"], "items": skku_menu.pick_corner_mains(parsed, cfg.get("corners") or None)}
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


def build_message(day: date, config: dict, breakfast, dorm, eng) -> str:
    bcfg, dcfg, ecfg = config.get("breakfast", {}), config["dorm_menu"], config.get("engineering", {})
    use_breakfast = bcfg.get("enabled", True)
    use_eng = ecfg.get("enabled", True)
    eng = eng or {}

    b_empty = (not use_breakfast) or (isinstance(breakfast, dict) and not breakfast["items"])
    d_empty = isinstance(dorm, dict) and not any(dorm.values())
    e_empty = (not use_eng) or all(isinstance(v, dict) and not v["items"] for v in eng.values())
    if b_empty and d_empty and e_empty:
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
            lines += [html_escape(it) for it in breakfast["items"]]

    # 점심·저녁: 기숙사 + 공대
    dname = dcfg.get("name", "기숙사")
    ename = ecfg.get("name", "공대")
    if isinstance(dorm, Exception):
        links.append(f'<a href="{dorm_menu.PAGE_URL}">{html_escape(dname)} 식단표 보기</a>')
    for meal in dcfg["meals"]:
        lines += ["", f"{meal['emoji']} <b>{meal['name']}</b>"]
        # 기숙사
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
        # 공대
        if use_eng and meal["name"] in eng:
            res = eng[meal["name"]]
            if isinstance(res, Exception):
                lines.append(f"<b>{html_escape(ename)}</b> ⚠️ 확인 못함 ({html_escape(failure_reason(res))})")
                url = skku_menu.menu_url(day, meal["skku_category"], ecfg["conspaceCd"], ecfg["srResId"])
                links.append(f'<a href="{html_escape(url)}">{html_escape(ename)} {meal["name"]} 보기</a>')
            elif not res["items"]:
                lines.append(f"<b>{html_escape(ename)}</b> 없음")
            else:
                lines.append(f"<b>{html_escape(ename)}</b> {res['time']}".rstrip())
                for corner, main in res["items"]:
                    lines.append(html_escape(f"{corner} · {main}" if corner else main))

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
                        help="접속하지 않고 저장해둔 학교 식당 페이지로 점검 (B=조식 행단골, L/D=공대 중식/석식)")
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
    bcfg, ecfg = config.get("breakfast", {}), config.get("engineering", {})
    breakfast = get_breakfast(day, bcfg, skku_files.get("B")) if bcfg.get("enabled", True) else None
    dorm = get_dorm(day, config["dorm_menu"], args.html)
    eng = get_engineering(day, ecfg, config["dorm_menu"]["meals"], skku_files) if ecfg.get("enabled", True) else {}
    message = build_message(day, config, breakfast, dorm, eng)
    failed = (isinstance(breakfast, Exception) or isinstance(dorm, Exception)
              or any(isinstance(v, Exception) for v in eng.values()))

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
