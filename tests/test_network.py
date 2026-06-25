"""Smoke tests for network security: SSRF, valid_url, safe_ip"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from network import safe_ip, valid_url, resolve


def test_safe_ip_public():
    assert safe_ip("8.8.8.8") is True


def test_safe_ip_private():
    assert safe_ip("192.168.1.1") is False
    assert safe_ip("10.0.0.1") is False
    assert safe_ip("172.16.0.1") is False


def test_safe_ip_loopback():
    assert safe_ip("127.0.0.1") is False
    assert safe_ip("::1") is False


def test_safe_ip_aws_metadata():
    assert safe_ip("169.254.169.254") is False


def test_safe_ip_link_local():
    assert safe_ip("169.254.1.1") is False


def test_safe_ip_multicast():
    assert safe_ip("224.0.0.1") is False


def test_safe_ip_invalid():
    assert safe_ip("not-an-ip") is False
    assert safe_ip("") is False


def test_valid_url_https():
    assert valid_url("https://example.com") is True


def test_valid_url_http():
    assert valid_url("http://example.com") is True


def test_valid_url_bad_scheme():
    assert valid_url("ftp://example.com") is False
    assert valid_url("file:///etc/passwd") is False


def test_valid_url_no_host():
    assert valid_url("") is False
    assert valid_url("http://") is False


def test_valid_url_blocked_ports():
    """SSRF: only ports 80 and 443 allowed"""
    assert valid_url("http://example.com:22") is False
    assert valid_url("http://example.com:8080") is False
    assert valid_url("http://example.com:8000") is False


def test_valid_url_allowed_ports():
    assert valid_url("http://example.com:80") is True
    assert valid_url("https://example.com:443") is True


def test_resolve():
    ip = resolve("example.com")
    assert ip is not None
    assert safe_ip(ip) is True


def test_resolve_nonexistent():
    ip = resolve("this-domain-definitely-does-not-exist-xyz.test")
    assert ip is None


def test_resolve_loopback():
    """resolve for localhost should return None (safe_ip=False)"""
    ip = resolve("localhost")
    assert ip is None


# ============================================================
#  Net class
# ============================================================

def test_net_get_success():
    from network import Net
    net = Net()
    r = net.get("https://example.com")
    assert r is not None
    net.close()


def test_net_get_stats():
    from network import Net
    net = Net()
    net.get("https://example.com")
    assert net._stats["req"] >= 1
    net.close()


def test_net_semaphore():
    from network import Net, config
    net = Net()
    assert net.sem._value == config.MAX_CONCURRENT
    net.close()


# ============================================================
#  safe_req
# ============================================================

def test_safe_req_ok():
    from network import safe_req
    r = safe_req("https://example.com")
    assert r is not None


def test_safe_req_redirect():
    """safe_req should follow redirects"""
    from network import safe_req, net

    # Create a fake 302 response followed by 200
    fake_resp_200 = MagicMock()
    fake_resp_200.status_code = 200
    fake_resp_200.ok = True

    fake_resp_302 = MagicMock()
    fake_resp_302.status_code = 302
    fake_resp_302.ok = False
    fake_resp_302.headers = {"Location": "https://example.com/final"}

    with patch.object(net, "get", side_effect=[fake_resp_302, fake_resp_200]):
        r = safe_req("https://example.com/start")
        assert r.status_code == 200


# ============================================================
#  path_ok
# ============================================================

def test_path_ok_allowed_dir():
    from network import path_ok
    import tempfile
    with tempfile.NamedTemporaryFile(dir=".", suffix=".test", delete=False) as f:
        path = f.name
    try:
        assert path_ok(path) is True
    finally:
        os.unlink(path)


def test_path_ok_traversal():
    from network import path_ok
    assert path_ok("/etc/passwd") is False
    assert path_ok("../../etc/passwd") is False


def test_path_ok_no_allowed_dirs():
    from network import path_ok, ALLOWED_DIRS
    orig = ALLOWED_DIRS[:]
    ALLOWED_DIRS.clear()
    try:
        assert path_ok(".") is False
    finally:
        ALLOWED_DIRS.extend(orig)
