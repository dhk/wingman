from wingman.application.corpus import extract_document

HTML = """<html><head><title>Streaming Lessons</title>
<style>body { color: red; }</style><script>alert("ignored")</script></head>
<body><h1>What I learned migrating to streaming</h1>
<p>The billing pipeline took three months.</p>
<p>Latency dropped from a day to minutes.</p></body></html>"""


def test_html_extraction_strips_markup_and_finds_title() -> None:
    title, body = extract_document(HTML, "post.html", ".html")
    assert title == "Streaming Lessons"
    assert "The billing pipeline took three months." in body
    assert "Latency dropped from a day to minutes." in body
    assert "alert" not in body
    assert "color: red" not in body


def test_html_title_falls_back_to_h1() -> None:
    html = "<html><body><h1>Only Heading</h1><p>Text.</p></body></html>"
    title, _ = extract_document(html, "post.html", ".html")
    assert title == "Only Heading"


def test_markdown_title_from_first_heading() -> None:
    title, body = extract_document("# My Essay\n\nBody text.\n", "essay.md", ".md")
    assert title == "My Essay"
    assert body.startswith("# My Essay")


def test_plain_text_title_falls_back_to_filename() -> None:
    title, _ = extract_document("\n\n\n", "notes.txt", ".txt")
    assert title == "notes.txt"
