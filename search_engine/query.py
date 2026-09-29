"""Хайлтын асуулгыг задлах.

Дэмжих бичиглэл:
    ном сан            -> хоёр үг (аль аль нь орсон баримтыг эхэлж хайна)
    "их эзэн хаан"     -> яг дарааллаараа орсон хэллэг
    -түүх              -> энэ үг орсон баримтыг хасна
    title:монгол       -> зөвхөн тухайн талбарт хайна
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .tokenizer import tokenize

_CLAUSE_RE = re.compile(r'(-)?(?:(\w+):)?(?:"([^"]*)"?|([^\s"]+))', re.UNICODE)


@dataclass
class Clause:
    terms: List[str]
    offsets: List[int] = field(default_factory=lambda: [0])
    phrase: bool = False
    negate: bool = False
    field: Optional[str] = None


def _term_clauses(text: str, negate: bool, fld: Optional[str]) -> List[Clause]:
    return [Clause([t.text], [0], negate=negate, field=fld) for t in tokenize(text)]


def parse_query(query: str, fields: Iterable[str]) -> List[Clause]:
    known_fields = set(fields)
    clauses: List[Clause] = []
    for m in _CLAUSE_RE.finditer(query or ""):
        neg, fld, phrase, word = m.groups()
        negate = bool(neg)
        if fld and fld.casefold() not in known_fields:
            # "http:" гэх мэт талбар биш зүйлийг энгийн үг гэж үзнэ
            clauses.extend(_term_clauses(fld, negate, None))
            fld = None
        fld = fld.casefold() if fld else None
        if phrase is not None:
            tokens = tokenize(phrase)
            if len(tokens) > 1:
                clauses.append(
                    Clause(
                        [t.text for t in tokens],
                        [t.position for t in tokens],
                        phrase=True,
                        negate=negate,
                        field=fld,
                    )
                )
                continue
            word = phrase
        clauses.extend(_term_clauses(word or "", negate, fld))
    return clauses
