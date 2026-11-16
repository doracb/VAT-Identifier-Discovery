import re
import time
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from validator import is_valid_uk_vat, normalize_vrn
from hmrc_verifier import verify_vat_hmrc

DIRECTORY_BLACKLIST = {
    "gov.uk",
    "companieshouse.gov.uk",
    "endole.co.uk",
    "checkcompany.co.uk",
    "duedil.com",
    "opencorporates.com",
    "company-information.service.gov.uk",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "yell.com",
    "yelp.co.uk",
    "192.com",
    "dnb.com",
    "bloomberg.com",
    "wikipedia.org",
    "bizdb.co.uk",
    "uk.globaldatabase.com",
    "pomanda.com",
    "kompass.com",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
}

GB_PREFIX_RE = re.compile(r"\b(?:GB|XI)\s*(\d{3}[\s\-]?\d{4}[\s\-]?\d{2}(?:[\s\-]?\d{3})?)\b", re.I)
VAT_CONTEXT_RE = re.compile(
    r"(?:VAT|V\.A\.T\.|Tax\s*(?:No|Number|Reg(?:istration)?|ID))[^0-9]{0,25}"
    r"((?:GB|XI)?\s*\d{3}[\s\-]?\d{4}[\s\-]?\d{2}(?:[\s\-]?\d{3})?)\b",
    re.I,
)
EORI_RE = re.compile(r"\bGB\s*(\d{9})000\b", re.I)


def is_blacklisted_domain(url: str) -> bool:
    netloc = urlparse(url).netloc.lower().lstrip("www.")
    return any(netloc == bad or netloc.endswith("." + bad) for bad in DIRECTORY_BLACKLIST)


def find_company_website(company_name: str, postcode: str) -> str | None:
    """Resolve a UK legal entity name to its probable official website URL."""
    outward_code = str(postcode).split()[0] if pd.notna(postcode) and str(postcode).strip() else ""
    query = f'"{company_name}" {outward_code} UK'.strip()

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="uk-en", max_results=6))
        for res in results:
            href = res.get("href", "")
            if href.startswith("http") and not is_blacklisted_domain(href):
                parsed = urlparse(href)
                return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return None


def extract_candidates_from_html(html: str, crn: str) -> tuple[set[str], bool, bool]:
    """
    Extract candidate VRNs (direct or via EORI) from raw HTML/text.
    Returns (valid_checksum_vrns, found_via_eori, crn_matched_on_site).
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    crn_clean = str(crn).strip().zfill(8)
    crn_found = crn_clean in re.sub(r"\s+", "", text)

    raw_matches = set()
    eori_found = False

    for m in GB_PREFIX_RE.findall(text):
        raw_matches.add(m)
    for m in VAT_CONTEXT_RE.findall(text):
        raw_matches.add(m)
    for m in EORI_RE.findall(text):
        raw_matches.add(m)
        eori_found = True

    valid_vrns = set()
    for raw in raw_matches:
        norm = normalize_vrn(raw)
        if norm and is_valid_uk_vat(norm):
            valid_vrns.add(norm)

    return valid_vrns, eori_found, crn_found


def crawl_site_for_vat(base_url: str, crn: str) -> dict:
    """Fetch homepage + up to 4 internal legal/contact pages and extract candidate VRNs."""
    visited = set()
    to_visit = [base_url]
    all_candidates = set()
    any_eori = False
    any_crn = False
    pages_fetched = 0

    try:
        resp = requests.get(base_url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code >= 400:
            return {"reachable": False, "pages": 0, "candidates": [], "eori": False, "crn_anchor": False}

        pages_fetched += 1
        visited.add(resp.url)
        vrns, eori, crn_hit = extract_candidates_from_html(resp.text, crn)
        all_candidates.update(vrns)
        any_eori |= eori
        any_crn |= crn_hit

        soup = BeautifulSoup(resp.text, "html.parser")
        keywords = ("contact", "about", "terms", "privacy", "legal", "vat", "imprint")
        base_netloc = urlparse(resp.url).netloc

        for a in soup.find_all("a", href=True):
            href_lower = a["href"].lower()
            text_lower = a.get_text(" ", strip=True).lower()
            if any(k in href_lower or k in text_lower for k in keywords):
                full_url = urljoin(resp.url, a["href"]).split("#")[0]
                if urlparse(full_url).netloc == base_netloc and full_url not in visited:
                    to_visit.append(full_url)
                    visited.add(full_url)
            if len(to_visit) >= 5:
                break

        for sub_url in to_visit[1:]:
            try:
                sub_resp = requests.get(sub_url, headers=HEADERS, timeout=8)
                if sub_resp.status_code < 400:
                    pages_fetched += 1
                    vrns, eori, crn_hit = extract_candidates_from_html(sub_resp.text, crn)
                    all_candidates.update(vrns)
                    any_eori |= eori
                    any_crn |= crn_hit
            except requests.RequestException:
                continue

        return {
            "reachable": True,
            "pages": pages_fetched,
            "candidates": sorted(all_candidates),
            "eori": any_eori,
            "crn_anchor": any_crn,
        }

    except requests.RequestException:
        return {"reachable": False, "pages": 0, "candidates": [], "eori": False, "crn_anchor": False}


def names_or_postcodes_match(ch_name: str, ch_postcode: str, hmrc_name: str | None, hmrc_addr: str | None) -> bool:
    """Check if HMRC's registered name or postcode reconciles with Companies House."""
    if not hmrc_name:
        return False

    if pd.notna(ch_postcode) and hmrc_addr:
        pc_clean = re.sub(r"\s+", "", str(ch_postcode).upper())
        addr_clean = re.sub(r"\s+", "", hmrc_addr.upper())
        if pc_clean and pc_clean in addr_clean:
            return True

    stop = {"LTD", "LIMITED", "PLC", "LLP", "UK", "THE", "AND", "&"}
    ch_tokens = {t for t in re.findall(r"[A-Z0-9]+", str(ch_name).upper()) if t not in stop}
    hmrc_tokens = {t for t in re.findall(r"[A-Z0-9]+", hmrc_name.upper()) if t not in stop}
    if ch_tokens and hmrc_tokens:
        overlap = len(ch_tokens & hmrc_tokens) / min(len(ch_tokens), len(hmrc_tokens))
        if overlap >= 0.5:
            return True

    return False


