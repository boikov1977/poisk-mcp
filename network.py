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
                    self._stats["bytes"] += len(r.content)
                else: 
                    self._stats["err"] += 1
                return r
            except Exception:
                self._stats["err"] += 1
                raise
    
    def close(self):
        self.s.close()

net = Net()

# DNS & Security
def safe_ip(ip):
    try:
        o = ipaddress.ip_address(ip)
        return not any([o.is_private, o.is_loopback, o.is_reserved, o.is_multicast, o.is_link_local, str(o) in {"169.254.169.254"}])
    except: 
        return False

_dns_cache = {}
_dns_lock = threading.Lock()

def resolve(host):
    with _dns_lock:
        if host in _dns_cache:
            result, timestamp = _dns_cache[host]
            if time.time() - timestamp < 300: return result
    try:
        socket.setdefaulttimeout(3)
        ip = socket.gethostbyname(host)
        socket.setdefaulttimeout(None)
        res = ip if safe_ip(ip) else None
        with _dns_lock:
            _dns_cache[host] = (res, time.time())
        return res
    except Exception:
        socket.setdefaulttimeout(None)
        with _dns_lock:
            _dns_cache[host] = (None, time.time())
        return None

def valid_url(u):
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.hostname: return False
        if p.port and p.port in {22, 23, 25, 53, 110, 143, 993, 995}: return False
        return resolve(p.hostname) is not None
    except: 
        return False

def safe_req(url):
    cur, visited = url, set()
    for _ in range(config.MAX_REDIRECTS):
        if cur in visited: raise Exception("Loop")
        visited.add(cur)
        if not valid_url(cur): raise Exception(f"Blocked: {cur}")
        r = net.get(cur, stream=True)
        if 300 <= r.status_code < 400:
            loc = r.headers.get("Location")
            if loc:
                cur = urljoin(cur, loc)
                continue
            break
        return r
    raise Exception("Too many redirects")

ALLOWED_DIRS = [Path.cwd().resolve()] 

def path_ok(p):
    if not ALLOWED_DIRS: return False
    import os
    try:
        rp = os.path.realpath(p)
        return any(os.path.commonpath([rp, os.path.realpath(a)]) == os.path.realpath(a) for a in ALLOWED_DIRS)
    except: 
        return False