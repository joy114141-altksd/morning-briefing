# morning-briefing

평일 아침 7시에 오늘의 식단을 텔레그램으로 보내줍니다.

- 조식: 행단골식당 천원의 아침밥 (일반식 메인 1개 + 컵밥 1개)
- 점심·저녁: 기숙사(봉룡학사) 공통 코너 메인 메뉴 + 공대식당(해오름) 코너별 메인 메뉴 전부 + 행단골 2층 나폴리폴리(메뉴 있는 날만)
- 공대식당의 4,500원 미만 메뉴(치킨텐더 등 사이드)와 가격이 없는 행사 메뉴(버거 등)는 뺍니다
- 학교 홈페이지에 가격이 있으면 메뉴 옆에 가격 (기숙사 공통 코너는 사이트에 가격이 없음)
- 비나 눈이 오는 날은 날짜 옆에 ☔70% / ❄️ (맑은 날은 표시 없음)

## 구조

```
morning-briefing/
├── .github/workflows/morning.yml   ← 아침 브리핑 워크플로
├── requirements.txt
└── briefing/
    ├── briefing.py                 ← 메시지 만들기 + 텔레그램 전송
    ├── skku_menu.py                ← 학교 홈페이지 식당 메뉴·가격 읽기 (행단골·해오름)
    ├── dorm_menu.py                ← 봉룡학사 식단 읽기
    ├── web.py                      ← 학교 서버 접속 (재시도)
    ├── weather.py                  ← 오늘 비·눈 예보 (Open-Meteo, API 키 필요 없음)
    ├── config.json                 ← 보낼 끼니·코너 설정
    └── tests/                      ← 점검용 식단 페이지 샘플
```

## 받게 되는 메시지

```
11/26 (목) ☔70%

참치김치찌개, 제육컵밥 1.0
사골떡만둣국 / 뚝배기나가사키만두전골 매콤불고기비빔밥 고구마치즈돈까스 6.8 / 뼈해장국 7.5
돼지불고기 / 돈치마요덮밥

11:30-13 / 11-13:30
17:30-18:30 / 17-18:20
```

- 날짜 옆 `☔70%`: 학교(자연과학캠퍼스) 07~21시 중 강수확률이 50% 이상이거나 비가 1mm 이상 올 때, 숫자는 그중 최고 강수확률. 눈이 오면 `❄️`. 맑은 날이나 날씨를 못 받아온 날은 아무것도 안 붙습니다.
- 위 세 줄은 조식·점심·저녁. 점심·저녁은 **기숙사 / 공대 / 나폴리** 순서로 ` / ` 구분 (나폴리는 메뉴 있는 날만).
- 아래 두 줄은 점심·저녁 운영시간, 같은 순서. 이웃한 식당 시간이 같으면 하나로 씁니다 (공대·나폴리 `11-13:30`).
- 가격은 천 원 단위(6.8 = 6,800원). 공대 기본 가격 6,000원은 생략.
- 메뉴가 없으면 `-`, 확인 못 하면 `?` (맨 아래에 식단표 링크).
- 조식은 천원의 아침밥 **일반식의 첫 요리**(밥·김치 제외)와 **컵밥**, 2개를 보냅니다.
- 점심·저녁은 기숙사 **공통** 코너 **메인 메뉴**만 보냅니다. 신관 특식과 Take-out(샐러드·요거트 등)은 뺍니다.
- 공대식당은 **코너마다 메인 메뉴 1개씩 전부** 보냅니다 (골라 먹을 수 있게).
- 공휴일처럼 식단이 모두 비어 있는 날: 오늘은 식단이 없어요
- 학교 서버 접속 실패나 페이지 구조 변경: 그 자리에 `?` + 맨 아래 식단표 링크 (나머지는 정상 전송)

메시지가 아예 안 온 날이 있으면 cron-job.org부터 확인하세요.

### 설정 바꾸기 (`briefing/config.json`)

