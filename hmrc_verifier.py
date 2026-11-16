import time
import requests
from bs4 import BeautifulSoup
from validator import normalize_vrn, is_valid_uk_vat

HMRC_URL = "https://www.tax.service.gov.uk/check-vat-number/enter-vat-details"


def verify_vat_hmrc(raw_vat: str, session: requests.Session | None = None) -> dict:
    """
    Validate a UK VAT number locally via Modulus-97, then confirm it against
    HMRC's public web checker to retrieve the registered name and address.
    """
    vrn = normalize_vrn(raw_vat)
    if not vrn or not is_valid_uk_vat(vrn):
        return {
            "vrn": vrn,
            "valid": False,
            "reason": "Failed local format or Modulus-97 checksum",
            "registered_name": None,
            "registered_address": None,
        }

    sess = session or requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Research-PoC/1.0"
    })

    try:
        get_resp = sess.get(HMRC_URL, timeout=15)
        get_resp.raise_for_status()
        soup = BeautifulSoup(get_resp.text, "html.parser")

        payload = {"target": vrn, "withConsultationNumber": "false"}
        for hidden in soup.find_all("input", {"type": "hidden"}):
            if hidden.get("name"):
                payload[hidden["name"]] = hidden.get("value", "")

        # 2. POST the candidate VRN
        post_resp = sess.post(HMRC_URL, data=payload, timeout=15)
        post_resp.raise_for_status()
        res_soup = BeautifulSoup(post_resp.text, "html.parser")

        page_text = res_soup.get_text(" ", strip=True)
        if "Invalid UK VAT number" in page_text or "cannot find a match" in page_text.lower():
            return {
                "vrn": vrn,
                "valid": False,
                "reason": "Rejected by HMRC checker",
                "registered_name": None,
                "registered_address": None,
            }

        details = {}
        main_el = res_soup.find("main") or res_soup
        for h3 in main_el.find_all("h3"):
            next_p = h3.find_next_sibling("p")
            if next_p:
                key = h3.get_text(" ", strip=True).lower()
                lines = [line.strip() for line in next_p.stripped_strings]
                details[key] = ", ".join(lines)

        reg_name = details.get("registered business name")
        reg_addr = details.get("registered business address")
        h1_text = res_soup.find("h1").get_text(" ", strip=True) if res_soup.find("h1") else ""

        return {
            "vrn": vrn,
            "valid": bool(reg_name),
            "reason": "Confirmed by HMRC" if reg_name else f"Unparsed page (H1: {h1_text})",
            "registered_name": reg_name,
            "registered_address": reg_addr,
        }

    except requests.RequestException as exc:
        return {
            "vrn": vrn,
            "valid": False,
            "reason": f"HTTP error: {exc}",
            "registered_name": None,
            "registered_address": None,
        }


if __name__ == "__main__":
    s = requests.Session()
    for test_vrn in ["GB 765 9707 76", "GB 220 4302 31", "GB 123 4567 89"]:
        print(test_vrn, "->", verify_vat_hmrc(test_vrn, session=s))
        time.sleep(0.5)