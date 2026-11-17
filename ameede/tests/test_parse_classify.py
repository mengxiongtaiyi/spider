from pathlib import Path

from ameede_crawler.classify import classify_url
from ameede_crawler.parse import parse_detail_page, parse_list_page


FIXTURES = Path(__file__).resolve().parents[1].parent  # unused; use /tmp fixtures from live fetch in CI-less env


def test_classify():
    assert classify_url("https://www.ameede.com/pen-holder-e0025665-file-cdr-and-dxf-free-vector-download-for-laser-cut/") == "detail"
    assert classify_url("https://www.ameede.com/download/e0025665/") == "download"
    assert classify_url("https://doubleclick.net/foo") == "ad"
    assert classify_url("https://www.ameede.com/amee/vector-cnc-laser/") == "unknown"


def test_list_and_detail_fixtures():
    list_html = Path("/tmp/ameede_list.html")
    detail_html = Path("/tmp/ameede_detail.html")
    if not list_html.exists() or not detail_html.exists():
        return
    items, max_page = parse_list_page(list_html.read_text(errors="ignore"), "https://www.ameede.com/amee/vector-cnc-laser/")
    assert len(items) >= 10
    assert max_page and max_page >= 1000
    assert all(i.entry_type == "detail" for i in items)

    rec = parse_detail_page(
        detail_html.read_text(errors="ignore"),
        "https://www.ameede.com/pen-holder-e0025665-file-cdr-and-dxf-free-vector-download-for-laser-cut/",
        "https://www.ameede.com/amee/vector-cnc-laser/",
    )
    assert rec.entry_type == "detail"
    assert "E0025665" in rec.title.upper() or rec.project_code == "E0025665"
    assert rec.cover_url.startswith("https://www.ameede.com/wp-content/uploads/")
    assert "download" in rec.download_url
