# -*- coding: utf-8 -*-
"""
아침 브리핑
-----------
평일 아침마다 오늘의 기숙사(봉룡학사) 식단을 텔레그램으로 보낸다.
GitHub Actions 에서 실행된다 (cron-job.org 가 평일 07:00 KST 에 workflow_dispatch 를 호출).

사용법:
    python briefing.py                         # 평소 실행: 오늘 식단 전송
    python briefing.py --dry-run               # 전송 없이 메시지만 출력 (점검용)
    python briefing.py --date 2026-11-25       # 특정 날짜 식단
    python briefing.py --html tests/sample_2026-11-25.html --date 2026-11-25 --dry-run
                                               # 저장해둔 페이지로 점검 (접속 없이)

받게 되는 메시지:
    🍚 식단 (점심·저녁 메인 메뉴)
    🍚 오늘은 기숙사 식단이 없어요   (공휴일 등 식단이 비어 있는 날)
    ⚠️ 식단을 확인하지 못했어요       (학교 서버 접속 실패, 페이지 구조 변경)
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
# 메시지
# ---------------------------------------------------------
def day_label(day: date) -> str:
    return f"{day.month}/{day.day} ({WEEKDAYS[day.weekday()]})"


def build_menu_section(day: date, picked: dict, cfg: dict) -> str:
    """식단 부분. 고른 코너가 하나도 없으면 '식단 없음' 문구."""
    if not any(picked.values()):
        return f"🍚 <b>{day_label(day)}</b> 오늘은 기숙사 식단이 없어요"

    show_corner_name = len(cfg["corners"]) > 1
    lines = [f"🍚 <b>{day_label(day)} {html_escape(cfg['name'])} 식단</b>"]
    for meal in cfg["meals"]:
        corners = picked.get(meal["name"], [])
        lines.append("")
        if not corners:
            lines.append(f"{meal['emoji']} <b>{meal['name']}</b>")
            lines.append("없음")
            continue
        times = corners[0]["time"]
        lines.append(f"{meal['emoji']} <b>{meal['name']}</b> {times}".rstrip())
        for c in corners:
            text = dorm_menu.main_dish(c["items"]) if cfg.get("main_only", True) else ", ".join(c["items"])
            prefix = f"[{html_escape(c['corner'])}] " if show_corner_name else ""
            lines.append(prefix + html_escape(text))
    return "\n".join(lines)


def build_failure_section(day: date, err: Exception) -> str:
    if "Timeout" in type(err).__name__ or "ConnectionError" in type(err).__name__:
        reason = "학교 서버 접속 실패"
    elif isinstance(err, dorm_menu.MenuPageError):
        reason = "페이지 구조가 바뀐 것 같아요"
    else:
        reason = type(err).__name__
    return (
        f"⚠️ <b>{day_label(day)} 기숙사 식단을 확인하지 못했어요</b>\n"
        f"{html_escape(reason)}\n"
        f'<a href="{dorm_menu.PAGE_URL}">식단표 직접 보기</a>'
    )


# ---------------------------------------------------------
# 메인
# ---------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="이 날짜(YYYY-MM-DD)의 식단 (기본: 오늘)")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램으로 보내지 않고 메시지만 출력")
    parser.add_argument("--html", help="접속하지 않고 저장해둔 식단 페이지 파일로 점검")
    parser.add_argument("--force", action="store_true", help="주말에도 실행")
    args = parser.parse_args()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    cfg = config["dorm_menu"]

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

    # --- 기숙사 식단 ---
    try:
        if args.html:
            html = Path(args.html).read_text(encoding="utf-8")
        else:
            log(f"[식단] {dorm_menu.menu_url(day)}")
            html = dorm_menu.fetch_menu_html(day, log=log)
        menu = dorm_menu.parse_menu(html, cfg["meals"])
        if menu["date"] and menu["date"] != day.isoformat():
            raise dorm_menu.MenuPageError(f"요청한 날짜({day})와 페이지 날짜({menu['date']})가 다름")
        for meal, corners in menu["meals"].items():
            for c in corners:
                log(f"  {meal} [{c['corner']}] {c['time']} {', '.join(c['items'])}")
        picked = dorm_menu.pick(menu, cfg["corners"])
        section = build_menu_section(day, picked, cfg)
        failed = False
    except Exception as e:
        log(f"  ! 식단 확인 실패: {type(e).__name__}: {str(e)[:200]}")
        section = build_failure_section(day, e)
        failed = True

    message = section  # 나중에 날씨 등 다른 항목을 붙일 자리

    if args.dry_run:
        log("[dry-run 전송 생략]\n" + message)
        return 0

    ok = send_telegram_message(bot_token, chat_id, message)
    log("텔레그램 전송 " + ("완료" if ok else "실패"))
    if not ok:
        return 1
    return 2 if failed else 0  # 식단 실패 시 Actions 에도 빨간불이 뜨도록


if __name__ == "__main__":
    sys.exit(main())
