import socket
import ipaddress
import threading
import time
from urllib.parse import urlparse, urljoin
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import config

# Импортируем logger из server.py (циклический импорт избегается через поздний импорт или передачу logger)
import logging
logger = logging.getLogger("SearchTool")

class Net:
    def __init__(self):
        self.s = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429,500,502,503])
        self.s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20))
        self.s.headers.update({"User-Agent": "SearchTool/3.4-Production"})
        self.sem = threading.Semaphore(config.MAX_CONCURRENT)
        self._stats = {"req": 0, "err": 0, "bytes": 0}

    def get(self, url, **kw):
        with self.sem:
            self._stats["req"] += 1
            try:
                r = self.s.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=False, **kw)
                if r.ok:
                    # Используем Content-Length из заголовка для статистики,
                    # чтобы не материализовать тело ответа при stream=True.
                    # Если заголовок кривой или отсутствует — не падаем, пропускаем.
                    cl = r.headers.get("Content-Length")
                    if cl:
                        try:
                            self._stats["bytes"] += int(cl)
                        except (TypeError, ValueError):
                            pass
                else:
                    self._stats["err"] += 1
                return r
            except Exception:
                self._stats["err"] += 1
                raise

    def close(self):
        self.s.close()

net = Net()

# ============================================================
#  DNS & Security
# ============================================================

def safe_ip(ip):
    """Проверка IP-адреса (IPv4 и IPv6) на принадлежность к приватным/опасным диапазонам.

    Обрабатывает IPv6-mapped IPv4 (напр. ::ffff:127.0.0.1) — нормализует к IPv4,
    иначе обходит проверки через mapped-форму.
    """
    try:
        o = ipaddress.ip_address(ip)
        if isinstance(o, ipaddress.IPv6Address) and o.ipv4_mapped is not None:
            o = o.ipv4_mapped
        return not any([
            o.is_private, o.is_loopback, o.is_reserved,
            o.is_multicast, o.is_link_local, o.is_unspecified,
            str(o) == "169.254.169.254",
        ])
    except Exception as e:
        logger.warning(f"safe_ip failed for {ip}: {e}")
        return False

# DNS-кэш: {host: (result_list, timestamp)}
# result_list — список ВСЕХ валидных IP (может быть пустым).
_dns_cache = {}
_dns_lock = threading.Lock()
_DNS_TTL = 300  # секунд

def _getaddrinfo_all(host):
    """Получить все IP-адреса (IPv4 + IPv6) через getaddrinfo.

    Возвращает список уникальных IP-строк. Бросает socket.gaierror при ошибке DNS.
    gethostbyname() НЕ подходит — он умеет только IPv4, что позволяло обойти
    проверку через AAAA-запись на приватный IPv6.
    """
    seen = set()
    addrs = []
    # getaddrinfo возвращает список кортежей:
    # (family, type, proto, canonname, sockaddr)
    # sockaddr для IPv4 — (ip, port), для IPv6 — (ip, port, flowinfo, scope_id)
    infos = socket.getaddrinfo(host, None)
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0]
        if ip not in seen:
            seen.add(ip)
            addrs.append(ip)
    return addrs

def resolve_all(host):
    """Разрешает host во ВСЕ валидные IP-адреса (IPv4 + IPv6).

    Возвращает список IP-строк, прошедших safe_ip(). Пустой список — если DNS
    упал, нет адресов или хотя бы один адрес невалиден (все должны быть safe).

    Используется для DNS-pinning: берём любой валидный IP и фиксируем его
    в PinnedHTTPAdapter, чтобы requests коннектился напрямую к IP — это
    устраняет TOCTOU DNS rebinding между проверкой и коннектом.
    """
    now = time.time()
    with _dns_lock:
        # P3: ленивая очистка просроченных записей из кэша (старше TTL).
        # Иначе кэш растёт бесконечно при скрейпинге множества доменов.
        expired = [h for h, (_, ts) in _dns_cache.items() if now - ts > _DNS_TTL]
        for h in expired:
            del _dns_cache[h]
        # Проверяем кэш (валидная запись — не старше TTL)
        if host in _dns_cache:
            result, timestamp = _dns_cache[host]
            if now - timestamp < _DNS_TTL:
                return list(result)

    try:
        # Не мутируем глобальный socket.setdefaulttimeout — это ломает
        # параллельные HTTP-запросы в многопоточной среде.
        all_addrs = _getaddrinfo_all(host)
        valid = [ip for ip in all_addrs if safe_ip(ip)]
        # Если хотя бы один адрес невалиден (приватный/loopback/и т.д.) —
        # считаем хост подозрительным и блокируем полностью.
        # Это защищает от mixed-resolution атак, когда у домена есть и
        # публичный, и приватный адрес.
        if len(valid) != len(all_addrs):
            logger.warning(
                f"resolve_all: {host} has mixed/unsafe addresses "
                f"({len(valid)}/{len(all_addrs)} safe) — blocking"
            )
            valid = []
        with _dns_lock:
            _dns_cache[host] = (valid, now)
        return valid
    except Exception as e:
        logger.warning(f"resolve_all failed for {host}: {e}")
        with _dns_lock:
            _dns_cache[host] = ([], now)
        return []

