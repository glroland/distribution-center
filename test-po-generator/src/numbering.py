"""Generates randomized PO metadata: PO numbers, dates, terms, and vendor identities."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from faker import Faker

from src.models import Company

fake = Faker()

PAYMENT_TERMS = ["Net 15", "Net 30", "Net 45", "Net 60", "Due on Receipt"]


def random_po_number() -> str:
    year = datetime.now().year
    return f"PO-{year}-{random.randint(100000, 999999)}"


def random_issue_date() -> str:
    days_ago = random.randint(0, 21)
    issue_date = datetime.now() - timedelta(days=days_ago)
    return issue_date.strftime("%B %d, %Y")


def random_payment_terms() -> str:
    return random.choice(PAYMENT_TERMS)


def random_vendor() -> Company:
    return Company(
        name=fake.company(),
        address_lines=[fake.street_address(), f"{fake.city()}, {fake.state_abbr()} {fake.zipcode()}"],
        phone=fake.phone_number(),
        email=fake.company_email(),
    )


def random_buyer_contact() -> str:
    return fake.name()


def random_ship_to(buyer: Company) -> list[str]:
    # Most of the time ship-to is the buyer's own address; occasionally a separate
    # warehouse/site address for extra realism.
    if random.random() < 0.75:
        return list(buyer.address_lines)
    return [fake.street_address(), f"{fake.city()}, {fake.state_abbr()} {fake.zipcode()}"]
