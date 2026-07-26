# kkotppa-archive

로또 통계분석 인스타 계정 **[@kkot.ppa](https://www.instagram.com/kkot.ppa/)** 게시물 아카이브.

[lotto-lab](https://lotto-lab-ten.vercel.app) 앱의 "꽃빠 아카이브" 탭이 런타임에 fetch 하는 공개 데이터.

## 데이터

- **파일**: [`kkotppa-archive.json`](./kkotppa-archive.json)
- **공개 URL (고정)**: `https://raw.githubusercontent.com/jyjung621/kkotppa-archive/main/kkotppa-archive.json`
- **갱신 주기**: 매일 13·18시 KST (ccdb 영속 스케줄러)
- **출처**: Bright Data Web Scraper API (Instagram - Posts). **증분 수집** — 실행당
  최신 12건만 조회해 신규 게시물을 누적하고, 기존 게시물은 썸네일 URL만 갱신한다.
- **비용**: 1 크레딧 = 1 레코드, 무료 등급 **월 5,000 크레딧**(매월 1일 갱신).
  12건 × 하루 2회 × 30일 = 월 720 크레딧 ≈ 한도의 **14%** → 상시 무료.

### 스키마

```jsonc
{
  "account": "kkot.ppa",
  "account_pk": "76382932745",
  "source": "hikerapi",
  "updated": "2026-07-22T23:27:56Z",   // 마지막 갱신(UTC)
  "posts": [                            // taken_at 내림차순(최신 먼저), 최대 90개
    {
      "code": "DbDBdo0kvR-",           // 인스타 shortcode (필수)
      "taken_at": "2026-07-21T07:52:51Z", // 게시 시각 UTC (필수)
      "permalink": "https://www.instagram.com/p/DbDBdo0kvR-/", // 필수
      "thumbnail_url": "https://...",  // 대표 이미지 — 상위 12개에만 존재 (아래 주의)
      "caption": "…",                  // 캡션 전문
      "product_type": "feed"           // feed|carousel|clips|igtv
    }
  ]
}
```

> ⚠️ **`thumbnail_url`은 상위 12개 게시물에만 보장된다.** 인스타 CDN 서명 URL은 실측
> ~108시간 후 만료되는데, 증분 수집은 매 실행 최신 12개만 갱신하므로 그 이하는 반드시
> 죽는다. 죽은 URL을 계속 싣고 다니면 아카이브가 누적될수록 payload만 부푸는(URL 1건당
> ~564자) 문제가 있어 쓰기 시점에 제거한다. 소비자는 `thumbnail_url` 부재를 정상 상태로
> 다루고 `permalink`로 폴백할 것. (`collect.py`의 `THUMB_KEEP` 상수로 조정 가능)

`posts`는 상한 없이 **누적**된다 — 오래된 게시물도 지우지 않으므로 적중이력 검증에 쓸
표본이 시간이 갈수록 늘어난다. `caption`·`taken_at`·`permalink`는 불변이라 한 번
수집하면 다시 조회하지 않는다.

## 수집기

`collect.py` — 표준 라이브러리 + curl. 토큰은 `BRIGHTDATA_TOKEN` 환경변수 또는
`~/.brightdata-token`(레포 밖, 0600)에서 읽으며 **레포에 커밋되지 않는다**.

```bash
python3 collect.py           # 증분 수집(12건) + JSON + git push
python3 collect.py --full    # 과거 백필(기본 150건)
python3 collect.py --n 30    # 조회 건수 지정
python3 collect.py --no-git  # JSON만(테스트)
```

**증분 동작**: 최신 12건만 조회해서
- 신규 게시물 → 전체 필드 추가(누적)
- 기존 게시물 → `thumbnail_url`만 갱신 (같은 응답에 포함, 추가 비용 0)
- 13번째 이하 → 손대지 않음

앱이 표시하는 썸네일은 상위 5개뿐이라 12건이면 표시분을 전부 덮는다.

**호출 흐름** (비동기 3단계): `POST /datasets/v3/trigger` → `GET /progress/{id}`
폴링 → `GET /snapshot/{id}`. 동기 `/scrape`는 무료 계정에서 `Customer is not active`로
막히므로 쓰지 않는다. 실측 소요: 12건 약 40~60초.

**필드 매핑**: `shortcode`→`code`, `url`→`permalink`, `description`→`caption`,
`date_posted`→`taken_at`(밀리초 제거), `thumbnail`→`thumbnail_url`,
`content_type`→`product_type`(Image→feed, Carousel→carousel, Video/Reel→clips).

### 폴백

`collect_hikerapi.py` — 이전 HikerAPI 구현. Bright Data 무료 등급 정책이 바뀌면
이쪽으로 되돌릴 수 있다(단 HikerAPI는 잔액 충전 필요). 출력 계약 스키마는 동일.