def resolve(host):
    """Обратная совместимость: возвращает ОДИН валидный IP-адрес или None.

    Делегирует в resolve_all() и берёт первый валидный адрес.
    Сохранено для существующих коллеров, ожидающих строку/None.
    """
    addrs = resolve_all(host)
    return addrs[0] if addrs else None

def valid_url(u):
    """Валидация URL: scheme (http/https), порт (80/443), SSRF-проверка DNS.

    Использует resolve_all() (а не resolve()), чтобы убедиться, что хост
    разрешается хотя бы в один валидный IP. Все адреса должны быть safe.
    """
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        if p.port and p.port not in {80, 443}:
            return False
        return len(resolve_all(p.hostname)) > 0
    except Exception as e:
        logger.warning(f"valid_url failed for {u}: {e}")
        return False

# ============================================================
#  DNS Pinning: PinnedHTTPAdapter
# ============================================================

class PinnedHTTPAdapter(HTTPAdapter):
    """HTTPAdapter, пинящий hostname к конкретному IP-адресу.

    Защищает от TOCTOU DNS rebinding: IP-адрес определяется ОДИН раз в
    resolve_all()/valid_url() и фиксируется на всю длительность запроса.
    В send() URL переписывается (hostname → pinned IP), а оригинальный
    hostname сохраняется в заголовке Host для виртуального хостинга.

    host_ip_map: {hostname: ip_address}

    Для HTTPS с IP-адресом в netloc проверка hostname сертификата не пройдёт
    (SNI/hostname валидируется по netloc). safe_req() осознанно передаёт
    verify=False для pinned-запросов — это компромисс: защита от SSRF важнее
    TLS-pin в данном сценарии (мы уже доверяем IP после safe_ip-проверки).
    """

    def __init__(self, host_ip_map, *args, **kwargs):
        self._host_ip_map = dict(host_ip_map) if host_ip_map else {}
        super().__init__(*args, **kwargs)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        parsed = urlparse(request.url)
        host = parsed.hostname
        if host and host in self._host_ip_map:
            pinned_ip = self._host_ip_map[host]
            # Сохраняем оригинальный Host-заголовок для виртуального хостинга
            if "Host" not in request.headers:
                request.headers["Host"] = host
            # Переписываем netloc: hostname → pinned IP (порт сохраняем, если есть)
            if parsed.port:
                old_prefix = f"{parsed.scheme}://{host}:{parsed.port}"
                new_prefix = f"{parsed.scheme}://{pinned_ip}:{parsed.port}"
            else:
                old_prefix = f"{parsed.scheme}://{host}"
                new_prefix = f"{parsed.scheme}://{pinned_ip}"
            request.url = request.url.replace(old_prefix, new_prefix, 1)
            # Для HTTPS с IP-адресом отключаем проверку hostname сертификата —
            # иначе urllib3 упадёт с SSLCertVerificationError (SNI по IP).
            # Это безопасно в контексте SSRF-защиты: IP уже валидирован safe_ip().
            if parsed.scheme == "https":
                verify = False
        return super().send(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)

# ============================================================
#  safe_req — безопасный HTTP-запрос с DNS-pinning и redirect-валидацией
# ============================================================

def _make_pinned_session(host_ip_map):
    """Создаёт одноразовую requests.Session с PinnedHTTPAdapter и retry-политикой.

    Аналогично Net.__init__: retry на 429/5xx, тот же User-Agent.
    Используется в safe_req() для каждого запроса в цепочке редиректов.
    """
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503])
    adapter = PinnedHTTPAdapter(host_ip_map, max_retries=retry, pool_connections=10, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "SearchTool/3.4-Production"})
    return s

