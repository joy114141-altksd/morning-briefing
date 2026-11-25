# -*- coding: utf-8 -*-
"""학교 서버 접속 (재시도 포함). 식단 모듈들이 같이 쓴다."""

import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

CONNECT_TIMEOUT = 15   # 학교 서버는 연결이 안 될 때 응답 없이 매달리므로 짧게 끊고 재시도
READ_TIMEOUT = 30


def fetch_html(url: str, total_seconds: float, log=print) -> str:
    """접속이 될 때까지 total_seconds 동안 재시도한다."""
    give_up_at = time.monotonic() + total_seconds
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
