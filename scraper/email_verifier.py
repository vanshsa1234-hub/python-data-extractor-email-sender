"""
scraper/email_verifier.py
=========================
Email Verification — 3 layers of checks without sending any email.

Layer 1 — Syntax check       : regex + format validation
Layer 2 — DNS / MX check     : does the domain have mail servers?
Layer 3 — SMTP handshake     : does the mailbox actually exist?
          (connects to mail server, says RCPT TO, reads response, quits)

Results:
  "valid"       — passed all 3 checks
  "risky"       — passed syntax + DNS but SMTP was inconclusive
  "invalid"     — failed syntax or DNS
  "catch_all"   — domain accepts all emails (can't verify individual mailbox)
  "timeout"     — SMTP server didn't respond in time

Usage:
    from scraper.email_verifier import verify_email, verify_bulk

    result = verify_email("principal@dpsvasantkunj.com")
    print(result)
    # {'email': 'principal@dpsvasantkunj.com', 'status': 'valid',
    #  'reason': 'SMTP accepted', 'mx': 'mail.dpsvasantkunj.com'}

    results = verify_bulk(["a@school.com", "b@college.edu"])
"""

import re
import socket
import smtplib
import logging
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

log = logging.getLogger(__name__)

# ── DNS import (graceful if not installed) ────────────────────────────────────
try:
    import dns.resolver
    _dns_available = True
except ImportError:
    _dns_available = False
    log.warning("dnspython not installed — MX checks disabled. Run: pip install dnspython")

EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+$")

# Disposable/known-bad domains
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "throwaway.email", "yopmail.com", "trashmail.com",
    "example.com", "test.com", "tempmail.com", "fakeinbox.com",
}

# SMTP codes that mean the address is invalid
SMTP_INVALID_CODES = {550, 551, 552, 553, 554, 450, 451, 452}
SMTP_VALID_CODES   = {250, 251}


# ── Layer 1: Syntax ───────────────────────────────────────────────────────────

def check_syntax(email: str) -> tuple[bool, str]:
    """Returns (is_valid, reason)"""
    email = email.strip().lower()
    if not email:
        return False, "Empty email"
    if not EMAIL_RE.match(email):
        return False, "Invalid format"
    domain = email.split("@")[1]
    if domain in DISPOSABLE_DOMAINS:
        return False, f"Disposable domain: {domain}"
    tld = domain.split(".")[-1]
    if len(tld) < 2:
        return False, "Invalid TLD"
    return True, "Syntax OK"


# ── Layer 2: DNS / MX check ───────────────────────────────────────────────────

def get_mx_records(domain: str) -> list[str]:
    """Return list of MX hostnames for a domain, sorted by priority."""
    if not _dns_available:
        return []
    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=5)
        sorted_records = sorted(records, key=lambda r: r.preference)
        return [str(r.exchange).rstrip(".") for r in sorted_records]
    except Exception:
        return []


def check_dns(email: str) -> tuple[bool, str, list[str]]:
    """Returns (is_valid, reason, mx_list)"""
    domain = email.strip().lower().split("@")[1]

    # First try MX records
    mx_records = get_mx_records(domain)
    if mx_records:
        return True, f"MX found: {mx_records[0]}", mx_records

    # Fall back to A record (some small domains skip MX)
    try:
        socket.gethostbyname(domain)
        return True, f"A record found for {domain}", [domain]
    except socket.gaierror:
        pass

    return False, f"No MX or A record for domain: {domain}", []


# ── Layer 3: SMTP handshake ───────────────────────────────────────────────────

def check_smtp(
    email: str,
    mx_host: str,
    from_address: str = "verify@gmail.com",
    timeout: int = 10,
) -> tuple[str, str]:
    """
    Connect to MX server and check if the mailbox exists via RCPT TO.
    Returns (status, reason) where status is:
      'valid'     — server confirmed the mailbox exists
      'invalid'   — server rejected the address
      'catch_all' — server accepts everything (can't distinguish)
      'risky'     — couldn't confirm either way
      'timeout'   — connection timed out
    """
    try:
        with smtplib.SMTP(timeout=timeout) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo_or_helo_if_needed()
            smtp.mail(from_address)
            code, msg = smtp.rcpt(email)
            smtp.quit()

            msg_str = msg.decode("utf-8", errors="ignore") if isinstance(msg, bytes) else str(msg)

            if code in SMTP_VALID_CODES:
                # Check if it's a catch-all by testing a random address
                if _is_catch_all(mx_host, email.split("@")[1], from_address, timeout):
                    return "catch_all", "Domain accepts all addresses"
                return "valid", f"SMTP accepted (code {code})"

            if code in SMTP_INVALID_CODES:
                return "invalid", f"SMTP rejected (code {code}): {msg_str[:80]}"

            return "risky", f"Unexpected SMTP code {code}: {msg_str[:80]}"

    except smtplib.SMTPConnectError:
        return "risky", "Could not connect to SMTP server"
    except smtplib.SMTPServerDisconnected:
        return "risky", "SMTP server disconnected"
    except socket.timeout:
        return "timeout", f"SMTP timeout connecting to {mx_host}"
    except ConnectionRefusedError:
        return "risky", "SMTP connection refused (port 25 blocked)"
    except OSError as e:
        return "risky", f"Network error: {e}"
    except Exception as e:
        return "risky", f"SMTP error: {type(e).__name__}: {e}"


