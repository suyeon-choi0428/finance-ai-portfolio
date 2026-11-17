import csv
import hashlib
import html
import json
import re
import time

from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
)

import feedparser
import requests


SOURCE = {
    "finance.yahoo.com": "Yahoo Finance",
    "cnbc.com": "CNBC",
    "fortune.com": "Fortune",
    "businessinsider.com": "Business Insider",
    "cnn.com": "CNN Business",
    "foxbusiness.com": "Fox Business",
    "fool.com": "The Motley Fool",
    "zacks.com": "Zacks",
}

UTC = timezone.utc
KST = timezone(timedelta(hours=9))

# 실행 시점을 기준으로 직전 168시간
END = datetime.now(UTC)
START = END - timedelta(days=7)

OUT = Path("data/news_rss")
RAW = OUT / "raw_feeds"
RAW.mkdir(parents=True, exist_ok=True)

RUN_ID = END.strftime("%Y%m%dT%H%M%SZ")

# 기존 Atom 주소는 작동했지만 공식 도움말은 RSS 미지원 안내.
# 사용하지 않으려면 False로 변경.
INCLUDE_BI_LEGACY = True


# CNBC
FEEDS = [
    (
        "cnbc.com",
        label,
        "https://search.cnbc.com/rs/search/combinedcms/view.xml"
        f"?partnerId=wrss01&id={fid}",
    )
    for label, fid in [
        ("top", "100003114"),
        ("business", "10001147"),
        ("economy", "20910258"),
        ("finance", "10000664"),
        ("technology", "19854910"),
        ("world", "100727362"),
    ]
]

# Fortune, The Motley Fool, Zacks
FEEDS += [
    (
        "fortune.com",
        "all",
        "https://fortune.com/feed/",
    ),
    (
        "fool.com",
        "headlines",
        "https://www.fool.com/feeds/index.aspx?id=foolwatch&format=rss2",
    ),
    (
        "zacks.com",
        "commentary",
        "https://feed.zacks.com/commentary/AllStories/rss",
    ),
]

# Fox Business
FEEDS += [
    (
        "foxbusiness.com",
        section,
        f"https://moxie.foxbusiness.com/google-publisher/{section}.xml",
    )
    for section in ["latest", "economy", "markets", "technology"]
]

# Business Insider: 기존 Atom 피드
if INCLUDE_BI_LEGACY:
    FEEDS.append(
        (
            "businessinsider.com",
            "legacy_atom",
            "https://feeds.businessinsider.com/custom/all",
        )
    )

# 사용할 수 있는 일반 뉴스 피드를 확인하지 못한 매체
NOT_CONFIGURED = {
    "finance.yahoo.com": (
        "확인한 일반 뉴스 RSS 주소가 404. "
        "종목별 피드로 대체하지 않음."
    ),
    "cnn.com": (
        "확인한 Business RSS 주소가 503. "
        "현재 사용 가능한 직접 피드 미확인."
    ),
}

print(
    "수집 기간(KST):",
    START.astimezone(KST),
    "~",
    END.astimezone(KST),
)
print("저장 위치:", OUT.resolve())

class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def clean(value):
    parser = TextOnly()
    parser.feed(str(value or ""))
    text = html.unescape(" ".join(parser.parts))
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    if not value:
        return None

    try:
        dt = parsedate_to_datetime(str(value))
    except (ValueError, TypeError, OverflowError):
        try:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except ValueError:
            return None

    # 시간대가 없는 날짜를 임의로 UTC로 가정하지 않음
    if dt.tzinfo is None:
        return None

    return dt.astimezone(UTC)


def canonical_url(url):
    p = urlsplit(url)

    if p.scheme not in ("http", "https"):
        return ""

    # 알려진 추적용 파라미터와 fragment만 제거
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {
            "fbclid", "gclid", "mc_cid", "mc_eid"
        }
    ]

    return urlunsplit(
        (
            p.scheme,
            p.netloc.lower(),
            p.path,
            urlencode(query),
            "",
        )
    )


def prepare_text(row):
    body = row.get("feed_content") or row.get("summary") or ""

    row["text_for_embedding"] = (
        row["title"] + "\n" + body
    ).strip()

    row["text_basis"] = (
        "rss_content"
        if row.get("feed_content")
        else "rss_summary"
        if row.get("summary")
        else "title_only"
    )

    # rss_content라도 기사 본문 전체라는 보장은 없음
    return row


