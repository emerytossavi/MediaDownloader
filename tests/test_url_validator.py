"""Unit tests for URL validation, SSRF checks, and scheme filtering."""

import socket
import pytest
from unittest.mock import patch
from app.security.url_validator import (
    SSRFSecurityError,
    URLValidationError,
    extract_url_from_text,
    is_ip_forbidden,
    validate_url_safety,
)


def test_extract_url_from_text():
    assert extract_url_from_text("Voici le lien https://youtu.be/dQw4w9WgXcQ super !") == "https://youtu.be/dQw4w9WgXcQ"
    assert extract_url_from_text("Pas de lien ici.") is None
    assert extract_url_from_text("http://example.com/photo.jpg, regarde.") == "http://example.com/photo.jpg"
    assert extract_url_from_text("") is None


def test_is_ip_forbidden():
    assert is_ip_forbidden("127.0.0.1") is True
    assert is_ip_forbidden("127.1.2.3") is True
    assert is_ip_forbidden("10.0.0.5") is True
    assert is_ip_forbidden("192.168.1.1") is True
    assert is_ip_forbidden("172.16.0.1") is True
    assert is_ip_forbidden("169.254.169.254") is True
    assert is_ip_forbidden("0.0.0.0") is True
    assert is_ip_forbidden("::1") is True
    assert is_ip_forbidden("fe80::1") is True
    assert is_ip_forbidden("8.8.8.8") is False
    assert is_ip_forbidden("1.1.1.1") is False


@pytest.mark.asyncio
async def test_validate_url_valid_http_https():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    validated = await validate_url_safety(url, resolve_dns=False)
    assert validated == url

    url2 = "http://example.com:8080/image.png"
    validated2 = await validate_url_safety(url2, resolve_dns=False)
    assert validated2 == url2


@pytest.mark.asyncio
async def test_validate_url_invalid_schemes():
    forbidden_schemes = [
        "file:///etc/passwd",
        "ftp://example.com/file.zip",
        "gopher://example.com",
        "javascript:alert(1)",
        "data:text/plain;base64,SGVsbG8=",
    ]
    for url in forbidden_schemes:
        with pytest.raises(URLValidationError, match="is not allowed"):
            await validate_url_safety(url, resolve_dns=False)


@pytest.mark.asyncio
async def test_validate_url_credentials_rejected():
    with pytest.raises(URLValidationError, match="Embedded credentials"):
        await validate_url_safety("https://admin:secret@example.com/video", resolve_dns=False)


@pytest.mark.asyncio
async def test_validate_url_dangerous_ports():
    with pytest.raises(URLValidationError, match="Port 22 is not allowed"):
        await validate_url_safety("http://example.com:22/file", resolve_dns=False)

    with pytest.raises(URLValidationError, match="Port 6379 is not allowed"):
        await validate_url_safety("http://example.com:6379/data", resolve_dns=False)


@pytest.mark.asyncio
async def test_validate_url_ssrf_direct_ip():
    with pytest.raises(SSRFSecurityError, match="Direct access to private/loopback IP"):
        await validate_url_safety("http://127.0.0.1/admin", resolve_dns=False)

    with pytest.raises(SSRFSecurityError, match="Direct access to private/loopback IP"):
        await validate_url_safety("http://192.168.1.100/data", resolve_dns=False)

    with pytest.raises(SSRFSecurityError, match="Direct access to private/loopback IP"):
        await validate_url_safety("http://169.254.169.254/latest/meta-data", resolve_dns=False)


@pytest.mark.asyncio
async def test_validate_url_blocked_hostnames():
    with pytest.raises(SSRFSecurityError, match="Access to host 'localhost' is blocked"):
        await validate_url_safety("http://localhost/status", resolve_dns=False)

    with pytest.raises(SSRFSecurityError, match="Access to internal domain"):
        await validate_url_safety("http://service.local/data", resolve_dns=False)


@pytest.mark.asyncio
async def test_validate_url_dns_ssrf_resolution():
    # Mock socket getaddrinfo to return a private IP for a public-looking domain
    mock_addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.42", 80))]

    with patch("asyncio.get_running_loop") as mock_loop_getter:
        mock_loop = mock_loop_getter.return_value

        async def fake_getaddrinfo(*args, **kwargs):
            return mock_addr

        mock_loop.getaddrinfo = fake_getaddrinfo

        with pytest.raises(SSRFSecurityError, match="resolves to private or non-routable address"):
            await validate_url_safety("https://sneaky-internal-redirect.com/video", resolve_dns=True)
