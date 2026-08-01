"""Registry of company PO templates. Each template module exposes a COMPANY
identity and a render(po, output_path) function."""

from __future__ import annotations

import random
from dataclasses import dataclass
from types import ModuleType

from src.templates import (
    acme_industrial,
    blue_ridge_supply,
    civic_municipal,
    crestline_construction,
    evergreen_office,
    golden_state_foods,
    harborview_logistics,
    ironclad_fabrication,
    meridian_healthcare,
    northwind_traders,
    pacific_tech_supply,
    summit_manufacturing,
)


@dataclass(frozen=True)
class TemplateEntry:
    module: ModuleType

    @property
    def company(self):
        return self.module.COMPANY

    def render(self, po, output_path):
        self.module.render(po, output_path)


TEMPLATE_REGISTRY: list[TemplateEntry] = [
    TemplateEntry(acme_industrial),
    TemplateEntry(northwind_traders),
    TemplateEntry(blue_ridge_supply),
    TemplateEntry(summit_manufacturing),
    TemplateEntry(harborview_logistics),
    TemplateEntry(evergreen_office),
    TemplateEntry(ironclad_fabrication),
    TemplateEntry(meridian_healthcare),
    TemplateEntry(crestline_construction),
    TemplateEntry(pacific_tech_supply),
    TemplateEntry(golden_state_foods),
    TemplateEntry(civic_municipal),
]


def get_random_template() -> TemplateEntry:
    return random.choice(TEMPLATE_REGISTRY)
