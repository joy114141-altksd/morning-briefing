# -*- coding: utf-8 -*-
"""
반도체 뉴스 브리핑 전송
-----------------------
semicon/news/YYYY-MM-DD.json (매일 아침 Claude 예약 작업이 GUIDE.md 기준으로 골라 저장)을
텔레그램 메시지로 만들어 보낸다. GitHub Actions(semicon-news.yml)에서 실행된다.

사용법:
    python send_news.py news/2026-11-28.json --dry-run   # 전송 없이 메시지만 출력 (형식 점검)
    python send_news.py news/2026-11-28.json             # 전송
    python send_news.py --date 2026-11-28                # news/ 폴더의 그날 파일 전송
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
NEWS_DIR = BASE_DIR / "news"

KST = timezone(timedelta(hours=9))
WEEKDAYS = "월화수목금토일"
MAX_LEN = 3900  # 텔레그램 한 메시지 한도 4096자, 여유를 둔다

SECTIONS = [  # 넓은 분야 → 좁은 분야 순서
    ("general", "🌐 반도체 전반"),
    ("device", "🔬 반도체 소자"),
    ("equipment", "🏭 장비·소재·패키징"),
]
BASIS = {"공식": "✅ 공식", "보도": "📰 보도"}


def log(message: str) -> None:
    print(message, flush=True)


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------
# 파일 점검
# ---------------------------------------------------------
def validate(data: dict) -> list:
    """형식 문제 목록 (비어 있으면 정상)"""
    problems = []
    try:
        date.fromisoformat(data.get("date", ""))
    except ValueError:
        problems.append(f"date 형식이 YYYY-MM-DD 가 아님: {data.get('date')!r}")
    keys = {k for k, _ in SECTIONS}
    for s in data.get("sections", []):
        if s.get("key") not in keys:
            problems.append(f"알 수 없는 분야 key: {s.get('key')!r} (가능: {', '.join(sorted(keys))})")
        for i, it in enumerate(s.get("items", []), 1):
            where = f"[{s.get('key')} {i}]"
            for field in ("headline", "summary", "why", "source", "url"):
                if not str(it.get(field, "")).strip():
                    problems.append(f"{where} {field} 가 비어 있음")
            if it.get("url") and not str(it["url"]).startswith(("http://", "https://")):
                problems.append(f"{where} url 이 http(s) 링크가 아님")
            if it.get("basis") not in BASIS:
                problems.append(f"{where} basis 는 '공식' 또는 '보도' 여야 함: {it.get('basis')!r}")
    return problems


# ---------------------------------------------------------
# 메시지
# ---------------------------------------------------------
def day_label(d: date) -> str:
    return f"{d.month}/{d.day} ({WEEKDAYS[d.weekday()]})"


def build_blocks(data: dict) -> list:
    """
    메시지를 이루는 덩어리 목록. 길면 덩어리 경계에서 나눠 보낸다.

    📡 반도체 브리핑 11/28 (토)

    🌐 반도체 전반

    1. 제목
    요약
    → 의미
    Reuters · ✅ 공식
    """
    d = date.fromisoformat(data["date"])
    blocks = [f"<b>📡 반도체 브리핑 {day_label(d)}</b>"]
    by_key = {s.get("key"): s.get("items", []) for s in data.get("sections", [])}
    n = 0
    for key, title in SECTIONS:
        items = by_key.get(key) or []
        if not items:
            continue
        blocks.append(f"<b>{title}</b>")
        for it in items:
            n += 1
            lines = [
                f"<b>{n}. {esc(it['headline'])}</b>",
                esc(it["summary"]),
                f"→ {esc(it['why'])}",
                f'<a href="{esc(it["url"])}">{esc(it["source"])}</a> · {BASIS.get(it.get("basis"), "")}',
            ]
            blocks.append("\n".join(lines))
    if n == 0:
        blocks.append(esc(data.get("note") or "오늘은 크게 볼 뉴스가 없어요"))
    elif data.get("note"):
        blocks.append(f"<i>{esc(data['note'])}</i>")
    return blocks


def split_messages(blocks: list) -> list:
    messages, cur = [], ""
    for b in blocks:
        cand = f"{cur}\n\n{b}" if cur else b
        if cur and len(cand) > MAX_LEN:
            messages.append(cur)
            cur = b
        else:
            cur = cand
    if cur:
        messages.append(cur)
    return messages


# ---------------------------------------------------------
# 텔레그램 (briefing/briefing.py 와 같은 방식)
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
# 메인
# ---------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", help="보낼 뉴스 파일 (JSON)")
    parser.add_argument("--date", help="news/ 폴더의 이 날짜(YYYY-MM-DD) 파일 (기본: 오늘)")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램으로 보내지 않고 메시지만 출력")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
    else:
        day = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
        path = NEWS_DIR / f"{day.isoformat()}.json"
    if not path.exists():
        log(f"뉴스 파일이 없습니다: {path}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(data)
    if problems:
        log("뉴스 파일 형식 문제:\n  - " + "\n  - ".join(problems))
        return 1

    messages = split_messages(build_blocks(data))

    if args.dry_run:
        for i, m in enumerate(messages, 1):
            log(f"[dry-run 메시지 {i}/{len(messages)}, {len(m)}자]\n{m}\n")
        return 0

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        log("텔레그램 봇 토큰 / chat_id 가 설정되지 않았습니다 (Secrets 확인).")
        return 1

    for i, m in enumerate(messages, 1):
        ok = send_telegram_message(bot_token, chat_id, m)
        log(f"메시지 {i}/{len(messages)} 전송 " + ("완료" if ok else "실패"))
        if not ok:
            return 1
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
