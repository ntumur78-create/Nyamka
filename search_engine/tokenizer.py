"""Текстийг үг (token) болгон хуваах, хэвийн болгох туслах функцууд."""

import re
import unicodedata
from typing import List, NamedTuple

# \w нь Юникод үсгийг бүгдийг (кирилл, ө, ү, латин, тоо) хамарна.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


class Token(NamedTuple):
    text: str  # хэвийн болгосон үг
    position: int  # текст доторх үгийн дугаар
    start: int  # эх текст дэх эхлэх тэмдэгтийн байрлал
    end: int  # эх текст дэх төгсгөлийн байрлал


def normalize(text: str) -> str:
    """Юникодыг NFC болгож, том жижиг үсгийн ялгааг арилгана."""
    return unicodedata.normalize("NFC", text).casefold()


def tokenize(text: str) -> List[Token]:
    """Текстийг байрлалын мэдээлэлтэй үгс болгон хуваана."""
    if not text:
        return []
    return [
        Token(normalize(m.group()), i, m.start(), m.end())
        for i, m in enumerate(_WORD_RE.finditer(text))
    ]


def edit_distance(a: str, b: str, max_dist: int) -> int:
    """Левенштейний зай. max_dist-ээс хэтэрвэл max_dist + 1 буцаана."""
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        row_min = i
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            row_min = min(row_min, cur[j])
        if row_min > max_dist:
            return max_dist + 1
        prev = cur
    return prev[-1]
