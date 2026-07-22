"""Smoke tests for network security: SSRF, valid_url, safe_ip, DNS pinning."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from network import safe_ip, valid_url, resolve, resolve_all


# ============================================================
#  safe_ip — IPv4 + IPv6
# ============================================================

def test_safe_ip_public():
    assert safe_ip("8.8.8.8") is True
    assert safe_ip("1.1.1.1") is True


def test_safe_ip_private():
    assert safe_ip("192.168.1.1") is False
    assert safe_ip("10.0.0.1") is False
    assert safe_ip("172.16.0.1") is False


def test_safe_ip_loopback():
    assert safe_ip("127.0.0.1") is False
    assert safe_ip("::1") is False


def test_safe_ip_aws_metadata():
    """SSRF: AWS metadata endpoint must be blocked."""
    assert safe_ip("169.254.169.254") is False


def test_safe_ip_link_local():
    assert safe_ip("169.254.1.1") is False


def test_safe_ip_multicast():
    assert safe_ip("224.0.0.1") is False


def test_safe_ip_unspecified():
    """0.0.0.0 / :: must be blocked."""
    assert safe_ip("0.0.0.0") is False
    assert safe_ip("::") is False


def test_safe_ip_invalid():
    assert safe_ip("not-an-ip") is False
    assert safe_ip("") is False


def test_safe_ip_ipv6_private():
    """IPv6 private ranges must be blocked (was bypass with gethostbyname)."""
    # fc00::/7 — ULA (IPv6 аналог private)
    assert safe_ip("fc00::1") is False
    assert safe_ip("fd00::1") is False
    # fe80::/10 — link-local IPv6
    assert safe_ip("fe80::1") is False


def test_safe_ip_ipv6_public():
    """Public IPv6 must pass."""
    # 2606:4700:4700::1111 — Cloudflare DNS
    assert safe_ip("2606:4700:4700::1111") is True


def test_safe_ip_ipv6_mapped_ipv4():
    """IPv6-mapped IPv4 (e.g. ::ffff:127.0.0.1) must not bypass checks."""
    # ::ffff:127.0.0.1 — это loopback, должен быть заблокирован
    assert safe_ip("::ffff:127.0.0.1") is False
    # ::ffff:8.8.8.8 — публичный, должен пройти
    assert safe_ip("::ffff:8.8.8.8") is True


# ============================================================
#  valid_url
# ============================================================

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
    """SSRF: only ports 80 and 443 allowed."""
    assert valid_url("http://example.com:22") is False
    assert valid_url("http://example.com:8080") is False
    assert valid_url("http://example.com:8000") is False


def test_valid_url_allowed_ports():
    assert valid_url("http://example.com:80") is True
    assert valid_url("https://example.com:443") is True


# ============================================================
#  resolve / resolve_all
# ============================================================

def test_resolve():
    ip = resolve("example.com")
    assert ip is not None
    assert safe_ip(ip) is True


def test_resolve_nonexistent():
    ip = resolve("this-domain-definitely-does-not-exist-xyz.test")
    assert ip is None


def test_resolve_loopback():
    """resolve for localhost should return None (safe_ip=False)."""
    ip = resolve("localhost")
    assert ip is None


def test_resolve_all_returns_list():
    """resolve_all returns a list of valid IP strings (IPv4 and/or IPv6)."""
    addrs = resolve_all("example.com")
    assert isinstance(addrs, list)
    assert len(addrs) > 0
    for ip in addrs:
        assert safe_ip(ip) is True


def test_resolve_all_nonexistent():
    """resolve_all for nonexistent domain returns empty list."""
    addrs = resolve_all("this-domain-definitely-does-not-exist-xyz.test")
    assert addrs == []


def test_resolve_all_loopback():
    """resolve_all for localhost returns [] (loopback addresses filtered)."""
    addrs = resolve_all("localhost")
    assert addrs == []


def test_resolve_delegates_to_resolve_all():
    """resolve() returns first element of resolve_all() or None."""
    with patch("network.resolve_all", return_value=["1.2.3.4", "5.6.7.8"]):
        assert resolve("example.com") == "1.2.3.4"
    with patch("network.resolve_all", return_value=[]):
        assert resolve("example.com") is None


def test_resolve_all_blocks_mixed_addresses(monkeypatch):
    """If any resolved address is unsafe, host is blocked entirely."""
    import network
    # Симулируем: DNS отдаёт и публичный, и приватный IP
    monkeypatch.setattr(network, "_getaddrinfo_all", lambda host: ["8.8.8.8", "127.0.0.1"])
    # Очищаем кэш, чтобы не влиял на тест
    with network._dns_lock:
        network._dns_cache.pop("mixed.test", None)
    addrs = network.resolve_all("mixed.test")
    assert addrs == [], f"expected blocked, got {addrs}"


def test_dns_cache_cleanup_expired(monkeypatch):
    """P3: expired cache entries are lazily removed."""
    import network
    import time as _time

    # Подкладываем просроченную запись
    with network._dns_lock:
        network._dns_cache["expired.test"] = (["1.2.3.4"], _time.time() - network._DNS_TTL - 1)
        network._dns_cache["fresh.test"] = (["5.6.7.8"], _time.time())

    # resolve_all должен удалить expired.test при следующем вызове
    # Мокаем _getaddrinfo_all, чтобы не делать реальный DNS для fresh.test
    monkeypatch.setattr(network, "_getaddrinfo_all", lambda host: ["9.9.9.9"])

    network.resolve_all("fresh.test")

    with network._dns_lock:
        assert "expired.test" not in network._dns_cache
        assert "fresh.test" in network._dns_cache


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
#  PinnedHTTPAdapter — DNS pinning unit tests
# ============================================================

def test_pinned_adapter_rewrites_url():
    """PinnedHTTPAdapter rewrites hostname -> pinned IP in request URL."""
    from network import PinnedHTTPAdapter
    import requests
    from requests.adapters import HTTPAdapter

    adapter = PinnedHTTPAdapter({"example.com": "93.184.216.34"})
    req = requests.Request("GET", "https://example.com/path?q=1").prepare()

    with patch.object(HTTPAdapter, "send") as mock_send:
        mock_send.return_value = MagicMock(status_code=200)
        adapter.send(req)

        sent_req = mock_send.call_args[0][0]
        assert "93.184.216.34" in sent_req.url
        # Host в netloc заменён на IP
        assert "example.com" not in sent_req.url.split("://")[1].split("/")[0]
        # Оригинальный Host сохранён в заголовке
        assert sent_req.headers.get("Host") == "example.com"


def test_pinned_adapter_preserves_port():
    """PinnedHTTPAdapter preserves port when rewriting URL."""
    from network import PinnedHTTPAdapter
    import requests
    from requests.adapters import HTTPAdapter

    adapter = PinnedHTTPAdapter({"example.com": "93.184.216.34"})
    req = requests.Request("GET", "https://example.com:443/path").prepare()

    with patch.object(HTTPAdapter, "send") as mock_send:
        mock_send.return_value = MagicMock(status_code=200)
        adapter.send(req)
        sent_req = mock_send.call_args[0][0]
        assert "93.184.216.34:443" in sent_req.url


def test_pinned_adapter_no_pin_for_unknown_host():
    """PinnedHTTPAdapter does not rewrite URL for hosts not in host_ip_map."""
    from network import PinnedHTTPAdapter
    import requests
    from requests.adapters import HTTPAdapter

    adapter = PinnedHTTPAdapter({"pinned.com": "1.2.3.4"})
    req = requests.Request("GET", "https://other.com/path").prepare()

    with patch.object(HTTPAdapter, "send") as mock_send:
        mock_send.return_value = MagicMock(status_code=200)
        adapter.send(req)
        sent_req = mock_send.call_args[0][0]
        assert sent_req.url == "https://other.com/path"


def test_pinned_adapter_https_disables_verify():
    """PinnedHTTPAdapter sets verify=False for HTTPS (IP breaks SNI)."""
    from network import PinnedHTTPAdapter
    import requests
    from requests.adapters import HTTPAdapter

    adapter = PinnedHTTPAdapter({"example.com": "93.184.216.34"})
    req = requests.Request("GET", "https://example.com/").prepare()

    with patch.object(HTTPAdapter, "send") as mock_send:
        mock_send.return_value = MagicMock(status_code=200)
        adapter.send(req, verify=True)
        kwargs = mock_send.call_args.kwargs
        assert kwargs.get("verify") is False


def test_pinned_adapter_http_keeps_verify():
    """PinnedHTTPAdapter does NOT disable verify for plain HTTP (no TLS)."""
    from network import PinnedHTTPAdapter
    import requests
    from requests.adapters import HTTPAdapter

    adapter = PinnedHTTPAdapter({"example.com": "93.184.216.34"})
    req = requests.Request("GET", "http://example.com/").prepare()

    with patch.object(HTTPAdapter, "send") as mock_send:
        mock_send.return_value = MagicMock(status_code=200)
        adapter.send(req, verify=True)
        kwargs = mock_send.call_args.kwargs
        assert kwargs.get("verify") is True


# ============================================================
#  safe_req
# ============================================================

def test_safe_req_ok():
    from network import safe_req
    r = safe_req("https://example.com")
    assert r is not None


def test_safe_req_redirect():
    """safe_req should follow redirects with per-hop DNS validation."""
    import requests
    from network import safe_req

    fake_resp_200 = MagicMock()
    fake_resp_200.status_code = 200
    fake_resp_200.ok = True

    fake_resp_302 = MagicMock()
    fake_resp_302.status_code = 302
    fake_resp_302.ok = False
    fake_resp_302.headers = {"Location": "https://example.com/final"}

    # safe_req creates its own Session with PinnedHTTPAdapter,
    # so we mock at requests.Session level.
    with patch.object(requests.Session, "get", side_effect=[fake_resp_302, fake_resp_200]):
        r = safe_req("https://example.com/start")
        assert r.status_code == 200


def test_safe_req_redirect_closes_response():
    """P3: redirect response is closed before following Location."""
    import requests
    from network import safe_req

    fake_resp_200 = MagicMock()
    fake_resp_200.status_code = 200
    fake_resp_200.ok = True

    fake_resp_302 = MagicMock()
    fake_resp_302.status_code = 302
    fake_resp_302.ok = False
    fake_resp_302.headers = {"Location": "https://example.com/final"}
    fake_resp_302.close = MagicMock()

    with patch.object(requests.Session, "get", side_effect=[fake_resp_302, fake_resp_200]):
        safe_req("https://example.com/start")
        fake_resp_302.close.assert_called_once()


def test_safe_req_blocks_loopback():
    """safe_req must block loopback URLs (SSRF)."""
    from network import safe_req
    try:
        safe_req("http://127.0.0.1/")
        assert False, "expected Exception"
    except Exception as e:
        assert "Blocked" in str(e)


def test_safe_req_blocks_metadata():
    """safe_req must block AWS metadata endpoint (SSRF)."""
    from network import safe_req
    try:
        safe_req("http://169.254.169.254/latest/meta-data/")
        assert False, "expected Exception"
    except Exception as e:
        assert "Blocked" in str(e)


def test_safe_req_loop_detection():
    """safe_req must detect redirect loops."""
    import requests
    from network import safe_req

    fake_resp_302 = MagicMock()
    fake_resp_302.status_code = 302
    fake_resp_302.ok = False
    fake_resp_302.headers = {"Location": "https://example.com/start"}
    fake_resp_302.close = MagicMock()

    with patch.object(requests.Session, "get", return_value=fake_resp_302):
        try:
            safe_req("https://example.com/start")
            assert False, "expected loop Exception"
        except Exception as e:
            assert "Loop" in str(e) or "redirects" in str(e).lower()


def test_safe_req_validates_redirect_target():
    """safe_req must validate each redirect target (no SSRF via redirect)."""
    import requests
    from network import safe_req

    fake_resp_302 = MagicMock()
    fake_resp_302.status_code = 302
    fake_resp_302.ok = False
    # Редирект на loopback — должен быть заблокирован
    fake_resp_302.headers = {"Location": "http://127.0.0.1/"}
    fake_resp_302.close = MagicMock()

    with patch.object(requests.Session, "get", return_value=fake_resp_302):
        try:
            safe_req("https://example.com/start")
            assert False, "expected Exception for redirect to loopback"
        except Exception as e:
            assert "Blocked" in str(e)


def test_safe_req_accepts_session_param():
    """safe_req accepts optional session parameter (backward compat)."""
    import requests
    from network import safe_req

    fake_resp_200 = MagicMock()
    fake_resp_200.status_code = 200
    fake_resp_200.ok = True

    sess = requests.Session()
    with patch.object(sess, "get", return_value=fake_resp_200):
        r = safe_req("https://example.com/", session=sess)
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
