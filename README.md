# kkotppa-archive

로또 통계분석 인스타 계정 **[@kkot.ppa](https://www.instagram.com/kkot.ppa/)** 게시물 아카이브.

[lotto-lab](https://lotto-lab-ten.vercel.app) 앱의 "꽃빠 아카이브" 탭이 런타임에 fetch 하는 공개 데이터.

## 데이터

- **파일**: [`kkotppa-archive.json`](./kkotppa-archive.json)
- **공개 URL (고정)**: `https://raw.githubusercontent.com/jyjung621/kkotppa-archive/main/kkotppa-archive.json`
- **갱신 주기**: 매일 13·18시 KST (ccdb 영속 스케줄러)
- **출처**: HikerAPI (읽기 전용). 매 실행마다 전체 덮어쓰기.

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
      "thumbnail_url": "https://...",  // 대표 이미지 (CDN 서명 URL, 수시 만료 → 매 갱신)
      "caption": "…",                  // 캡션 전문
      "product_type": "feed"           // feed|carousel|clips|igtv
    }
  ]
}
```

> ⚠️ `thumbnail_url`은 인스타 CDN 서명 URL이라 수 시간~수일 후 만료된다. 매 수집 시 새로
> 채워 덮어쓰므로 앱은 최신 파일을 페치해 대체로 살아있는 URL을 받는다. 만료 시 앱은
> `permalink`로 폴백한다.

## 수집기

`collect.py` — 표준 라이브러리 + curl. `HIKERAPI_KEY`는 환경변수 또는 로컬
`instagram-analysis/.mcp.json`에서 읽으며 **레포에 커밋되지 않는다**.

```bash
python3 collect.py           # 수집 + JSON + git push
python3 collect.py --no-git  # JSON만(테스트)
```
