# Inventory totals fixture

This small fixture defines expected behavior for invoice aggregation.

- Each item's unit_price_cents is multiplied by its positive quantity.
- merge_items returns items sorted by SKU and merges repeated SKUs by adding quantities.
- Repeated SKUs must have the same unit price; otherwise it raises ValueError.
- Inputs are not mutated.

Run the assertions with python3 -m unittest discover -s tests -v.
