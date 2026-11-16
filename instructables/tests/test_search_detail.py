from instructables_crawler.detail import category_path_from_classifications, is_ad_image_url


def test_category_path():
    path = category_path_from_classifications(
        [
            {
                "title": "Workshop",
                "channels": [{"title": "Laser Cutting"}],
            }
        ]
    )
    assert path == "Workshop > Laser Cutting"


def test_ad_image_filter():
    assert is_ad_image_url("https://doubleclick.net/foo.jpg")
    assert not is_ad_image_url(
        "https://content.instructables.com/F0M/4H03/IJ0EG6CC/F0M4H03IJ0EG6CC.jpg"
    )
