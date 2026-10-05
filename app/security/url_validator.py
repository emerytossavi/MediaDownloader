"""URL validation and SSRF prevention module."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import urlparse
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Allowed schemes
ALLOWED_SCHEMES = {"http", "https"}

# Allowed destination ports (standard web traffic only)
ALLOWED_PORTS = {80, 443, 8080, 8443}

# Forbidden hostnames (case-insensitive)
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "local",
    "broadcasthost",
    "0.0.0.0",
}

# Blocked domain suffixes
BLOCKED_DOMAIN_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".corp",
)

# Regex to detect URLs inside message text
_URL_REGEX = re.compile(
    r"https?://(?:[a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)?@)?[^\s/$.?#].[^\s]*",
    re.IGNORECASE,
)


class URLValidationError(ValueError):
    """Raised when an URL fails syntax, scheme, or domain security checks."""


class SSRFSecurityError(URLValidationError):
    """Raised when an URL targets internal, loopback, or non-routable infrastructure."""


def extract_url_from_text(text: str) -> Optional[str]:
    """Extract the first HTTP/HTTPS URL from arbitrary message text."""
    if not text:
        return None
    match = _URL_REGEX.search(text.strip())
    if match:
        url = match.group(0).rstrip(".,;!?'\"()[]{}<>")
        return url
    return None


def is_ip_forbidden(ip_str: str) -> bool:
    """Check if an IP address is private, loopback, link-local, multicast, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return True  # If it cannot be parsed as an IP when expected, block it for safety


async def validate_url_safety(url: str, resolve_dns: bool = True) -> str:
    """Perform comprehensive URL syntactic, scheme, port, and SSRF validation.

    Args:
        url: The candidate URL string.
        resolve_dns: Whether to perform DNS resolution to verify destination IP routability.

    Returns:
        The validated URL string.

    Raises:
        URLValidationError: For syntax, schema, port, or userinfo violations.
        SSRFSecurityError: For targets resolving to localhost/private IP ranges.
    """
    if not url or not isinstance(url, str):
        raise URLValidationError("URL is empty or not a valid string.")

    cleaned_url = url.strip()
    if len(cleaned_url) > 2048:
        raise URLValidationError("URL length exceeds maximum allowed limit (2048 chars).")

    try:
        parsed = urlparse(cleaned_url)
    except Exception as exc:
        raise URLValidationError(f"Malformed URL: {exc}") from exc

    # 1. Scheme check
    if not parsed.scheme or parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise URLValidationError(
            f"Scheme '{parsed.scheme}' is not allowed. Only HTTP and HTTPS are permitted."
        )

    # 2. Host check
    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError("URL does not contain a valid hostname.")

    hostname_lower = hostname.lower().strip()

    # 3. Userinfo check (credentials in URL)
    if parsed.username or parsed.password:
        raise URLValidationError("Embedded credentials (user:password@) in URL are forbidden.")

    # 4. Blocked hostname names & suffixes
    if hostname_lower in BLOCKED_HOSTNAMES:
        raise SSRFSecurityError(f"Access to host '{hostname}' is blocked.")

    if any(hostname_lower.endswith(suffix) for suffix in BLOCKED_DOMAIN_SUFFIXES):
        raise SSRFSecurityError(f"Access to internal domain '{hostname}' is forbidden.")

    # 5. Port check
    port = parsed.port
    if port is not None and port not in ALLOWED_PORTS:
        raise URLValidationError(
            f"Port {port} is not allowed. Only standard web ports (80, 443, 8080, 8443) are accepted."
        )

    # 6. Direct IP check (e.g. http://127.0.0.1 or http://[::1])
    is_direct_ip = False
    parsed_ip = None
    try:
        parsed_ip = ipaddress.ip_address(hostname_lower.strip("[]"))
        is_direct_ip = True
    except ValueError:
        is_direct_ip = False

    if is_direct_ip and parsed_ip is not None:
        if is_ip_forbidden(str(parsed_ip)):
            raise SSRFSecurityError(f"Direct access to private/loopback IP '{parsed_ip}' is forbidden.")
        # If it's a valid public IP, no DNS resolution is needed
        return cleaned_url

    # 7. Asynchronous DNS Resolution & SSRF check
    if resolve_dns:
        await _verify_dns_resolution(hostname_lower)

    return cleaned_url


async def _verify_dns_resolution(hostname: str) -> None:
    """Resolve hostname and ensure no resolved IP address points to private or loopback ranges."""
    loop = asyncio.get_running_loop()

    try:
        # Run socket.getaddrinfo in the default thread pool to avoid blocking the asyncio event loop
        addr_info = await loop.getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise URLValidationError(f"Unable to resolve domain '{hostname}': {exc}") from exc

    if not addr_info:
        raise URLValidationError(f"Domain '{hostname}' did not resolve to any IP address.")

    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        if is_ip_forbidden(ip_str):
            logger.warning(
                f"SSRF prevention triggered: hostname '{hostname}' resolved to forbidden IP '{ip_str}'"
            )
            raise SSRFSecurityError(
                f"Destination '{hostname}' resolves to private or non-routable address ({ip_str})."
            )