| 항목 | 설명 |
|---|---|
| `layout` | 끼니 순서와 끼니마다 들어갈 식당 순서. 끼니마다 `meal`과 `rows`(`name`=식당 이름, `source`=breakfast / dorm / restaurants 의 name, `join`=한 식당 메뉴 사이 구분, 기본 빈칸), `show_time`=false 면 그 끼니 운영시간 줄 생략 |
| `breakfast.enabled` | `false`면 조식을 빼고 보냅니다 |
| `breakfast.default_price` | 조식 페이지에 가격이 없을 때 붙일 가격 (천원의 아침밥 `1,000원`) |
| `restaurants` | 점심·저녁에 함께 볼 학교 식당 목록. 식당마다 아래 항목 |
| ↳ `enabled` | `false`면 그 식당을 뺍니다 |
| ↳ `meals` | `["점심", "저녁"]` 중 받을 끼니 |
| ↳ `corners` | 비워두면 코너 전부, `["나폴리"]`처럼 적으면 이름에 그 글자가 들어간 코너만 |
| ↳ `show` | `mains`: 코너마다 메인 1개, `all`: 코너의 메뉴 전부 |
| ↳ `optional` | `true`면 메뉴가 없는 날 그 식당 줄을 아예 안 보여줍니다 |
| ↳ `min_price` | 이 가격보다 싼 메뉴는 사이드로 보고 뺍니다 (공대 `4500`) |
| ↳ `default_price` | 이 가격인 메뉴는 가격을 생략합니다 (공대 `6,000원`) |
| ↳ `drop_unpriced` | `true`면 가격이 안 적힌 메뉴(버거 데이 등)도 뺍니다 |
| `dorm_menu.corners` | 보낼 기숙사 코너 이름. 예: `["공통", "신관"]` (2개 이상이면 메뉴 앞에 `[코너]` 표시) |
| `main_only` | `true`면 메인 메뉴 하나만, `false`면 반찬까지 전부 |
| `weather` | 날씨 표시. `enabled`=false 면 끄기, `hours`=볼 시간대(KST), `rain_probability`=☔ 붙일 강수확률(%), `rain_mm`=이만큼 비가 오면 확률과 상관없이 ☔, `snow_cm`=❄️ 붙일 적설(cm), `latitude`·`longitude`=위치 |
| `meals` | 보낼 끼니. 기숙사 아침을 넣으려면 `{ "name": "아침", "tab": "foodlist01" }` 추가 |

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
- 접속 없이 샘플로 (`briefing` 폴더에서):
  `python briefing.py --date 2026-11-25 --dry-run --html tests/sample_2026-11-25.html --skku-html B=tests/sample_skku_haengdan_B_2026-11-26.html --skku-html 공대:L=tests/sample_skku_haeorum_L_2026-11-26.html --skku-html 공대:D=tests/sample_skku_haeorum_D_2026-11-26.html --skku-html 나폴리폴리:L=tests/sample_skku_haengdan_L_2026-11-26.html --weather-json tests/sample_weather_rain.json`
  (해오름·행단골 중식 샘플과 날씨 샘플(`sample_weather_*.json`)은 실제 저장본이 아니라 같은 틀로 만든 가상 데이터입니다)

## Secrets 설정

저장소 **Settings → Secrets and variables → Actions**에 아래 2개가 필요합니다 (daily-automation과 같은 값).

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 채팅 ID |

## 반도체 뉴스 브리핑 (`semicon/`)

매일 아침 반도체 뉴스 중 믿을 만하고 파급력 있는 것만 골라 같은 텔레그램 봇으로 보냅니다.
분야는 넓은 것 → 좁은 것 순서: 🌐 반도체 전반 → 🔬 반도체 소자 → 🏭 장비·소재·패키징.

- 뉴스 고르기: Claude 예약 작업이 매일 아침 [`semicon/GUIDE.md`](semicon/GUIDE.md) 기준으로 골라 `semicon/news/YYYY-MM-DD.json`을 main에 푸시
- 보내기: 그 푸시를 받아 `Semicon News` 워크플로(`.github/workflows/semicon-news.yml`)가 `semicon/send_news.py`로 전송 (Secrets는 위 2개 그대로 사용, cron-job.org 설정 필요 없음)
- 기준 바꾸기: `semicon/GUIDE.md`만 고치면 다음 날부터 반영
- 다시 보내기: Actions → Semicon News → Run workflow (`date` 비우면 오늘)
- 점검: `cd semicon && python send_news.py news/2026-11-28.json --dry-run`

## 사용 시간

한 번에 보통 1분 안팎(학교 서버 재시도 시 최대 약 20분), 한 달 평일 약 22회라 무료 사용량 안에서 충분합니다.
