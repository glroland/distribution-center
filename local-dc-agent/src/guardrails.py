"""Lightweight, dependency-free heuristic scan for indirect prompt injection
in text that originated from an untrusted purchase-order PDF.

This is a stopgap, not a substitute for real detection: on OpenShift AI,
front the two OpenAI-compatible endpoints this service calls (see
fulfillment.py and order_extraction.py, both routed through
settings.OPENAI_BASE_URL) with TrustyAI's FMS Guardrails Orchestrator for
model-based prompt-injection/PII/HAP detection. What lives here instead is
the piece a text detector *can't* do on its own: recognizing that a given
string is business-sensitive (an extracted ship_to address, a line-item
description) and gating what happens next around that -- see
fulfillment.py's use of scan_order_fields() and redact().

Regexes are intentionally simple substring/phrase matches, not an attempt
at exhaustive coverage -- good enough to catch the obvious "ignore your
instructions" family of payloads a PO PDF can carry, including ones a human
skimming the rendered PDF would never see (e.g. white-on-white text, which
Docling's text-layer extraction reads just the same as any other text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ProcessOrderResult


@dataclass
class GuardrailFinding:
    pattern: str
    excerpt: str


_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in [
        r"ignore (all|any|the)?\s*(previous|prior|above|earlier)\s+instructions",
        r"disregard (all|any|the)?\s*(previous|prior|above|earlier)",
        r"new\s+instructions\s*:",
        r"system\s*(prompt|message)\s*:",
        r"\byou are now\b",
        r"\bact as (a|an)\b.{0,40}\b(agent|assistant|system)\b",
        r"\bdo not (tell|inform|notify|alert)\b.{0,40}\b(supervisor|human|user)\b",
        r"\boverride\b.{0,20}\b(polic(y|ies)|instructions)\b",
        r"\bcall\s+`?(wms|robot|shipping|supervisor|label)__\w+`?\s+with",
        r"\bset\s+(the\s+)?ship[_ -]?to\b",
        r"\breveal (your|the) (system\s+)?prompt\b",
        r"\binclude (the|your) (api[_ -]?key|system prompt|credentials)\b",
        r"<!--.*?-->",  # markdown/HTML comments: invisible in a rendered preview
    ]
]

_EXCERPT_PADDING = 20


def scan(text: str | None) -> list[GuardrailFinding]:
    """Scans one string for injection-style phrasing. Returns one finding per
    matched pattern (not per occurrence), each with a short excerpt for logs/UI."""
    if not text:
        return []
    findings = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            start = max(match.start() - _EXCERPT_PADDING, 0)
            end = min(match.end() + _EXCERPT_PADDING, len(text))
            excerpt = text[start:end].strip().replace("\n", " ")
            findings.append(GuardrailFinding(pattern=pattern.pattern, excerpt=excerpt))
    return findings


def scan_order_fields(order: ProcessOrderResult) -> list[GuardrailFinding]:
    """Scans every free-text field the extraction LLM pulled out of the
    untrusted PDF -- the only channel injected instructions can ride into the
    fulfillment loop's own prompt (see fulfillment.py's _order_message(),
    which serializes exactly these fields into the conversation)."""
    texts = [order.po_number, order.vendor_name, order.buyer_name, order.ship_to, order.payment_terms]
    texts += [item.description for item in order.line_items]
    findings: list[GuardrailFinding] = []
    for text in texts:
        findings.extend(scan(text))
    return findings


def redact(text: str) -> tuple[str, list[GuardrailFinding]]:
    """Redacts matched spans from tool-call result text before it's replayed
    back into the model's own conversation history, so a compromised or
    adversarial downstream MCP response can't steer later turns. Returns the
    (possibly redacted) text alongside whatever was found."""
    findings = scan(text)
    if not findings:
        return text, findings
    redacted = text
    for pattern in _INJECTION_PATTERNS:
        redacted = pattern.sub("[REDACTED: possible injected instruction]", redacted)
    return redacted, findings
