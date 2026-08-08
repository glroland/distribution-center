from src.seed_data import ExpectedStock, load_catalog, load_expected_stock


def test_load_catalog_has_every_product():
    catalog = load_catalog()
    assert "SKU-1001" in catalog
    assert catalog["SKU-1001"] == "Wrench"


def test_load_expected_stock_covers_full_catalog():
    catalog = load_catalog()
    stock = load_expected_stock()
    assert set(stock) == set(catalog)


def test_catalog_only_sku_has_zero_stock():
    # SKU-1019/1020 are in products.csv but not seeded in either
    # local-wms-api's or local-inventory-robot-api's data -- see
    # CLAUDE.md's "Shared demo data" section.
    stock = load_expected_stock()
    assert stock["SKU-1019"].on_hand_qty == 0
    assert stock["SKU-1019"].shelf_qty == 0
    assert stock["SKU-1019"].expected_fulfillable(5) == 0


def test_expected_fulfillable_is_capped_by_the_scarcer_store():
    stock = ExpectedStock(sku="X", on_hand_qty=5, shelf_qty=100)
    assert stock.expected_fulfillable(10) == 5

    stock = ExpectedStock(sku="Y", on_hand_qty=100, shelf_qty=5)
    assert stock.expected_fulfillable(10) == 5


def test_expected_fulfillable_never_exceeds_requested_qty():
    stock = ExpectedStock(sku="Z", on_hand_qty=100, shelf_qty=100)
    assert stock.expected_fulfillable(3) == 3
