from robotime_crawler.parse import record_from_product


def test_variants_not_split_and_brand_not_designer():
    product = {
        "id": 1,
        "handle": "demo-product",
        "title": "Demo Puzzle XYZ01",
        "vendor": "ROKR",
        "product_type": "3D Wooden Puzzle",
        "tags": "Mechanical",
        "variants": [
            {"title": "A", "price": "10.00", "compare_at_price": "12.00", "available": False, "sku": "A"},
            {"title": "B", "price": "11.00", "compare_at_price": None, "available": True, "sku": "B"},
        ],
        "images": [{"src": "https://cdn.shopify.com/demo.jpg"}],
    }
    rec = record_from_product(
        product,
        collection_source_page_url="https://www.robotimeonline.com/collections/all",
        currency="USD",
        enrich={"brand": "ROKR", "designer": "", "finalUrl": "https://www.robotimeonline.com/products/demo-product"},
    )
    assert rec.variantCount == "2"
    assert rec.price == "11.00"
    assert rec.sku == "B"
    assert rec.brand == "ROKR"
    assert rec.designer == ""
    assert rec.availabilityStatus == "inStock"
    assert rec.sourceUrl.endswith("/products/demo-product")