def run_pipeline(input_csv: str = "sample_companies.csv", output_csv: str = "poc_results.csv", limit: int | None = None):
    df = pd.read_csv(input_csv, dtype={"CompanyNumber": str})
    if limit:
        df = df.head(limit).copy()

    hmrc_session = requests.Session()
    records = []

    for idx, row in df.iterrows():
        name = row["CompanyName"]
        crn = row["CompanyNumber"]
        postcode = row["RegAddress.PostCode"]
        tier = row["SampleTier"]

        print(f"[{idx + 1}/{len(df)}] {name} ({tier})...")

        website = find_company_website(name, postcode)
        if not website:
            records.append({
                "CompanyName": name,
                "CompanyNumber": crn,
                "SampleTier": tier,
                "Postcode": postcode,
                "Website": None,
                "PagesCrawled": 0,
                "CRN_Anchor": False,
                "Found_Via_EORI": False,
                "Candidate_VRN": None,
                "HMRC_Valid": False,
                "Entity_Match": False,
                "HMRC_Name": None,
                "HMRC_Address": None,
                "Outcome": "NO_WEBSITE_FOUND",
            })
            time.sleep(1.0)
            continue

        crawl = crawl_site_for_vat(website, crn)
        if not crawl["reachable"]:
            records.append({
                "CompanyName": name,
                "CompanyNumber": crn,
                "SampleTier": tier,
                "Postcode": postcode,
                "Website": website,
                "PagesCrawled": 0,
                "CRN_Anchor": False,
                "Found_Via_EORI": False,
                "Candidate_VRN": None,
                "HMRC_Valid": False,
                "Entity_Match": False,
                "HMRC_Name": None,
                "HMRC_Address": None,
                "Outcome": "SITE_UNREACHABLE",
            })
            time.sleep(1.0)
            continue

        if not crawl["candidates"]:
            records.append({
                "CompanyName": name,
                "CompanyNumber": crn,
                "SampleTier": tier,
                "Postcode": postcode,
                "Website": website,
                "PagesCrawled": crawl["pages"],
                "CRN_Anchor": crawl["crn_anchor"],
                "Found_Via_EORI": False,
                "Candidate_VRN": None,
                "HMRC_Valid": False,
                "Entity_Match": False,
                "HMRC_Name": None,
                "HMRC_Address": None,
                "Outcome": "WEBSITE_FOUND_NO_VAT",
            })
            time.sleep(1.0)
            continue

        chosen_res = None
        chosen_vrn = crawl["candidates"][0]
        entity_matched = False

        for cand in crawl["candidates"]:
            res = verify_vat_hmrc(cand, session=hmrc_session)
            time.sleep(0.4)
            if res["valid"]:
                matched = names_or_postcodes_match(name, postcode, res["registered_name"], res["registered_address"])
                chosen_res = res
                chosen_vrn = cand
                entity_matched = matched
                if matched:
                    break

        if chosen_res and chosen_res["valid"]:
            outcome = "VERIFIED_MATCH" if entity_matched else "VERIFIED_ENTITY_MISMATCH"
        else:
            outcome = "CHECKSUM_PASSED_HMRC_REJECTED"

        records.append({
            "CompanyName": name,
            "CompanyNumber": crn,
            "SampleTier": tier,
            "Postcode": postcode,
            "Website": website,
            "PagesCrawled": crawl["pages"],
            "CRN_Anchor": crawl["crn_anchor"],
            "Found_Via_EORI": crawl["eori"],
            "Candidate_VRN": f"GB{chosen_vrn}",
            "HMRC_Valid": bool(chosen_res and chosen_res["valid"]),
            "Entity_Match": entity_matched,
            "HMRC_Name": chosen_res["registered_name"] if chosen_res else None,
            "HMRC_Address": chosen_res["registered_address"] if chosen_res else None,
            "Outcome": outcome,
        })
        time.sleep(1.0)

    out_df = pd.DataFrame(records)
    out_df.to_csv(output_csv, index=False)

    print("\n=== PIPELINE SUMMARY ===")
    print(out_df["Outcome"].value_counts())
    print("\n=== OUTCOMES BY SAMPLE TIER ===")
    print(pd.crosstab(out_df["SampleTier"], out_df["Outcome"]))


if __name__ == "__main__":
    run_pipeline(limit=None)