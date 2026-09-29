"""Урвуу индекс (inverted index) ба BM25 эрэмбэлэлттэй хайлтын хөдөлгүүр."""

import bisect
import html
import json
import math
import os
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set

from .query import Clause, parse_query
from .tokenizer import edit_distance, normalize, tokenize

DEFAULT_FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "body": 1.0}
PHRASE_BOOST = 1.5
PREFIX_MIN_LEN = 2
PREFIX_MAX_EXPANSIONS = 50
FUZZY_MIN_LEN = 4
SNIPPET_TOKENS = 30


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _max_typos(term: str) -> int:
    return 1 if len(term) < 8 else 2


def _matches_filters(doc: Dict[str, Any], filters: Dict[str, str]) -> bool:
    for key, expected in filters.items():
        value = doc.get(key)
        values = value if isinstance(value, (list, tuple)) else [value]
        if normalize(str(expected)) not in {normalize(str(v)) for v in values if v is not None}:
            return False
    return True


def highlight(text: str, terms: Set[str], window: Optional[int] = SNIPPET_TOKENS) -> str:
    """Олдсон үгсийг <mark> тагаар тэмдэглэсэн, HTML-escape хийсэн хэсэг буцаана."""
    tokens = tokenize(text)
    if window is None or len(tokens) <= window:
        start, end = 0, len(tokens)
    else:
        hits = [i for i, t in enumerate(tokens) if t.text in terms]
        anchor = 0
        if hits:
            # Хамгийн олон олдсон үг багтах цонхыг сонгоно
            best = max(hits, key=lambda h: sum(1 for x in hits if h <= x < h + window))
            anchor = max(0, best - 5)
        start = min(anchor, len(tokens) - window)
        end = start + window
    char_start = tokens[start].start if start > 0 else 0
    char_end = tokens[end - 1].end if end < len(tokens) else len(text)

    parts = ["…"] if char_start > 0 else []
    cursor = char_start
    for tok in tokens[start:end]:
        if tok.text in terms:
            parts.append(html.escape(text[cursor:tok.start]))
            parts.append("<mark>" + html.escape(text[tok.start:tok.end]) + "</mark>")
            cursor = tok.end
    parts.append(html.escape(text[cursor:char_end]))
    if char_end < len(text):
        parts.append("…")
    return "".join(parts)


