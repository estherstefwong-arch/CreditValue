"""Extract credit-rating sections from Annual Information Forms filed on EDGAR (40-F / 20-F / 10-K)."""

import argparse
import gzip
import html
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

from bloomberg_adapter.edgar_xbrl import ROOT, USER_AGENT
from bloomberg_adapter.manual_csv import AGENCIES, RATINGS_COLUMNS, RATINGS_PATH, WITHDRAWN, normalize_rating

RAW_DIR = ROOT / "data" / "raw" / "aif"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
ANNUAL_FORMS = {"40-F", "20-F", "10-K"}

# Parent -> CIK of the filer whose AIF reports the ratings of our bond issuer.
FILERS = {
    "RY": 1000275, "TD": 947263, "BMO": 927971, "BNS": 9631, "CM": 1045520,
    "BCE": 718940, "RCI": 733099, "TU": 868675,
    "ENB": 895728, "TRP": 1232384, "PPL": 1546066,
    "FTS": 1666175, "BIP": 1406234,
}
RATING_TOKEN = re.compile(r"\b(Aaa|Aa[1-3]|A[1-3]|Baa[1-3]|AA[+-]?|A[+-]|BBB[+-]?)(?![\w-])|\((high|low)\)")


def _get(url: str) -> requests.Response:
    if not USER_AGENT:
        raise RuntimeError('Set SEC_USER_AGENT="Your Name you@example.com" in the environment or .env')
    for attempt in range(3):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
            resp.raise_for_status()
            time.sleep(0.2)  # SEC fair-access limit is 10 requests/second
            return resp
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def annual_filings(cik: int, since: str) -> list[dict]:
    """Annual reports filed on or after since. Banks file so many notes that older filings
    sit in paged history files, so read those until they predate since."""
    sub = _get(SUBMISSIONS_URL.format(cik=cik)).json()
    pages = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        if pages[-1]["filingDate"] and min(pages[-1]["filingDate"]) < since:
            break
        pages.append(_get(f"https://data.sec.gov/submissions/{f['name']}").json())
    return [
        {"form": f, "filed": d, "acc": a.replace("-", ""), "primary": p}
        for page in pages
        for f, d, a, p in zip(page["form"], page["filingDate"], page["accessionNumber"], page["primaryDocument"])
        if f in ANNUAL_FORMS and d >= since
    ]


def to_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " | ", raw)
    text = html.unescape(text)
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    return re.sub(r"\s+", " ", text)


def find_aif(cik: int, filing: dict) -> tuple[str, str] | None:
    """(document name, text) of the AIF, or the main annual report when no AIF exhibit exists.

    Exhibits are tried in order: names containing "aif", then EX-99s, then everything else.
    """
    base = ARCHIVE_URL.format(cik=cik, acc=filing["acc"])
    items = _get(f"{base}/index.json").json()["directory"]["item"]
    docs = [i["name"] for i in items if i["name"].endswith(".htm") and not re.fullmatch(r"R\d+\.htm", i["name"])]

    def priority(name: str) -> int:
        n = name.lower()
        return 0 if "aif" in n else 1 if re.search(r"ex[-_]?\d|99[-_]?1", n) else 2

    for name in sorted((d for d in docs if d != filing["primary"]), key=priority):
        if priority(name) == 2:
            break
        text = to_text(_get(f"{base}/{name}").text)
        if re.search(r"annual information form", text[:20000], re.I):
            return name, text
    if filing["form"] in {"10-K", "20-F"}:  # ratings sit in the annual report itself
        return filing["primary"], to_text(_get(f"{base}/{filing['primary']}").text)
    return None


def ratings_section(text: str, width: int = 6000) -> str:
    """The window with the densest run of rating symbols near agency names."""
    agencies = r"Moody|DBRS|S&P|Standard & Poor|Fitch"
    best, best_score = "", 0
    for m in re.finditer(agencies, text):
        window = text[max(0, m.start() - 1000): m.start() + width]
        score = len(RATING_TOKEN.findall(window)) + 3 * len(re.findall(agencies, window))
        if score > best_score:
            best, best_score = window, score
    return best


