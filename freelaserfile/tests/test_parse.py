from freelaserfile_crawler.api import FreeLaserFileApi, detail_url
from freelaserfile_crawler.http_client import PoliteClient
from freelaserfile_crawler.parse import design_image_urls, record_from_payload


def test_detail_url():
    assert detail_url("hello-summer-floral-round-door-hanger-nt5og") == (
        "https://freelaserfile.com/design/hello-summer-floral-round-door-hanger-nt5og"
    )


def test_excludes_similar_images():
    api = FreeLaserFileApi(PoliteClient())
    payload = {
        "slug": "demo",
        "title": "Demo",
        "category": {"name": "Art & Decor"},
        "tags": [{"tag": {"name": "laser cut"}}],
        "images": [
            {
                "sortOrder": 0,
                "watermarkedUrl": "watermarked/main.webp",
                "mediumUrl": "medium/main.webp",
            }
        ],
        "similar": [
            {
                "slug": "other",
                "images": [{"watermarkedUrl": "watermarked/side.webp"}],
            }
        ],
    }
    urls = design_image_urls(api, payload)
    assert len(urls) == 1
    # Prefer medium over watermarked (CDN originals often 404).
    assert urls[0].endswith("medium/main.webp")
    assert "side.webp" not in urls[0]
    rec = record_from_payload(api, payload, source_page_url="https://freelaserfile.com/")
    assert "side.webp" not in rec.image_urls
    assert rec.categories == "Art & Decor"
    assert "laser cut" in rec.tags