def collect_feed(spec):
    domain, label, feed_url = spec

    report = {
        "source": SOURCE[domain],
        "feed": label,
        "feed_url": feed_url,
        "status": "error",
        "entries": 0,
        "kept": 0,
        "missing_date": 0,
        "outside_window": 0,
        "invalid_item": 0,
        "coverage_guaranteed": False,
    }

    rows = []
    dates = []

    try:
        response = requests.get(
            feed_url,
            timeout=(8, 25),
            headers={
                "User-Agent": "NewsRelationCollector/1.0",
                "Accept": (
                    "application/rss+xml, "
                    "application/atom+xml, application/xml"
                ),
            },
        )

        report["http_status"] = response.status_code
        report["resolved_feed_url"] = response.url
        response.raise_for_status()

        if len(response.content) > 10_000_000:
            raise ValueError("피드 응답이 10MB를 초과함")

        # 실행 당시 받은 피드 원본 보관
        raw_file = RAW / f"{RUN_ID}_{domain}_{label}.xml"
        raw_file.write_bytes(response.content)
        report["raw_file"] = str(raw_file)

        parsed = feedparser.parse(response.content)

        if not parsed.version:
            raise ValueError(
                "RSS/Atom 형식으로 확인되지 않은 응답"
            )

        report["entries"] = len(parsed.entries)
        report["parser_warning"] = str(
            parsed.get("bozo_exception", "")
        )[:200]

        for entry in parsed.entries:
            # updated만으로 오래된 기사를 신규 기사로 간주하지 않음
            published_raw = entry.get("published", "")
            dt = parse_date(published_raw)

            if dt is None:
                report["missing_date"] += 1
                continue

            dates.append(dt)

            if not START <= dt < END:
                report["outside_window"] += 1
                continue

            link = entry.get("link", "")
            if not link:
                report["invalid_item"] += 1
                continue

            original_url = urljoin(response.url, link)
            url = canonical_url(original_url)
            host = (urlsplit(url).hostname or "").lower()
            title = clean(entry.get("title", ""))

            # 요청한 매체 도메인의 기사 링크인지 확인
            valid_domain = (
                host == domain or host.endswith("." + domain)
            )

            if not title or not url or not valid_domain:
                report["invalid_item"] += 1
                continue

            content = clean(
                " ".join(
                    part.get("value", "")
                    for part in entry.get("content", [])
                )
            )

            seen = datetime.now(UTC).isoformat()

            row = {
                "article_id": hashlib.sha256(
                    url.encode()
                ).hexdigest(),
                "source_domain": domain,
                "source_name": SOURCE[domain],
                "title": title,
                "url": url,
                "original_url": original_url,
                "published_raw": published_raw,
                "published_utc": dt.isoformat(),
                "published_kst": dt.astimezone(KST).isoformat(),
                "summary": clean(entry.get("summary", "")),
                "feed_content": content,
                "feed_names": [label],
                "first_seen_utc": seen,
                "last_seen_utc": seen,
            }

            rows.append(prepare_text(row))

        report["status"] = (
            "ok" if parsed.entries else "empty_feed"
        )
        report["kept"] = len(rows)

        if dates:
            report["oldest_in_feed_utc"] = min(dates).isoformat()
            report["newest_in_feed_utc"] = max(dates).isoformat()

            # True면 피드의 가장 오래된 기사도 수집 시작일보다 최신
            report["oldest_after_window_start"] = (
                min(dates) > START
            )

    except (requests.RequestException, ValueError) as exc:
        report["error"] = str(exc)[:300]

    return rows, report

all_rows = []
reports = []

for spec in FEEDS:
    rows, report = collect_feed(spec)

    all_rows.extend(rows)
    reports.append(report)

    print(
        f"{report['source']:18} "
        f"{report['feed']:12} "
        f"{report['status']:10} "
        f"{report['kept']:4}건"
    )

    time.sleep(0.2)


for domain, reason in NOT_CONFIGURED.items():
    reports.append(
        {
            "source": SOURCE[domain],
            "status": "not_configured",
            "reason": reason,
            "coverage_guaranteed": False,
        }
    )


