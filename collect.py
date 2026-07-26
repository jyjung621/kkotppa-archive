#!/usr/bin/env python3
"""꽃빠(@kkot.ppa) 로또 게시물 아카이브 수집기 (증분 방식).

HikerAPI로 최신 게시물을 긁어 lotto-lab 앱이 런타임 fetch 하는 계약 스키마
JSON(kkotppa-archive.json)을 생성하고, 이 스크립트가 있는 git 레포에 커밋·푸시한다.

**증분 수집** — 기본 실행은 1페이지(최신 12개)만 조회한다:
  - 신규 게시물  → 전체 필드 추가 (아카이브에 영구 누적)
  - 기존 게시물  → thumbnail_url만 갱신 (같은 응답에 들어있어 추가 요청 0건)
  - 13번째 이하  → 손대지 않음 (caption·taken_at·permalink는 불변)
왜: 앱이 쓰는 필드 중 만료되는 건 썸네일뿐이고(실측 ~108시간), 그 썸네일은
화면 상위 5개만 표시된다. 1페이지(12개)면 표시분을 모두 덮으므로 전체 재수집은
순수 낭비였다(실행당 8요청 → 1요청).

- 표준 라이브러리 + curl만 사용 (python urllib은 HikerAPI가 UA 차단 → 403).
- HIKERAPI_KEY: 환경변수 우선, 없으면 instagram-analysis/.mcp.json에서 읽음.

Usage:
    python3 collect.py            # 증분 수집(1요청) + JSON + git push
    python3 collect.py --full     # 전체 백필(페이지네이션 끝까지) — 최초/과거보강용
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

PK = "76382932745"          # @kkot.ppa (인스타꽃빠의 로또 통계분석)
ACCOUNT = "kkot.ppa"
PAGES_MAX = 20              # --full 백필 안전 상한 (12개/페이지)
THUMB_KEEP = 12             # 썸네일 URL을 보존할 상위 게시물 수 (§ 아래 주석)
# 인스타 CDN 썸네일은 서명 URL이라 ~108시간 후 만료된다. 매 실행 1페이지(12개)만
# 갱신되므로 13번째 이하의 URL은 반드시 죽는다. 죽은 URL을 계속 싣고 다니면
# 아카이브가 누적될수록 payload만 부풀어(URL 1건당 ~564자) 앱 로딩이 무거워지므로
# 쓰기 시점에 상위 THUMB_KEEP개만 남긴다. 앱은 썸네일이 없으면 대체 UI로 폴백한다.
HERE = Path(__file__).resolve().parent
OUT = HERE / "kkotppa-archive.json"
KEY_MCP_PATH = Path(
    "/Users/rucyjung/Desktop/ai_workspace/project/instagram-analysis/.mcp.json"
)


def load_key() -> str:
    key = os.environ.get("HIKERAPI_KEY")
    if key:
        return key.strip()
    try:
        cfg = json.loads(KEY_MCP_PATH.read_text())
        return cfg["mcpServers"]["hikerapi"]["env"]["HIKERAPI_KEY"].strip()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"HIKERAPI_KEY를 찾을 수 없음: env 미설정 & {KEY_MCP_PATH} 읽기 실패 ({e})")


def curl_json(url: str, key: str, tries: int = 5):
    """curl로 GET JSON. 403/429는 백오프 재시도."""
    for t in range(tries):
        p = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", url, "-H", f"x-access-key: {key}"],
            capture_output=True, text=True,
        )
        body, _, code = p.stdout.rpartition("\n")
        if code == "200":
            return json.loads(body)
        if code in ("403", "429") and t < tries - 1:
            time.sleep(1.5 * (t + 1))
            continue
        sys.exit(f"HikerAPI 호출 실패 HTTP {code}: {url}\n{body[:300]}")
    return None


def thumb_of(item: dict):
    iv = item.get("image_versions")
    if isinstance(iv, list) and iv:
        return iv[0].get("url")
    if isinstance(iv, dict):
        cands = iv.get("items") or iv.get("candidates") or []
        if cands:
            return cands[0].get("url")
    return item.get("thumbnail_url")


_PRODUCT_MAP = {"carousel_container": "carousel"}


def map_post(item: dict) -> dict | None:
    code = item.get("code")
    taken = item.get("taken_at")  # 이미 ISO8601 UTC "…Z"
    if not code or not taken:
        return None
    post = {
        "code": code,
        "taken_at": taken,
        "permalink": f"https://www.instagram.com/p/{code}/",
    }
    thumb = thumb_of(item)
    if thumb:
        post["thumbnail_url"] = thumb
    cap = item.get("caption_text")
    if cap:
        post["caption"] = cap
    pt = item.get("product_type")
    if pt:
        post["product_type"] = _PRODUCT_MAP.get(pt, pt)
    return post


def fetch_page(key: str, cursor: str | None) -> tuple[list[dict], str | None]:
    """1페이지(최신 12개) 조회. (매핑된 posts, next_cursor) 반환."""
    url = f"https://api.hikerapi.com/v1/user/medias/chunk?user_id={PK}"
    if cursor:
        url += f"&end_cursor={cursor}"
    d = curl_json(url, key)
    if isinstance(d, list):
        items = d[0] if d else []
        nxt = d[1] if len(d) > 1 else None
    else:
        items = d.get("items", []) if isinstance(d, dict) else []
        nxt = d.get("next_cursor") if isinstance(d, dict) else None
    return [m for m in (map_post(it) for it in items) if m], nxt


def load_existing() -> list[dict]:
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text()).get("posts", [])
    except Exception as e:  # noqa: BLE001
        print(f"경고: 기존 아카이브 읽기 실패({e}) — 빈 상태로 시작", file=sys.stderr)
        return []


def merge(existing: list[dict], fresh: list[dict]) -> tuple[list[dict], int, int]:
    """기존 아카이브에 새로 받은 페이지를 병합.

    신규는 통째로 추가하고, 이미 있는 건 thumbnail_url만 갱신한다
    (caption·taken_at·permalink는 불변이라 덮어쓸 이유가 없다).
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
    """상위 THUMB_KEEP개를 벗어난 게시물의 만료된 썸네일 URL을 제거."""
    dropped = 0
    for p in posts[THUMB_KEEP:]:
        if p.pop("thumbnail_url", None):
            dropped += 1
    return dropped


