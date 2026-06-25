"""Tests for tools.py — extract(), jina(), is_binary()"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import extract, jina, is_binary


# ═══════════════════════════════════════════════════════════════
#  extract()
# ═══════════════════════════════════════════════════════════════

def test_extract_basic_html():
    html_text = "<html><body><p>Hello world</p></body></html>"
    result = extract(html_text)
    assert "Hello world" in result


def test_extract_removes_scripts_and_styles():
    html_text = """
    <html><head><style>body{color:red}</style></head>
    <body><script>alert('xss')</script><p>Visible text</p></body></html>
    """
    result = extract(html_text)
    assert "Visible text" in result
    assert "alert" not in result
    assert "color" not in result


def test_extract_main_tag():
    html_text = """
    <html><body>
    <nav>Nav content</nav>
    <main><p>Main content</p></main>
    </body></html>
    """
    result = extract(html_text)
    assert "Main content" in result
    assert "Nav content" not in result


def test_extract_article_tag():
    html_text = """
    <html><body>
    <article><h1>Article Title</h1><p>Article body</p></article>
    </body></html>
    """
    result = extract(html_text)
    assert "Article Title" in result
    assert "Article body" in result


def test_extract_collapses_whitespace():
    html_text = "<html><body><p>Line  one</p>\n\n\n<p>Line  two</p></body></html>"
    result = extract(html_text)
    assert "Line one" in result
    assert "Line two" in result
    # Должно быть не более одной пустой строки подряд
    assert "\n\n\n" not in result


def test_extract_html_entities():
    html_text = "<html><body><p>&lt;tag&gt; &amp; &quot;quote&quot;</p></body></html>"
    result = extract(html_text)
    assert "<tag>" in result
    assert "&" in result
    assert '"quote"' in result


def test_extract_empty_html():
    result = extract("")
    assert result == ""


def test_extract_no_body():
    html_text = "<html><head><title>Title</title></head></html>"
    result = extract(html_text)
    assert "Title" in result


def test_extract_iframe_nav_header_footer_aside_removed():
    html_text = """
    <html><body>
    <header>Header</header>
    <nav>Nav</nav>
    <aside>Aside</aside>
    <footer>Footer</footer>
    <iframe>IFrame</iframe>
    <p>Keep me</p>
    </body></html>
    """
    result = extract(html_text)
    assert "Keep me" in result
    assert "Header" not in result
    assert "Nav" not in result
    assert "Aside" not in result
    assert "Footer" not in result
    assert "IFrame" not in result


# ═══════════════════════════════════════════════════════════════
#  jina()
# ═══════════════════════════════════════════════════════════════

class FakeResponse:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


class FakeNet:
    def __init__(self, response=None):
        self._response = response
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self._response is not None:
            return self._response
        raise Exception("network error")


def test_jina_success():
    long_text = "A" * 101
    net = FakeNet(FakeResponse(long_text, 200))
    result = jina("https://example.com", net)
    assert result == long_text
    assert net.calls[0][0] == "https://r.jina.ai/https://example.com"


def test_jina_too_short():
    """Ответ < 100 символов — считаем неудачным"""
    net = FakeNet(FakeResponse("short", 200))
    result = jina("https://example.com", net)
    assert result is None


def test_jina_non_200():
    net = FakeNet(FakeResponse("Forbidden", 403))
    result = jina("https://example.com", net)
    assert result is None


def test_jina_network_error():
    net = FakeNet(None)
    result = jina("https://example.com", net)
    assert result is None


# ═══════════════════════════════════════════════════════════════
#  is_binary()
# ═══════════════════════════════════════════════════════════════

def test_is_binary_text_extension():
    """Файлы с текстовыми расширениями — не бинарные"""
    assert is_binary("/tmp/test.py") is False
    assert is_binary("/tmp/test.md") is False
    assert is_binary("/tmp/test.txt") is False
    assert is_binary("/tmp/test.json") is False
    assert is_binary("/tmp/test.html") is False
    assert is_binary("/tmp/test.css") is False


def test_is_binary_by_content():
    """Файл без текстового расширения, содержит null bytes — бинарный"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"hello\x00world")
        path = f.name
    try:
        assert is_binary(path) is True
    finally:
        os.unlink(path)


def test_is_binary_text_content():
    """Файл без текстового расширения, но без null bytes — текстовый"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".unknown", delete=False) as f:
        f.write(b"just plain text content")
        path = f.name
    try:
        assert is_binary(path) is False
    finally:
        os.unlink(path)


def test_is_binary_empty_file():
    """Пустой файл — текстовый"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".empty", delete=False) as f:
        path = f.name
    try:
        assert is_binary(path) is False
    finally:
        os.unlink(path)


def test_is_binary_extension_case_insensitive():
    assert is_binary("/tmp/test.PY") is False
    assert is_binary("/tmp/test.JS") is False


def test_is_binary_unreadable_file():
    """Если файл нельзя прочитать — считаем бинарным"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".noaccess", delete=False) as f:
        f.write(b"content")
        path = f.name
    try:
        os.chmod(path, 0o000)
        assert is_binary(path) is True
    finally:
        os.chmod(path, 0o644)
        os.unlink(path)
