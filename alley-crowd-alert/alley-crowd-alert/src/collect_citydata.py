#!/usr/bin/env python3
"""서울 실시간 인구데이터(citydata_ppltn) 수집기.

서울 실시간 도시데이터 API는 과거 데이터를 주지 않으므로, 주기적으로 직접 호출해 쌓는다.
- 원본 응답: data/raw/YYYY-MM-DD.jsonl (응답 형식이 바뀌어도 원본을 잃지 않도록 그대로 저장)
- 요약 표: data/ppltn.csv (혼잡 단계, 추정 인구 범위, 기준 시각)

사용법:
    export SEOUL_API_KEY=발급받은_인증키
    python src/collect_citydata.py --areas config/areas.txt                 # 1회 수집
    python src/collect_citydata.py --areas config/areas.txt --interval 300  # 5분마다 반복
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

API_URL = "http://openapi.seoul.go.kr:8088/{key}/json/citydata_ppltn/1/1/{area}"
SUMMARY_FIELDS = [
    "fetched_at",
    "AREA_NM",
    "AREA_CD",
    "AREA_CONGEST_LVL",
    "AREA_PPLTN_MIN",
    "AREA_PPLTN_MAX",
    "PPLTN_TIME",
    "AREA_CONGEST_MSG",
]


def read_areas(path: Path) -> list[str]:
    """장소명 목록 파일을 읽는다. 빈 줄과 #으로 시작하는 줄은 건너뛴다."""
    areas = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            areas.append(name)
    return areas


def fetch(key: str, area: str, timeout: float = 15.0) -> dict:
    """장소 한 곳의 실시간 인구데이터를 호출한다(API는 한 번에 한 장소만 받는다)."""
    url = API_URL.format(
        key=urllib.parse.quote(key, safe=""),
        area=urllib.parse.quote(area, safe=""),
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_record(payload) -> dict | None:
    """응답에서 AREA_CONGEST_LVL이 들어 있는 레코드를 찾는다.

    최상위 키 이름이 바뀌어도 동작하도록 응답 전체를 재귀로 탐색한다.
    """
    if isinstance(payload, dict):
        if "AREA_CONGEST_LVL" in payload:
            return payload
        for value in payload.values():
            found = find_record(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_record(item)
            if found is not None:
                return found
    return None


def collect_once(key: str, areas: list[str], out_dir: Path, fetcher=fetch) -> int:
    """모든 장소를 한 번씩 호출해 원본과 요약을 저장하고, 성공한 장소 수를 돌려준다."""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{datetime.now():%Y-%m-%d}.jsonl"
    summary_path = out_dir / "ppltn.csv"
    write_header = not summary_path.exists()

    ok = 0
    with raw_path.open("a", encoding="utf-8") as raw_f, \
            summary_path.open("a", encoding="utf-8", newline="") as sum_f:
        writer = csv.DictWriter(sum_f, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for area in areas:
            fetched_at = datetime.now().isoformat(timespec="seconds")
            try:
                payload = fetcher(key, area)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                print(f"[{fetched_at}] {area}: 호출 실패 - {exc}", file=sys.stderr)
                continue
            raw_f.write(json.dumps(
                {"fetched_at": fetched_at, "area": area, "payload": payload},
                ensure_ascii=False,
            ) + "\n")
            record = find_record(payload)
            if record is None:
                print(f"[{fetched_at}] {area}: 인구 데이터 없음(장소명 확인 필요)", file=sys.stderr)
                continue
            writer.writerow({"fetched_at": fetched_at, **record})
            ok += 1
            time.sleep(0.2)  # 연속 호출 간격
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="서울 실시간 인구데이터 수집기")
    parser.add_argument("--areas", type=Path, default=Path("config/areas.txt"),
                        help="장소명 목록 파일 (기본: config/areas.txt)")
    parser.add_argument("--out", type=Path, default=Path("data"),
                        help="저장 폴더 (기본: data)")
    parser.add_argument("--interval", type=int, default=0,
                        help="반복 간격(초). 0이면 한 번만 수집")
    args = parser.parse_args()

    key = os.environ.get("SEOUL_API_KEY")
    if not key:
        print("환경변수 SEOUL_API_KEY에 서울 열린데이터광장 인증키를 넣어 주세요.", file=sys.stderr)
        return 1
    areas = read_areas(args.areas)
    if not areas:
        print(f"{args.areas}에 장소명이 없습니다.", file=sys.stderr)
        return 1

    while True:
        ok = collect_once(key, areas, args.out)
        print(f"[{datetime.now():%H:%M:%S}] {ok}/{len(areas)}곳 수집")
        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