def collect(key: str, full: bool) -> tuple[list[dict], int, int]:
    """증분(1페이지) 또는 전체 백필 수집 후 기존 아카이브와 병합."""
    existing = load_existing()
    fresh: list[dict] = []
    cursor = None
    pages = PAGES_MAX if full else 1
    for i in range(pages):
        page, cursor = fetch_page(key, cursor)
        fresh.extend(page)
        if not cursor:
            break
        if i + 1 < pages:
            time.sleep(1.3)
    print(f"조회: {len(fresh)}건 ({'전체 백필' if full else '증분 1페이지'})")
    return merge(existing, fresh)


def git(*args: str):
    subprocess.run(["git", "-C", str(HERE), *args], check=True)


def main() -> None:
    no_git = "--no-git" in sys.argv
    full = "--full" in sys.argv
    key = load_key()
    posts, added, refreshed = collect(key, full)
    if not posts:
        sys.exit("수집된 게시물이 0개 — 쓰기 중단(기존 파일 보존)")
    dropped = strip_dead_thumbs(posts)
    doc = {
        "account": ACCOUNT,
        "account_pk": PK,
        "source": "hikerapi",
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
    diff = subprocess.run(
        ["git", "-C", str(HERE), "diff", "--cached", "--quiet"]
    ).returncode
    if diff == 0:
        print("변경 없음 — 커밋 생략")
        return
    stamp = doc["updated"]
    git("commit", "-m", f"data: kkotppa archive refresh {stamp} (총 {len(posts)}건, 신규 +{added})")
    git("push", "origin", "main")
    print("pushed to origin/main")


if __name__ == "__main__":
    main()
