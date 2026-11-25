# morning-briefing

평일 아침 7시에 오늘의 기숙사(봉룡학사) 식단을 텔레그램으로 보내줍니다.

## 구조

```
morning-briefing/
├── .github/workflows/morning.yml   ← 아침 브리핑 워크플로
├── requirements.txt
└── briefing/
    ├── briefing.py                 ← 메시지 만들기 + 텔레그램 전송
    ├── dorm_menu.py                ← 봉룡학사 식단 읽기
    ├── config.json                 ← 보낼 끼니·코너 설정
    └── tests/sample_2026-11-25.html ← 점검용 식단 페이지 샘플
```

## 받게 되는 메시지

```
🍚 11/25 (수) 봉룡학사 식단

🌞 점심 11:30~13:00
부대햄김치덮밥

🌙 저녁 17:30~18:30
치킨마요덮밥
```

- 점심·저녁의 **공통** 코너 **메인 메뉴**만 보냅니다. 신관 특식과 Take-out(샐러드·요거트 등)은 뺍니다.
- 공휴일처럼 식단이 비어 있는 날: 🍚 오늘은 기숙사 식단이 없어요
- 학교 서버 접속 실패나 페이지 구조 변경: ⚠️ 기숙사 식단을 확인하지 못했어요 (식단표 링크 포함)

메시지가 아예 안 온 날이 있으면 cron-job.org부터 확인하세요.

### 설정 바꾸기 (`briefing/config.json`)

| 항목 | 설명 |
|---|---|
| `corners` | 보낼 코너 이름. 예: `["공통", "신관"]` (2개 이상이면 메뉴 앞에 `[코너]` 표시) |
| `main_only` | `true`면 메인 메뉴 하나만, `false`면 반찬까지 전부 |
| `meals` | 보낼 끼니. 아침을 넣으려면 `{ "name": "아침", "tab": "foodlist01", "emoji": "🌅" }` 추가 |

## 실행 시각

GitHub 자체 `schedule`은 정시 실행이 보장되지 않아서 쓰지 않습니다.
**cron-job.org**가 **평일(월~금) KST 07:00**에 GitHub API(`workflow_dispatch`)를 호출해서 실행합니다.

cron-job.org 설정:

| 항목 | 값 |
|---|---|
| URL | `https://api.github.com/repos/<계정>/morning-briefing/actions/workflows/morning.yml/dispatches` |
| Method | `POST` |
| Schedule | 매주 월~금, 07:00, 시간대 Asia/Seoul |
| Headers | `Authorization: Bearer <GitHub 토큰>`<br>`Accept: application/vnd.github+json` |
| Body | `{"ref":"main"}` |

GitHub 토큰은 이 저장소에 **Actions: Read and write** 권한이 있어야 합니다
(fine-grained 토큰이면 daily-automation과 같은 토큰에 이 저장소를 추가해도 됩니다).
실행 기록에 `401 Unauthorized`가 뜨면 토큰이 만료된 것이니 새로 발급해서 헤더 값을 바꿔주세요.

## 점검

- Actions → Morning Briefing → Run workflow: `date`를 비우면 오늘 식단, 날짜를 넣으면 그날 식단을 보냅니다.
- 내 컴퓨터에서: `cd briefing && python briefing.py --dry-run` (전송 없이 메시지만 출력)
- 접속 없이 샘플로: `python briefing.py --html tests/sample_2026-11-25.html --date 2026-11-25 --dry-run`

## Secrets 설정

저장소 **Settings → Secrets and variables → Actions**에 아래 2개가 필요합니다 (daily-automation과 같은 값).

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 채팅 ID |

## 사용 시간

한 번에 보통 1분 안팎(학교 서버 재시도 시 최대 약 9분), 한 달 평일 약 22회라 무료 사용량 안에서 충분합니다.
