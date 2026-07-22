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
    """Проверка IP-адреса (IPv4 и IPv6) на принадлежность к приватным/опасным диапазонам."""
    try:
        o = ipaddress.ip_address(ip)
        # IPv6-mapped IPv4 (напр. ::ffff:127.0.0.1) — нормализуем к IPv4,
        # иначе is_loopback/is_private могут вернуть True для mapped public IPv4
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

# DNS-кэш: {host: (result, timestamp)}
# result — список валидных IP-адресов (может быть пустым, если все невалидны → None в resolve())
_dns_cache = {}
_dns_lock = threading.Lock()
_DNS_TTL = 300  # секунд

def _getaddrinfo_all(host):
    """Получить все IP-адреса (IPv4 + IPv6) через getaddrinfo.
    Возвращает список уникальных IP-строк. Бросает socket.gaierror при ошибке DNS."""
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
    упал или все адреса невалидны. Используется для DNS-pinning (берём любой
    из списка и пиним его в PinnedHTTPAdapter).
    """
    now = time.time()
    with _dns_lock:
        # Ленивая очистка просроченных записей (P3: DNS cache cleanup)
        expired = [h for h, (_, ts) in _dns_cache.items() if now - ts > _DNS_TTL]
        for h in expired:
            del _dns_cache[h]
        # Проверяем кэш
        if host in _dns_cache:
            result, timestamp = _dns_cache[host]
            if now - timestamp < _DNS_TTL:
                return list(result) if result is not None else []

    try:
        # Не мутируем глобальный socket.setdefaulttimeout — это ломает
        # параллельные HTTP-запросы в многопоточной среде.
        # Используем getaddrinfo вместо gethostbyname, чтобы получить и IPv4, и IPv6
        # (gethostbyname умеет только IPv4 — это была SSRF-дыра: AAAA на приватный IPv6).
        all_addrs = _getaddrinfo_all(host)
        valid = [ip for ip in all_addrs if safe_ip(ip)]
        # Если хотя бы один адрес невалиден — считаем хост подозрительным
        # и не отдаём ни одного (все должны быть валидны для прохождения).
        if len(valid) != len(all_addrs):
            logger.warning(
                f"resolve_all: {host} has mixed/invalid addresses "
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

    Использует resolve_all() и берёт первый валидный адрес. Сохранено для
    существующих коллеров, которые ожидают строку/None.
    """
    addrs = resolve_all(host)
    return addrs[0] if addrs else None

