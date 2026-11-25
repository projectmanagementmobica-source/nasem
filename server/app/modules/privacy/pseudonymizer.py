"""Mask personal details before text leaves the server; restore them in the model's output.

The token map lives only in memory for one request and is never persisted or sent anywhere.
Detection is best-effort (regex + names already known for this user): it REDUCES what leaves
Egypt, it does not guarantee zero personal data. New, never-seen names pass through once.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("إيميل", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("رقم_قومي", re.compile(r"(?<![\d٠-٩])[23]\d{13}(?![\d٠-٩])")),
    ("رقم", re.compile(r"(?:\+?20|0)1[0125][\s-]?\d{3,4}[\s-]?\d{4}(?![\d٠-٩])|[٠١][٠١٢٥][٠-٩]{8}")),
    ("مبلغ", re.compile(r"\d[\d,.]*\s*(?:جنيه|ج\.م|EGP|دولار|\$|ريال|يورو)")),
]
_TOKEN_RE = re.compile(r"\[(?:شخص|رقم|إيميل|رقم_قومي|مبلغ)_\d+\]")

TOKEN_INSTRUCTION = (
    "Placeholders like [شخص_1], [رقم_1], [مبلغ_1] stand for private details hidden by the app. "
    "Treat each as the real thing it names and copy them EXACTLY as written wherever you refer to "
    "them (including entity_name and attribute_value). Never guess what they hide."
)


class Pseudonymizer:
    def __init__(self, known_names: Iterable[str] = ()) -> None:
        self._by_value: dict[tuple[str, str], str] = {}
        self._by_token: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        names = sorted({n for n in known_names if n and n != "user" and len(n) >= 2}, key=len, reverse=True)
        self._name_re = (
            re.compile(
                r"(?<![^\W\d_])([وف]?[بلك]?)(" + "|".join(re.escape(n) for n in names) + r")(?![^\W\d_])",
                re.IGNORECASE,
            )
            if names
            else None
        )

    def _token(self, kind: str, value: str) -> str:
        key = (kind, value.lower())
        if key not in self._by_value:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            token = f"[{kind}_{self._counters[kind]}]"
            self._by_value[key] = token
            self._by_token[token] = value
        return self._by_value[key]

    def _replacer(self, kind: str) -> Callable[[re.Match[str]], str]:
        def _sub(match: re.Match[str]) -> str:
            return self._token(kind, match.group(0))

        return _sub

    def mask(self, text: str) -> str:
        out = text
        for kind, pattern in _RULES:
            out = pattern.sub(self._replacer(kind), out)
        if self._name_re is not None:
            out = self._name_re.sub(lambda m: m.group(1) + self._token("شخص", m.group(2)), out)
        return out

    def unmask(self, value: Any) -> Any:
        if isinstance(value, str):
            return _TOKEN_RE.sub(lambda m: self._by_token.get(m.group(0), m.group(0)), value)
        if isinstance(value, list):
            return [self.unmask(v) for v in value]
        if isinstance(value, dict):
            return {k: self.unmask(v) for k, v in value.items()}
        return value

    @property
    def hidden_count(self) -> int:
        return len(self._by_token)