def _is_catch_all(mx_host: str, domain: str, from_address: str, timeout: int) -> bool:
    """Test a random fake address — if accepted, domain is catch-all."""
    fake = f"zz_test_{random.randint(10000,99999)}@{domain}"
    try:
        with smtplib.SMTP(timeout=timeout) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo_or_helo_if_needed()
            smtp.mail(from_address)
            code, _ = smtp.rcpt(fake)
            smtp.quit()
            return code in SMTP_VALID_CODES
    except Exception:
        return False


# ── Main verify function ───────────────────────────────────────────────────────

def verify_email(
    email: str,
    smtp_check: bool = True,
    from_address: str = "verify@gmail.com",
    timeout: int = 10,
) -> dict:
    """
    Full 3-layer email verification.

    Args:
        email:        Email address to verify.
        smtp_check:   Whether to do SMTP handshake (slower but more accurate).
        from_address: EHLO from address used in SMTP check.
        timeout:      Seconds to wait for SMTP response.

    Returns dict:
        {
          email:   str,
          status:  "valid" | "invalid" | "risky" | "catch_all" | "timeout",
          reason:  str,       # human-readable explanation
          mx:      str,       # primary MX hostname (empty if none found)
          checks:  {syntax, dns, smtp}  # per-layer results
        }
    """
    email = email.strip().lower()
    result = {
        "email":  email,
        "status": "invalid",
        "reason": "",
        "mx":     "",
        "checks": {"syntax": "", "dns": "", "smtp": ""},
    }

    # ── Layer 1: Syntax ────────────────────────────────────────────────────────
    syntax_ok, syntax_reason = check_syntax(email)
    result["checks"]["syntax"] = syntax_reason
    if not syntax_ok:
        result["status"] = "invalid"
        result["reason"] = syntax_reason
        return result

    # ── Layer 2: DNS ───────────────────────────────────────────────────────────
    dns_ok, dns_reason, mx_list = check_dns(email)
    result["checks"]["dns"] = dns_reason
    result["mx"] = mx_list[0] if mx_list else ""

    if not dns_ok:
        result["status"] = "invalid"
        result["reason"] = dns_reason
        return result

    # ── Layer 3: SMTP ──────────────────────────────────────────────────────────
    if not smtp_check or not mx_list:
        # Skip SMTP — mark as risky (DNS passed but can't confirm mailbox)
        result["status"] = "risky"
        result["reason"] = "DNS OK — SMTP check skipped"
        result["checks"]["smtp"] = "Skipped"
        return result

    smtp_status, smtp_reason = check_smtp(
        email, mx_list[0], from_address, timeout
    )
    result["checks"]["smtp"] = smtp_reason
    result["status"] = smtp_status
    result["reason"] = smtp_reason
    return result


# ── Bulk verification ──────────────────────────────────────────────────────────

def verify_bulk(
    emails: list[str],
    smtp_check: bool = True,
    max_workers: int = 5,
    delay: float = 0.5,
    from_address: str = "verify@gmail.com",
    timeout: int = 10,
) -> list[dict]:
    """
    Verify a list of email addresses concurrently.

    Args:
        emails:      List of email strings.
        smtp_check:  Enable SMTP handshake per email.
        max_workers: Concurrent threads (keep low to avoid rate limits).
        delay:       Seconds between each thread start.
        from_address: EHLO from address.
        timeout:     Per-email SMTP timeout.

    Returns:
        List of result dicts (same format as verify_email).
    """
    results  = []
    total    = len(emails)

    log.info("Verifying %d emails (smtp=%s, workers=%d)", total, smtp_check, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i, email in enumerate(emails):
            future = executor.submit(
                verify_email, email, smtp_check, from_address, timeout
            )
            futures[future] = email
            if delay > 0 and i < total - 1:
                time.sleep(delay)

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                log.info(
                    "Verified %-40s → %-10s %s",
                    result["email"], result["status"], result["reason"][:50]
                )
            except Exception as exc:
                email = futures[future]
                log.warning("Verification failed for %s: %s", email, exc)
                results.append({
                    "email": email, "status": "risky",
                    "reason": str(exc), "mx": "",
                    "checks": {"syntax": "", "dns": "", "smtp": ""},
                })

    # Sort results to match original order
    order = {e: i for i, e in enumerate(emails)}
    results.sort(key=lambda r: order.get(r["email"], 999))

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    log.info("Verification complete: %s", counts)

    return results


def filter_verified(
    results: list[dict],
    include_statuses: list[str] = None,
) -> list[dict]:
    """
    Filter verification results to only keep emails worth sending to.

    Default keeps: valid + catch_all + risky
    Drops: invalid + timeout
    """
    if include_statuses is None:
        include_statuses = ["valid", "catch_all", "risky"]
    return [r for r in results if r["status"] in include_statuses]