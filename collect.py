#!/usr/bin/env python3
"""꽃빠(@kkot.ppa) 로또 게시물 아카이브 수집기 — Bright Data Web Scraper API.

lotto-lab 앱이 런타임 fetch 하는 계약 스키마 JSON(kkotppa-archive.json)을 만들고
이 스크립트가 있는 git 레포에 커밋·푸시한다.

**증분 수집** (2026-10-26 개편 — Posts discover_new 가 10/20부터 0건 반환하는 Bright Data 장애 우회):
  1) Profiles 데이터셋 1건(1 크레딧) → 최근 12건 목록(url·caption·image_url·content_type·날짜)
  2) 목록 중 아카이브에 없는 shortcode 만 Posts 데이터셋에 URL 로 상세 조회(신규 1건 = 1 크레딧)
     — 프로필 목록의 날짜는 자정으로 잘려 와서 taken_at 정밀값은 상세 조회에서 받는다
  3) 기존 게시물은 프로필 목록의 image_url 로 thumbnail_url 만 갱신(추가 비용 0)
  겹치는 게시물은 프로필 1건 안에 들어 있어 따로 돈이 들지 않는다. 신규가 없으면 실행당 1 크레딧.
  - N번째 이하   → 손대지 않음 (caption·taken_at·permalink는 불변)
왜: 앱이 쓰는 필드 중 만료되는 건 인스타 CDN 썸네일뿐이고(실측 ~108시간), 그
썸네일은 화면 상위 5개만 표시된다. 12건이면 표시분을 모두 덮는다.

**과금**: Bright Data는 1 크레딧 = 1 레코드. 무료 등급 월 5,000 크레딧(매월 1일 갱신).
개편 후: (1 + 신규 건수) × 하루 2회 ≈ 월 60~90 크레딧. (구 discover 방식은 12건 고정 과금 → 월 720)

**흐름**: 동기 /scrape 는 무료 계정에서 "Customer is not active"로 막히므로
비동기 3단계를 쓴다 — trigger → progress 폴링 → snapshot 다운로드.

- 표준 라이브러리 + curl만 사용.
- BRIGHTDATA_TOKEN: 환경변수 우선, 없으면 ~/.brightdata-token 파일에서 읽음.
  (공개 레포이므로 토큰은 절대 커밋하지 않는다.)

Usage:
    python3 collect.py            # 증분 수집(12건) + JSON + git push
    python3 collect.py --full     # 과거 백필 (--n 으로 건수 지정, 기본 150)
    python3 collect.py --n 30     # 조회 건수 지정
    python3 collect.py --no-git   # JSON만 쓰고 git 작업 생략(테스트용)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ACCOUNT = "kkot.ppa"
PROFILE_URL = f"https://www.instagram.com/{ACCOUNT}/"
ACCOUNT_PK = "76382932745"      # 인스타 내부 user id (계약 스키마 유지용)
DATASET_ID = "gd_lk5ns7kz21pck8jpis"   # Bright Data "Instagram - Posts"
PROFILE_DATASET_ID = "gd_l1vikfch901nx3by4"   # Bright Data "Instagram - Profiles"
API = "https://api.brightdata.com/datasets/v3"

N_INCREMENTAL = 12          # 증분 실행 시 조회 건수 (= 소모 크레딧)
N_FULL = 150                # --full 백필 시 조회 건수
POLL_INTERVAL = 10          # 진행상태 폴링 간격(초)
POLL_TIMEOUT = 600          # 폴링 최대 대기(초). 실측 3건 33초.

THUMB_KEEP = 12             # 썸네일 URL을 보존할 상위 게시물 수
# 인스타 CDN 썸네일은 서명 URL이라 ~108시간 후 만료된다. 매 실행 상위 N건만
# 갱신되므로 그 이하의 URL은 반드시 죽는다. 죽은 URL을 계속 싣고 다니면 아카이브가
# 누적될수록 payload만 부푸므로(URL 1건당 ~564자) 쓰기 시점에 상위 THUMB_KEEP개만
# 남긴다. 앱은 썸네일이 없으면 대체 UI로 폴백한다.

HERE = Path(__file__).resolve().parent
OUT = HERE / "kkotppa-archive.json"
TOKEN_PATH = Path.home() / ".brightdata-token"

# Bright Data content_type → 계약 스키마 product_type
_TYPE_MAP = {"image": "feed", "carousel": "carousel", "video": "clips", "reel": "clips"}


def load_token() -> str:
    tok = os.environ.get("BRIGHTDATA_TOKEN")
    if tok:
        return tok.strip()
    try:
        return TOKEN_PATH.read_text().strip()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"BRIGHTDATA_TOKEN을 찾을 수 없음: env 미설정 & {TOKEN_PATH} 읽기 실패 ({e})")


def curl(args: list[str], tries: int = 3) -> tuple[str, str]:
    """curl 실행 → (body, http_code). 5xx/429는 백오프 재시도."""
    for t in range(tries):
        p = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", "--max-time", "120", *args],
            capture_output=True, text=True,
        )
        body, _, code = p.stdout.rpartition("\n")
        if code.startswith("2"):
            return body, code
        if (code.startswith("5") or code == "429") and t < tries - 1:
            time.sleep(2.0 * (t + 1))
            continue
        return body, code
    return "", "000"


def api_get(path: str, token: str) -> tuple[str, str]:
    return curl([f"{API}/{path}", "-H", f"Authorization: Bearer {token}"])


def trigger(token: str, n_posts: int) -> str:
    payload = json.dumps([{"url": PROFILE_URL, "num_of_posts": n_posts}])
    body, code = curl([
        "-X", "POST",
        f"{API}/trigger?dataset_id={DATASET_ID}&type=discover_new&discover_by=url",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
        "-d", payload,
    ])
    if code != "200":
        sys.exit(f"trigger 실패 HTTP {code}: {body[:300]}")
    sid = json.loads(body).get("snapshot_id")
    if not sid:
        sys.exit(f"trigger 응답에 snapshot_id 없음: {body[:300]}")
    print(f"trigger ok — snapshot {sid} (요청 {n_posts}건)")
    return sid


def trigger_inputs(token: str, dataset_id: str, inputs: list[dict], label: str) -> str:
    """discover 없이 URL 입력 그대로 수집(프로필 1건 / 게시물 URL n건)."""
    body, code = curl([
        "-X", "POST",
        f"{API}/trigger?dataset_id={dataset_id}&include_errors=true",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(inputs),
    ])
    if code != "200":
        sys.exit(f"{label} trigger 실패 HTTP {code}: {body[:300]}")
    sid = json.loads(body).get("snapshot_id")
    if not sid:
        sys.exit(f"{label} trigger 응답에 snapshot_id 없음: {body[:300]}")
    print(f"{label} trigger ok — snapshot {sid} (입력 {len(inputs)}건)")
    return sid


def collect_incremental(token: str, existing: list[dict]) -> list[dict]:
    """프로필 목록 → 신규만 URL 상세 조회. 반환은 map_post 입력과 같은 원시 dict 목록."""
    sid = trigger_inputs(token, PROFILE_DATASET_ID, [{"url": PROFILE_URL}], "profile")
    wait_ready(token, sid)
    prof = download(token, sid)
    listed = (prof[0].get("posts") or []) if prof and isinstance(prof[0], dict) else []
    if not listed:
        sys.exit("프로필 응답에 게시물 목록 없음 — 쓰기 중단(기존 파일 보존)")
    known = {p["code"] for p in existing}
    by_code = {}
    for it in listed:
        code = (it.get("url") or "").rstrip("/").split("/")[-1]
        if code:
            by_code[code] = it
    new_codes = [c for c in by_code if c not in known]
    print(f"프로필 목록 {len(by_code)}건 — 신규 {len(new_codes)}건")
    if len(new_codes) == len(by_code):
        print("경고: 목록 전부가 신규 — 실행 간격 사이에 12건 넘게 올라왔을 수 있음(누락 가능)", file=sys.stderr)
    raw: list[dict] = []
    if new_codes:
        sid2 = trigger_inputs(token, DATASET_ID,
                              [{"url": f"https://www.instagram.com/p/{c}/"} for c in new_codes], "posts")
        wait_ready(token, sid2)
        raw = [r for r in download(token, sid2) if isinstance(r, dict) and r.get("shortcode")]
    got = {r["shortcode"] for r in raw}
    # 기존 게시물: 썸네일 갱신용 최소 레코드(merge 가 thumbnail_url 만 반영)
    old_by = {p["code"]: p for p in existing}
    for code, it in by_code.items():
        if code in got or code not in old_by:
            continue
        raw.append({"shortcode": code, "date_posted": old_by[code]["taken_at"],
                    "url": old_by[code].get("permalink"), "thumbnail": it.get("image_url")})
    return raw


def wait_ready(token: str, sid: str) -> None:
    waited = 0
    while waited < POLL_TIMEOUT:
        body, code = api_get(f"progress/{sid}", token)
        if code != "200":
            sys.exit(f"progress 실패 HTTP {code}: {body[:200]}")
        st = json.loads(body)
        status = st.get("status")
        if status == "ready":
            print(f"수집 완료 — records={st.get('records')} errors={st.get('errors')}")
            return
        if status in ("failed", "canceled"):
            sys.exit(f"수집 실패: {body[:300]}")
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
    sys.exit(f"폴링 타임아웃({POLL_TIMEOUT}s) — snapshot {sid}는 나중에 수동 확인 가능")


def download(token: str, sid: str) -> list[dict]:
    body, code = api_get(f"snapshot/{sid}?format=json", token)
    if code != "200":
        sys.exit(f"snapshot 다운로드 실패 HTTP {code}: {body[:200]}")
    data = json.loads(body)
    return data if isinstance(data, list) else []


def norm_ts(raw: str | None) -> str | None:
    """'2026-07-24T11:35:47.000Z' → '2026-07-24T11:35:47Z' (계약 스키마 포맷)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return raw[:19] + "Z" if len(raw) >= 19 else None