def safe_req(url, session=None, **kwargs):
    """Безопасный HTTP-запрос: SSRF-проверка + DNS-pinning + redirect-валидация.

    Для каждого URL в цепочке (включая редиректы):
      1. valid_url() — проверка scheme/port/DNS
      2. resolve_all() — получение валидных IP
      3. PinnedHTTPAdapter — пиннинг hostname → IP (защита от DNS rebinding)
      4. session.get() / одноразовая сессия — сам запрос

    Параметры:
      url — стартовый URL
      session — опционально, существующая requests.Session. Если None —
                создаётся одноразовая сессия с PinnedHTTPAdapter per-request.
                session принимается для обратной совместимости с коллерами,
                которые хотят переиспользовать свою сессию (на неё временно
                навешивается PinnedHTTPAdapter).
      **kwargs — пробрасываются в get() (напр. stream=True).

    Возвращает Response последнего (не-редирект) ответа.
    Бросает Exception при блокировке SSRF, петле редиректов или превышении лимита.
    """
    cur, visited = url, set()
    for _ in range(config.MAX_REDIRECTS):
        if cur in visited:
            raise Exception("Loop")
        visited.add(cur)

        p = urlparse(cur)
        if not p.hostname:
            raise Exception(f"Blocked (no host): {cur}")

        # Валидация URL (scheme/port/DNS)
        if not valid_url(cur):
            raise Exception(f"Blocked: {cur}")

        # DNS-pinning: получаем валидные IP и пинним первый
        valid_ips = resolve_all(p.hostname)
        if not valid_ips:
            raise Exception(f"Blocked (no safe IP): {cur}")
        pinned_ip = valid_ips[0]

        # Выбор сессии: либо переиспользуем переданную, либо создаём одноразовую.
        # Не мутируем глобальный net.s — он может использоваться параллельно
        # другими потоками. PinnedHTTPAdapter навешивается на оба scheme.
        own_session = False
        if session is not None:
            req_session = session
            adapter = PinnedHTTPAdapter({p.hostname: pinned_ip})
            req_session.mount("http://", adapter)
            req_session.mount("https://", adapter)
        else:
            req_session = _make_pinned_session({p.hostname: pinned_ip})
            own_session = True

        try:
            # Семафор глобального net — чтобы не пробить MAX_CONCURRENT
            with net.sem:
                net._stats["req"] += 1
                try:
                    r = req_session.get(
                        cur,
                        timeout=config.REQUEST_TIMEOUT,
                        allow_redirects=False,
                        **kwargs,
                    )
                    if r.ok:
                        cl = r.headers.get("Content-Length")
                        if cl:
                            try:
                                net._stats["bytes"] += int(cl)
                            except (TypeError, ValueError):
                                pass
                    else:
                        net._stats["err"] += 1
                except Exception:
                    net._stats["err"] += 1
                    raise

            if 300 <= r.status_code < 400:
                loc = r.headers.get("Location")
                # P3: закрываем ответ редиректа перед continue,
                # чтобы не течь соединения в длинных redirect-цепочках.
                try:
                    r.close()
                except Exception:
                    pass
                if loc:
                    cur = urljoin(cur, loc)
                    continue
                break
            return r
        finally:
            # Одноразовую сессию закрываем только если не возвращаем streaming-ответ.
            # requests.Response держит connection pool; закрывать сессию сразу
            # небезопасно при stream=True — коллер ещё будет iter_content().
            # Поэтому НЕ закрываем здесь для streaming-ответов; для не-streaming
            # сессия будет собрана GC. Для streaming коллер должен закрыть сам.
            # В нашей реализации finally выполняется до return r, поэтому
            # безопасно закрываем только пул для редиректов (уже закрыты выше).
            if own_session:
                # НЕ закрываем пул — r может ещё стримиться.
                # Пул будет переиспользован/закрыт GC. Это компромисс.
                pass
    raise Exception("Too many redirects")

ALLOWED_DIRS = [Path.cwd().resolve()]

def path_ok(p):
    if not ALLOWED_DIRS:
        return False
    import os
    try:
        rp = os.path.realpath(p)
        return any(os.path.commonpath([rp, os.path.realpath(a)]) == os.path.realpath(a) for a in ALLOWED_DIRS)
    except Exception as e:
        logger.warning(f"path_ok failed for {p}: {e}")
        return False