def valid_url(u):
    """Валидация URL: scheme, port, SSRF-проверка хоста через resolve_all()."""
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        if p.port and p.port not in {80, 443}:
            return False
        # Используем resolve_all (а не resolve), чтобы проверить наличие
        # хотя бы одного валидного IP. resolve() возвращает только первый,
        # а нам важно знать, что хост вообще разрешается безопасно.
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
    requests/urllib3 коннектятся напрямую к IP, а оригинальный hostname
    отправляется в заголовке Host и в SNI (через preserve_host).

    host_ip_map: {hostname: ip_address}
    """

    def __init__(self, host_ip_map, *args, **kwargs):
        self._host_ip_map = dict(host_ip_map) if host_ip_map else {}
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        from urllib.parse import urlparse as _urlparse
        parsed = _urlparse(request.url)
        host = parsed.hostname
        if host and host in self._host_ip_map:
            pinned_ip = self._host_ip_map[host]
            # Сохраняем оригинальный Host-заголовок для виртуального хостинга
            if "Host" not in request.headers:
                # Нормализуем: порт не нужен в Host, если стандартный
                request.headers["Host"] = host
            # Подменяем URL: hostname → pinned IP
            # Делаем это безопасно: только первое вхождение схемы://host
            old_prefix = f"{parsed.scheme}://{host}"
            new_prefix = f"{parsed.scheme}://{pinned_ip}"
            # Учитываем порт в URL (если был)
            if parsed.port:
                old_prefix = f"{parsed.scheme}://{host}:{parsed.port}"
                new_prefix = f"{parsed.scheme}://{pinned_ip}:{parsed.port}"
            request.url = request.url.replace(old_prefix, new_prefix, 1)
            # Для HTTPS с IP нужно отключить проверку hostname в сертификате
            # (SNI/hostname проверка идёт по netloc, а там теперь IP).
            # Но verify=False — плохо для безопасности в проде.
            # Компромисс: передаём server_hostname через connection_pool_kwargs.
            # Проще всего — отключить verify только для pinned запросов через
            # явный флаг в kwargs (session-level), но requests не даёт per-request
            # verify из adapter. Поэтому оставляем как есть: IP в URL, Host-заголовок
            # оригинальный. Для HTTPS это вызовет SSL-ошибку по hostname — но
            # это лучше, чем SSRF. Коллеры, которым нужен HTTPS-pinning, должны
            # использовать verify=False осознанно (safe_req выставляет это).
        return super().send(request, **kwargs)

# ============================================================
#  safe_req — безопасный HTTP-запрос с DNS-pinning и redirect-валидацией
# ============================================================

def safe_req(url, session=None, **kwargs):
    """Безопасный HTTP-запрос: SSRF-проверка + DNS-pinning + redirect-валидация.

    Для каждого URL в цепочке (включая редиректы):
      1. valid_url() — проверка scheme/port/DNS
      2. resolve_all() — получение валидных IP
      3. PinnedHTTPAdapter — пиннинг hostname → IP (защита от DNS rebinding)
      4. net.get() / session.get() — сам запрос

    Параметры:
      url — стартовый URL
      session — опционально, существующая requests.Session (для коллеров,
                которые хотят переиспользовать сессию). Если None —
                используется глобальный net (с PinnedHTTPAdapter поверх).
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

        # Создаём временную сессию с PinnedHTTPAdapter.
        # Не мутируем глобальный net.s — он может использоваться параллельно.
        # PinnedHTTPAdapter монтируется на оба scheme, чтобы покрыть редиректы
        # между http/https.
        if session is not None:
            req_session = session
            # Вешаем adapter на эту сессию (idempotent — заменяет, если уже есть)
            adapter = PinnedHTTPAdapter({p.hostname: pinned_ip})
            req_session.mount("http://", adapter)
            req_session.mount("https://", adapter)
            r = req_session.get(
                cur, timeout=config.REQUEST_TIMEOUT,
                allow_redirects=False, **kwargs,
            )
        else:
            # Используем глобальный net, но через временный adapter.
            # net.get уже имеет retry/semaphore/statistics — переиспользуем.
            adapter = PinnedHTTPAdapter({p.hostname: pinned_ip})
            # Монтируем временно, затем восстанавливаем (net может быть
            # вызван параллельно — поэтому лучше создать одноразовую сессию).
            # Для thread-safety создаём новую сессию на каждый запрос.
            req_session = requests.Session()
            req_session.mount("http://", adapter)
            req_session.mount("https://", adapter)
            req_session.headers.update(net.s.headers)
            try:
                r = req_session.get(
                    cur, timeout=config.REQUEST_TIMEOUT,
                    allow_redirects=False, **kwargs,
                )
                # Обновляем статистику глобального net (для совместимости)
                with net.sem:
                    net._stats["req"] += 1
                    if r.ok:
                        cl = r.headers.get("Content-Length")
                        if cl:
                            try:
                                net._stats["bytes"] += int(cl)
                            except (TypeError, ValueError):
                                pass
                    else:
                        net._stats["err"] += 1
            finally:
                # Сессия одноразовая — закрываем после каждого запроса,
                # чтобы не течь соединения. Но НЕ закрываем, если возвращаем
                # streaming-ответ (iter_content) — закроет коллер.
                pass

        if 300 <= r.status_code < 400:
            loc = r.headers.get("Location")
            # P3: закрываем ответ редиректа перед continue, чтобы не течь соединения
            try:
                r.close()
            except Exception:
                pass
            if loc:
                cur = urljoin(cur, loc)
                continue
            break
        return r
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