class SearchIndex:
    def __init__(
        self,
        field_weights: Optional[Dict[str, float]] = None,
        k1: float = 1.2,
        b: float = 0.75,
    ):
        self.field_weights = dict(field_weights or DEFAULT_FIELD_WEIGHTS)
        self.k1 = k1
        self.b = b
        self._docs: Dict[str, Dict[str, Any]] = {}
        # term -> doc_id -> field -> [байрлал]
        self._postings: Dict[str, Dict[str, Dict[str, List[int]]]] = {}
        self._lengths: Dict[str, Dict[str, int]] = {}
        self._total_len: Dict[str, int] = defaultdict(int)
        self._field_docs: Dict[str, int] = defaultdict(int)
        self._vocab: Optional[List[str]] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Баримт нэмэх / устгах
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._docs)

    def __contains__(self, doc_id: str) -> bool:
        return str(doc_id) in self._docs

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self._docs.get(str(doc_id))

    def documents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._docs.values())

    def add(self, doc: Dict[str, Any]) -> str:
        """Баримт нэмнэ. Ижил id-тай баримт байвал шинэчилнэ."""
        if not isinstance(doc, dict):
            raise TypeError("Баримт нь dict байх ёстой")
        doc = dict(doc)
        doc_id = str(doc.get("id") or uuid.uuid4().hex[:12])
        doc["id"] = doc_id
        with self._lock:
            self._remove_locked(doc_id)
            self._docs[doc_id] = doc
            lengths = {}
            for fld in self.field_weights:
                tokens = tokenize(_field_text(doc.get(fld)))
                if not tokens:
                    continue
                lengths[fld] = len(tokens)
                self._total_len[fld] += len(tokens)
                self._field_docs[fld] += 1
                for tok in tokens:
                    by_doc = self._postings.setdefault(tok.text, {})
                    by_doc.setdefault(doc_id, {}).setdefault(fld, []).append(tok.position)
            self._lengths[doc_id] = lengths
            self._vocab = None
        return doc_id

    def add_many(self, docs: Iterable[Dict[str, Any]]) -> List[str]:
        return [self.add(doc) for doc in docs]

    def remove(self, doc_id: str) -> bool:
        with self._lock:
            return self._remove_locked(str(doc_id))

    def _remove_locked(self, doc_id: str) -> bool:
        doc = self._docs.pop(doc_id, None)
        if doc is None:
            return False
        for fld, n in self._lengths.pop(doc_id, {}).items():
            self._total_len[fld] -= n
            self._field_docs[fld] -= 1
        for fld in self.field_weights:
            for term in {t.text for t in tokenize(_field_text(doc.get(fld)))}:
                by_doc = self._postings.get(term)
                if by_doc and doc_id in by_doc:
                    del by_doc[doc_id]
                    if not by_doc:
                        del self._postings[term]
        self._vocab = None
        return True

    # ------------------------------------------------------------------
    # Хадгалах / ачаалах
    # ------------------------------------------------------------------
    def save(self, path: str) -> None:
        with self._lock:
            data = json.dumps(list(self._docs.values()), ensure_ascii=False, indent=2)
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str, **kwargs) -> "SearchIndex":
        index = cls(**kwargs)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("documents", [])
        index.add_many(data)
        return index

    # ------------------------------------------------------------------
    # Толь бичиг
    # ------------------------------------------------------------------
    def _sorted_vocab(self) -> List[str]:
        if self._vocab is None:
            self._vocab = sorted(self._postings)
        return self._vocab

    def _df(self, term: str) -> int:
        return len(self._postings.get(term, ()))

    def _prefix_terms(self, prefix: str) -> List[str]:
        vocab = self._sorted_vocab()
        i = bisect.bisect_left(vocab, prefix)
        found = []
        while i < len(vocab) and vocab[i].startswith(prefix):
            found.append(vocab[i])
            i += 1
        found.sort(key=lambda t: (-self._df(t), t))
        return found[:PREFIX_MAX_EXPANSIONS]

    def _fuzzy_terms(self, term: str) -> Dict[str, int]:
        max_dist = _max_typos(term)
        result = {}
        for cand in self._postings:
            d = edit_distance(term, cand, max_dist)
            if d <= max_dist:
                result[cand] = d
        return result

    def _expand(self, term: str, prefix: bool, fuzzy: bool) -> Dict[str, float]:
        """Асуулгын үгийг индекс дэх үгс болон жинд хувиргана."""
        out: Dict[str, float] = {}
        if term in self._postings:
            out[term] = 1.0
        if prefix and len(term) >= PREFIX_MIN_LEN:
            # Монгол хэл залгамал тул "ном" -> "номын", "номыг" гэх мэт
            for cand in self._prefix_terms(term):
                out.setdefault(cand, 0.5 + 0.4 * len(term) / len(cand))
        if fuzzy and not out and len(term) >= FUZZY_MIN_LEN:
            for cand, dist in self._fuzzy_terms(term).items():
                out[cand] = 0.6 / dist
        return out

    # ------------------------------------------------------------------
    # Оноо тооцох (BM25F)
    # ------------------------------------------------------------------
    def _idf(self, term: str) -> float:
        n = len(self._docs)
        df = self._df(term)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _term_score(self, term: str, doc_id: str, only_field: Optional[str] = None) -> float:
        entry = self._postings.get(term, {}).get(doc_id)
        if not entry:
            return 0.0
        score = 0.0
        for fld, positions in entry.items():
            if only_field and fld != only_field:
                continue
            tf = len(positions)
            avg = self._total_len[fld] / max(self._field_docs[fld], 1) or 1.0
            dl = self._lengths[doc_id].get(fld, 0)
            norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * dl / avg))
            score += self.field_weights.get(fld, 1.0) * norm
        return score * self._idf(term)

    def _eval_clause(
        self,
        clause: Clause,
        prefix: bool,
        fuzzy: bool,
        matched: Optional[Dict[str, Set[str]]],
    ) -> Dict[str, float]:
        if clause.phrase:
            return self._eval_phrase(clause, matched)
        scores: Dict[str, float] = {}
        for cand, weight in self._expand(clause.terms[0], prefix, fuzzy).items():
            for doc_id, entry in self._postings[cand].items():
                if clause.field and clause.field not in entry:
                    continue
                s = weight * self._term_score(cand, doc_id, clause.field)
                if s > scores.get(doc_id, 0.0):
                    scores[doc_id] = s
                if matched is not None:
                    matched[doc_id].add(cand)
        return scores

    def _eval_phrase(self, clause: Clause, matched: Optional[Dict[str, Set[str]]]) -> Dict[str, float]:
        if any(t not in self._postings for t in clause.terms):
            return {}
        candidates = set(self._postings[clause.terms[0]])
        for term in clause.terms[1:]:
            candidates &= set(self._postings[term])
        base = clause.offsets[0]
        scores: Dict[str, float] = {}
        for doc_id in candidates:
            first = self._postings[clause.terms[0]][doc_id]
            for fld, positions in first.items():
                if clause.field and fld != clause.field:
                    continue
                others = [set(self._postings[t][doc_id].get(fld, ())) for t in clause.terms[1:]]
                if any(
                    all(p + off - base in s for off, s in zip(clause.offsets[1:], others))
                    for p in positions
                ):
                    scores[doc_id] = PHRASE_BOOST * sum(
                        self._term_score(t, doc_id, clause.field) for t in clause.terms
                    )
                    if matched is not None:
                        matched[doc_id].update(clause.terms)
                    break
        return scores

    # ------------------------------------------------------------------
    # Хайлт
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, str]] = None,
        facets: Optional[List[str]] = None,
        prefix: bool = True,
        fuzzy: bool = True,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        filters = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
        with self._lock:
            clauses = parse_query(query, self.field_weights)
            positive = [c for c in clauses if not c.negate]
            negative = [c for c in clauses if c.negate]
            matched: Dict[str, Set[str]] = defaultdict(set)
            results = [self._eval_clause(c, prefix, fuzzy, matched) for c in positive]

            mode = "and"
            if positive:
                docs = set(results[0]).intersection(*results[1:])
                if not docs and len(positive) > 1:
                    # Бүх үг орсон баримт олдохгүй бол аль нэг нь орсныг хайна
                    mode = "or"
                    docs = set().union(*results)
            else:
                docs = set(self._docs) if (negative or filters) else set()

            for clause in negative:
                docs -= set(self._eval_clause(clause, False, False, None))
            if filters:
                docs = {d for d in docs if _matches_filters(self._docs[d], filters)}

            scores = {}
            for d in docs:
                score = sum(r.get(d, 0.0) for r in results)
                if mode == "or":
                    score *= sum(1 for r in results if d in r) / len(results)
                scores[d] = score
            ranked = sorted(docs, key=lambda d: (-scores[d], d))

            hits = []
            for doc_id in ranked[offset : offset + limit]:
                doc = self._docs[doc_id]
                hits.append(
                    {
                        "id": doc_id,
                        "score": round(scores[doc_id], 4),
                        "document": doc,
                        "highlights": self._highlights(doc, matched.get(doc_id, set())),
                    }
                )

            return {
                "query": query,
                "total": len(ranked),
                "mode": mode,
                "hits": hits,
                "facets": self._facets(ranked, facets or []),
                "suggestion": self._did_you_mean(query, positive),
                "took_ms": round((time.perf_counter() - started) * 1000, 3),
            }

    def _highlights(self, doc: Dict[str, Any], terms: Set[str]) -> Dict[str, str]:
        out = {}
        for fld in self.field_weights:
            text = _field_text(doc.get(fld))
            if text:
                out[fld] = highlight(text, terms, None if fld == "title" else SNIPPET_TOKENS)
        return out

    def _facets(self, doc_ids: List[str], fields: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        out = {}
        for fld in fields:
            counter: Counter = Counter()
            for doc_id in doc_ids:
                value = self._docs[doc_id].get(fld)
                values = value if isinstance(value, (list, tuple)) else [value]
                counter.update(str(v) for v in values if v not in (None, ""))
            out[fld] = [{"value": v, "count": c} for v, c in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]
        return out

    def _did_you_mean(self, query: str, positive: List[Clause]) -> Optional[str]:
        """Индекст байхгүй үгийг хамгийн ойр үгээр сольж санал болгоно."""
        replacements = {}
        for clause in positive:
            if clause.phrase:
                continue
            term = clause.terms[0]
            if term in self._postings or len(term) < FUZZY_MIN_LEN or self._prefix_terms(term):
                continue
            candidates = self._fuzzy_terms(term)
            if candidates:
                best = min(candidates, key=lambda c: (candidates[c], -self._df(c), c))
                replacements[term] = best
        if not replacements:
            return None
        tokens = tokenize(query)
        pieces, cursor = [], 0
        for tok in tokens:
            if tok.text in replacements:
                pieces.append(query[cursor:tok.start])
                pieces.append(replacements[tok.text])
                cursor = tok.end
        pieces.append(query[cursor:])
        return "".join(pieces)

    def autocomplete(self, text: str, limit: int = 8) -> List[str]:
        """Сүүлийн үгийн эхлэлээр асуулгыг гүйцээх санал буцаана."""
        tokens = tokenize(text)
        if not tokens or not text or not text[-1].isalnum():
            return []
        last = tokens[-1]
        head = text[: last.start]
        with self._lock:
            terms = self._prefix_terms(last.text)
        return [head + t for t in terms if t != last.text][:limit]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "documents": len(self._docs),
                "terms": len(self._postings),
                "fields": {
                    fld: {
                        "weight": w,
                        "avg_length": round(self._total_len[fld] / self._field_docs[fld], 2)
                        if self._field_docs[fld]
                        else 0,
                    }
                    for fld, w in self.field_weights.items()
                },
            }