def extract(parent: str, since: str) -> list[Path]:
    """Write one rating-section file per annual filing; the full AIF text is cached gzipped."""
    cik = FILERS[parent]
    out = []
    for filing in annual_filings(cik, since):
        path = RAW_DIR / f"{parent}_{filing['filed']}.txt"
        full = RAW_DIR / "full" / f"{parent}_{filing['filed']}.txt.gz"
        if full.exists():
            source, text = gzip.decompress(full.read_bytes()).decode().split("\n", 1)
        else:
            found = find_aif(cik, filing)
            if found is None:
                print(f"  {parent} {filing['filed']} {filing['form']}: no AIF found")
                continue
            name, text = found
            source = f"{ARCHIVE_URL.format(cik=cik, acc=filing['acc'])}/{name}"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(gzip.compress(f"{source}\n{text}".encode()))
        path.write_text(f"SOURCE {source}\nFILED {filing['filed']} {filing['form']}\n\n{ratings_section(text)}\n")
        out.append(path)
    return out


def _full_texts() -> dict[str, str]:
    """Source URL -> cached full text, for quote checking."""
    texts = {}
    for f in (RAW_DIR / "full").glob("*.txt.gz"):
        source, text = gzip.decompress(f.read_bytes()).decode().split("\n", 1)
        texts[source] = text
    return texts


def verify(rows: pd.DataFrame) -> pd.DataFrame:
    """Add a `problems` column: quotes must be verbatim in the filing and contain each rating."""
    texts = _full_texts()
    problems = []
    for r in rows.itertuples():
        issues = []
        text = texts.get(r.source)
        quotes = [q.strip() for q in str(r.quote).split(";;") if q.strip()]
        if text is None:
            issues.append("source not in cache")
        elif any(q not in text for q in quotes):
            issues.append("quote not found verbatim")
        joined = " ".join(quotes)
        for agency in AGENCIES:
            value = getattr(r, agency)
            if not value:
                continue
            try:
                normalize_rating(agency, value)
            except ValueError:
                issues.append(f"bad {agency} symbol {value!r}")
            if value == WITHDRAWN:
                if "withdr" not in joined.lower():
                    issues.append(f"{agency} withdrawal not in quote")
                continue
            core = value.split(" (")[0]
            if core not in joined:
                issues.append(f"{agency} {value!r} not in quote")
        problems.append("; ".join(issues))
    return rows.assign(problems=problems)


def merge(extracted: list[Path]) -> pd.DataFrame:
    """Verified extraction rows -> reference/ratings.csv, keeping hand-entered rows for other issuers."""
    rows = pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in extracted], ignore_index=True)
    rows = verify(rows)
    bad = rows[rows["problems"] != ""]
    good = rows[(rows["problems"] == "") & (rows[AGENCIES] != "").any(axis=1)].copy()
    for agency in AGENCIES:
        good[agency] = [normalize_rating(agency, v) for v in good[agency]]
    # No stated as-of date: the ratings were public by the filing date at the latest.
    good["effective_date"] = good["as_of_date"].where(good["as_of_date"] != "", good["filed"])
    good["notes"] = (good["notes"] + "; filed " + good["filed"]).str.strip("; ")
    # The same rating action is often repeated in later filings; keep the first report of it.
    good = good.sort_values("filed").drop_duplicates(["issuer", "seniority", "effective_date"] + AGENCIES)

    existing = pd.read_csv(RATINGS_PATH, dtype=str, keep_default_na=False) if RATINGS_PATH.exists() else \
        pd.DataFrame(columns=RATINGS_COLUMNS)
    existing = existing.reindex(columns=RATINGS_COLUMNS, fill_value="")
    keep = existing[~existing["issuer"].isin(good["issuer"])]
    out = pd.concat([good[RATINGS_COLUMNS], keep], ignore_index=True).sort_values(["issuer", "seniority", "effective_date"])
    out.to_csv(RATINGS_PATH, index=False)
    return bad


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2021-01-01")
    parser.add_argument("--merge", nargs="+", type=Path, metavar="CSV",
                        help="verify extracted rating CSVs and write reference/ratings.csv")
    parser.add_argument("parents", nargs="*", default=list(FILERS))
    args = parser.parse_args()
    if args.merge:
        bad = merge(args.merge)
        print(f"Wrote {RATINGS_PATH}; {len(bad)} rows rejected")
        if not bad.empty:
            print(bad[["issuer", "seniority", "as_of_date", "sp", "moodys", "dbrs", "problems"]].to_string(index=False))
        return
    for parent in args.parents:
        paths = extract(parent, args.since)
        print(f"{parent}: {len(paths)} AIF rating sections")


if __name__ == "__main__":
    main()
