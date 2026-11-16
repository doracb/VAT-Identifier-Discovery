import re

WEIGHTS = (8, 7, 6, 5, 4, 3, 2)


def normalize_vrn(raw_vat: str) -> str | None:
    """
    Strip spaces, hyphens, and optional GB/XI country prefixes.
    Returns the core 9-digit VRN string (truncating 12-digit branch suffixes),
    or None if the format does not match 9 or 12 digits.
    """
    if not raw_vat:
        return None

    cleaned = re.sub(r"[\s\-]", "", raw_vat.upper())
    if cleaned.startswith(("GB", "XI")):
        cleaned = cleaned[2:]

    if not cleaned.isdigit() or len(cleaned) not in (9, 12):
        return None

    return cleaned[:9]


def is_valid_uk_vat(raw_vat: str) -> bool:
    """
    Validate a UK VAT Registration Number using HMRC's Modulus-97
    and Modulus-97 (+55) checksum rules.
    """
    vrn = normalize_vrn(raw_vat)
    if vrn is None or vrn == "000000000":
        return False

    digits = [int(d) for d in vrn]
    weighted_sum = sum(d*w for d, w in zip(digits[:7], WEIGHTS))
    check_digits = int(vrn[7:])

    standard_valid = (weighted_sum + check_digits) % 97 == 0
    alt_55_valid = (weighted_sum + 55 + check_digits) % 97 == 0

    return standard_valid or alt_55_valid


if __name__ == "__main__":
    test_cases = [
        ("GB 220 4302 31", True),    # A valid standard Modulus-97
        ("GB 433 1868 94", True),    # A valid newer (+55) Modulus-97
        ("433186894001", True),      # A valid 12-digit branch format
        ("GB 123 4567 89", False),   # An invalid checksum
        ("GB 000 0000 00", False),   # All zeros guard
    ]
    
    for vat, expected in test_cases:
        result = is_valid_uk_vat(vat)
        assert result == expected, f"Failed for {vat}: got {result}, expected {expected}"
    print("All checksum sanity checks were passed!")