def map_post(r: dict) -> dict | None:
    code = r.get("shortcode")
    taken = norm_ts(r.get("date_posted"))
    if not code or not taken:
        return None
    post = {
        "code": code,
        "taken_at": taken,
        "permalink": r.get("url") or f"https://www.instagram.com/p/{code}/",
    }
    thumb = r.get("thumbnail")
    if not thumb:
        photos = r.get("photos")
        if isinstance(photos, list) and photos:
            thumb = photos[0]
    if thumb:
        post["thumbnail_url"] = thumb
    cap = r.get("description")
    if cap:
        post["caption"] = cap
    ct = (r.get("content_type") or "").lower()
    if ct:
        post["product_type"] = _TYPE_MAP.get(ct, ct)
    return post


def load_existing() -> list[dict]:
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text()).get("posts", [])
    except Exception as e:  # noqa: BLE001
        print(f"경고: 기존 아카이브 읽기 실패({e}) — 빈 상태로 시작", file=sys.stderr)
        return []


def merge(existing: list[dict], fresh: list[dict]) -> tuple[list[dict], int, int]:
    """신규는 통째로 추가, 기존은 thumbnail_url만 갱신.

    caption·taken_at·permalink는 불변이라 덮어쓰지 않는다.
    """
    by_code = {p["code"]: dict(p) for p in existing}
    added = refreshed = 0
    for p in fresh:
        old = by_code.get(p["code"])
        if old is None:
            by_code[p["code"]] = p
            added += 1
            continue
        thumb = p.get("thumbnail_url")
        if thumb and thumb != old.get("thumbnail_url"):
            old["thumbnail_url"] = thumb
            refreshed += 1
    ordered = sorted(by_code.values(), key=lambda p: p["taken_at"], reverse=True)
    return ordered, added, refreshed