def merge_record(previous, current):
    merged = {**previous, **current}

    # 같은 기사의 RSS 텍스트 중 더 긴 것을 보존
    for key in ("summary", "feed_content"):
        merged[key] = max(
            [
                previous.get(key, ""),
                current.get(key, ""),
            ],
            key=len,
        )

    merged["feed_names"] = sorted(
        set(previous.get("feed_names", []))
        | set(current.get("feed_names", []))
    )

    merged["first_seen_utc"] = previous.get(
        "first_seen_utc",
        current["first_seen_utc"],
    )

    variants = set(
        previous.get(
            "published_variants_utc",
            [previous["published_utc"]],
        )
    )
    variants.update(
        current.get(
            "published_variants_utc",
            [current["published_utc"]],
        )
    )

    merged["published_variants_utc"] = sorted(variants)
    merged["published_date_conflict"] = len(variants) > 1

    # 같은 URL의 발행일이 다르면 이른 날짜를 보존하고 충돌 표시
    dt = min(parse_date(x) for x in variants)

    merged["published_utc"] = dt.isoformat()
    merged["published_kst"] = dt.astimezone(KST).isoformat()

    return prepare_text(merged)


unique = {}

for row in all_rows:
    key = row["article_id"]

    unique[key] = (
        merge_record(unique[key], row)
        if key in unique
        else row
    )

print(
    "이번 실행 URL 중복 제거:",
    len(all_rows),
    "→",
    len(unique),
)

archive_path = OUT / "news_archive.json"

old_rows = (
    json.loads(archive_path.read_text(encoding="utf-8"))
    if archive_path.exists()
    else []
)

archive = {
    row["article_id"]: row
    for row in old_rows
}

for key, row in unique.items():
    archive[key] = (
        merge_record(archive[key], row)
        if key in archive
        else row
    )


# 기존 누적 저장 자료까지 포함해서 최근 7일 결과 계산
weekly_news = sorted(
    [
        row
        for row in archive.values()
        if START <= parse_date(row["published_utc"]) < END
    ],
    key=lambda row: row["published_utc"],
    reverse=True,
)


def save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(path)


save_json(
    archive_path,
    list(archive.values()),
)

save_json(
    OUT / "news_last7days.json",
    weekly_news,
)

save_json(
    OUT / "feed_status.json",
    reports,
)

save_json(
    OUT / "run_metadata.json",
    {
        "start_utc": START.isoformat(),
        "end_utc": END.isoformat(),
        "start_kst": START.astimezone(KST).isoformat(),
        "end_kst": END.astimezone(KST).isoformat(),
        "unique_articles": len(weekly_news),
        "coverage_guaranteed": False,
        "scope": (
            "실행 당시 RSS/Atom과 기존 로컬 누적 저장에서 "
            "확보한 최근 7일 기사"
        ),
        "limitation": (
            "지난 7일 전체 기사 복원·누락 없음·"
            "본문 전체 확보를 보장하지 않음"
        ),
    },
)


columns = [
    "article_id",
    "source_name",
    "title",
    "published_kst",
    "url",
    "summary",
    "feed_content",
    "text_for_embedding",
    "text_basis",
    "feed_names",
]

with (OUT / "news_last7days.csv").open(
    "w",
    encoding="utf-8-sig",
    newline="",
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=columns,
    )

    writer.writeheader()

    for row in weekly_news:
        item = {
            key: row.get(key, "")
            for key in columns
        }

        item["feed_names"] = json.dumps(
            item["feed_names"],
            ensure_ascii=False,
        )

        writer.writerow(item)

print("저장 완료:", OUT.resolve())

print("최근 7일 고유 기사:", len(weekly_news))

print(
    "매체별:",
    dict(
        Counter(
            row["source_name"]
            for row in weekly_news
        )
    ),
)

for report in reports:
    if report["status"] != "ok":
        print(
            "확인 필요:",
            report["source"],
            report.get("feed", ""),
            report.get("error")
            or report.get("reason")
            or report["status"],
        )

for row in weekly_news[:3]:
    print(
        "\n",
        row["source_name"],
        row["published_kst"],
    )
    print(row["title"])
    print(row["url"])

print(
    "\n주의: 기사 수가 많거나 피드의 가장 오래된 날짜가 "
    "7일 이전이어도 전체 확보를 증명하지는 않습니다."
)