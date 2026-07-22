#!/usr/bin/env python3
"""꽃빠(@kkot.ppa) 로또 게시물 아카이브 수집기.

HikerAPI로 최신 게시물을 긁어 lotto-lab 앱이 런타임 fetch 하는 계약 스키마
JSON(kkotppa-archive.json)을 생성하고, 이 스크립트가 있는 git 레포에 커밋·푸시한다.

- 표준 라이브러리 + curl만 사용 (python urllib은 HikerAPI가 UA 차단 → 403).
- 매 실행마다 파일 전체를 덮어쓴다(증분 아님). CDN 썸네일 URL은 만료되므로 매번 갱신.
- HIKERAPI_KEY: 환경변수 우선, 없으면 instagram-analysis/.mcp.json에서 읽음.

Usage:
    python3 collect.py            # 수집 + JSON 쓰기 + git commit/push
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
MAX_POSTS = 90              # 최신순 최대 보관 개수
PAGES_MAX = 10             # 안전 상한 (12개/페이지)
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


def collect(key: str) -> list[dict]:
    posts: dict[str, dict] = {}
    cursor = None
    for _ in range(PAGES_MAX):
        url = f"https://api.hikerapi.com/v1/user/medias/chunk?user_id={PK}"
        if cursor:
            url += f"&end_cursor={cursor}"
        d = curl_json(url, key)
        if isinstance(d, list):
            items = d[0] if d else []
            cursor = d[1] if len(d) > 1 else None
        else:
            items = d.get("items", []) if isinstance(d, dict) else []
            cursor = d.get("next_cursor") if isinstance(d, dict) else None
        for it in items:
            m = map_post(it)
            if m:
                posts[m["code"]] = m
        if len(posts) >= MAX_POSTS or not cursor:
            break
        time.sleep(1.3)
    ordered = sorted(posts.values(), key=lambda p: p["taken_at"], reverse=True)
    return ordered[:MAX_POSTS]


def git(*args: str):
    subprocess.run(["git", "-C", str(HERE), *args], check=True)


def main() -> None:
    no_git = "--no-git" in sys.argv
    key = load_key()
    posts = collect(key)
    if not posts:
        sys.exit("수집된 게시물이 0개 — 쓰기 중단(기존 파일 보존)")
    doc = {
        "account": ACCOUNT,
        "account_pk": PK,
        "source": "hikerapi",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posts": posts,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {OUT} — {len(posts)} posts, latest {posts[0]['taken_at']}")
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
    git("commit", "-m", f"data: kkotppa archive refresh {stamp} ({len(posts)} posts)")
    git("push", "origin", "main")
    print("pushed to origin/main")


if __name__ == "__main__":
    main()