def strip_dead_thumbs(posts: list[dict]) -> int:
    dropped = 0
    for p in posts[THUMB_KEEP:]:
        if p.pop("thumbnail_url", None):
            dropped += 1
    return dropped


def git(*args: str):
    subprocess.run(["git", "-C", str(HERE), *args], check=True)


def main() -> None:
    argv = sys.argv[1:]
    no_git = "--no-git" in argv
    full = "--full" in argv
    n = N_FULL if full else N_INCREMENTAL
    if "--n" in argv:
        try:
            n = int(argv[argv.index("--n") + 1])
        except (IndexError, ValueError):
            sys.exit("--n 뒤에 정수를 지정하라")

    token = load_token()
    if full or "--n" in argv:
        # 과거 백필은 discover 경로 유지(2026-10-20~ Bright Data 장애로 0건일 수 있음)
        sid = trigger(token, n)
        wait_ready(token, sid)
        raw = download(token, sid)
    else:
        raw = collect_incremental(token, load_existing())
    fresh = [m for m in (map_post(r) for r in raw) if m]
    print(f"매핑: {len(raw)}건 수신 → {len(fresh)}건 유효")
    if not fresh:
        sys.exit("유효 게시물 0건 — 쓰기 중단(기존 파일 보존)")

    posts, added, refreshed = merge(load_existing(), fresh)
    dropped = strip_dead_thumbs(posts)
    doc = {
        "account": ACCOUNT,
        "account_pk": ACCOUNT_PK,
        "source": "brightdata",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posts": posts,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(
        f"wrote {OUT} — 총 {len(posts)}건 "
        f"(신규 +{added} / 썸네일갱신 {refreshed} / 만료썸네일제거 {dropped}), "
        f"최신 {posts[0]['taken_at']}"
    )
    if no_git:
        return
    git("add", "kkotppa-archive.json")
    if subprocess.run(["git", "-C", str(HERE), "diff", "--cached", "--quiet"]).returncode == 0:
        print("변경 없음 — 커밋 생략")
        return
    git("commit", "-m", f"data: kkotppa archive refresh {doc['updated']} (총 {len(posts)}건, 신규 +{added})")
    git("push", "origin", "main")
    print("pushed to origin/main")


if __name__ == "__main__":
    main()
