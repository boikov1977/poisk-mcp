# tools.py
# Модуль содержит функции, импортируемые из server.py

import os
from bs4 import BeautifulSoup
import html
import re
import logging

logger = logging.getLogger("SearchTool")

# Импортируем wmo из tools.py для использования в server.py
WMO = {
    0: "Ясно", 1: "Ясно", 2: "Облачно", 3: "Пасмурно",
    45: "Туман", 61: "Дождь", 63: "Дождь", 65: "Ливень",
    71: "Снег", 73: "Снег", 75: "Снегопад", 80: "Ливень", 95: "Гроза"
}

def wmo(c):
    """Получить описание погоды по WMO коду"""
    return WMO.get(c, f"?({c})")

def jina(url, net):
    """Получить markdown-контент через Jina Reader API"""
    try:
        r = net.get(f"https://r.jina.ai/{url}", headers={"Accept": "text/markdown"})
        if r.status_code == 200 and len(r.text.strip()) > 100:
            return r.text.strip()
    except Exception as e:
        # ИСПРАВЛЕНО: ловим только сетевые ошибки, не ошибки программирования
        logger.debug(f"Jina API failed: {type(e).__name__}: {e}")
        return None

def extract(html_text):
    """Извлечь основной текст из HTML"""
    soup = BeautifulSoup(html_text, "html.parser")
    # Удаляем ненужные элементы
    for t in soup(["script", "style", "iframe", "nav", "header", "footer", "aside"]):
        t.decompose()

    # Ищем основной контент
    m = soup.find("main") or soup.find("article") or soup.body
    text = (m or soup).get_text(separator="\n", strip=True)

    # Очистка текста
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = text.split("\n")
    cleaned_lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw_lines]
    out = []
    prev_blank = False
    for ln in cleaned_lines:
        if ln:
            out.append(ln)
            prev_blank = False
        else:
            if not prev_blank:
                out.append("")
                prev_blank = True
    return "\n".join(out).strip()

def is_binary(filepath):
    """Проверить, является ли файл бинарным.
    ИСПРАВЛЕНО: сначала проверяем расширение, затем содержимое.
    UTF-16/UTF-32 файлы содержат нулевые байты, но являются текстовыми.
    """
    # Сначала проверяем по расширению — текстовые файлы пропускаем
    text_extensions = {
        '.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.xml',
        '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.log', '.csv',
        '.html', '.htm', '.css', '.sql', '.sh', '.bash', '.bat', '.cmd',
        '.rb', '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs', '.php',
        '.r', '.m', '.swift', '.kt', '.scala', '.pl', '.pm', '.lua',
    }
    ext = os.path.splitext(filepath)[1].lower()
    if ext in text_extensions:
        return False

    # Fallback: проверяем наличие нулевых байтов
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(8192)
            if not chunk:  # Пустой файл — текстовый
                return False
            # Проверяем первые 8KB на наличие null bytes
            return b'\x00' in chunk
    except:
        return True
