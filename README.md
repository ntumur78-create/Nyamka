# Nyamka хайлтын систем

Монгол (кирилл) болон бусад хэлний текстэд зориулсан бүрэн текст хайлтын систем.
Зөвхөн Python-ийн стандарт сан ашигладаг тул нэмэлт суулгац шаардлагагүй (Python 3.8+).

## Боломжууд

- **Урвуу индекс + BM25F эрэмбэлэлт** — талбар бүр жинтэй (`title` ×3, `tags` ×2, `body` ×1)
- **Үгийн эхлэлээр хайх** — монгол хэл залгамал тул `ном` гэж хайхад `номын`, `номонд` бас олдоно
- **Үсгийн алдаа засах** — `Хархорм` → `Хархорум`, мөн «Та … гэж хайсан уу?» санал
- **Хэллэгээр хайх** — `"нийслэл хот"`
- **Хасах** — `нийслэл -улаанбаатар`
- **Талбарт хайх** — `title:хаан`
- **Шүүлтүүр ба ангилал (facet)** — `category`, `tags`
- Бүх үг орсон баримт олдохгүй бол аль нэг нь орсныг харуулна
- Олдсон үгийг тодруулсан хэсэгчилсэн текст (snippet), автоматаар гүйцээх (autocomplete)
- Веб интерфэйс, JSON API, командын мөрийн хэрэгсэл

## Хэрэглэх

```bash
# Веб сервер асаах → http://127.0.0.1:8000
python3 -m search_engine serve

# Командын мөрөөс хайх
python3 -m search_engine search "монгол түүх"
python3 -m search_engine search "уламжлал" --category Соёл

# Хавтас доторх .txt / .md файлуудыг индекслэх
python3 -m search_engine --data data/my_docs.json index ./docs
python3 -m search_engine --data data/my_docs.json serve --port 9000
```

Анхдагч өгөгдөл: `data/documents.json` (жишээ монгол баримтууд).

## API

| Арга | Зам | Тайлбар |
|------|-----|---------|
| GET | `/api/search?q=...&limit=10&offset=0&category=...&tags=...` | Хайлт |
| GET | `/api/autocomplete?q=...` | Гүйцээх санал |
| GET | `/api/documents` | Бүх баримт |
| GET | `/api/documents/{id}` | Нэг баримт |
| POST | `/api/documents` | Баримт (эсвэл жагсаалт) нэмэх/шинэчлэх |
| DELETE | `/api/documents/{id}` | Баримт устгах |
| GET | `/api/stats` | Индексийн статистик |

Баримтын бүтэц:

```json
{"id": "naadam", "title": "Наадам", "body": "…", "category": "Соёл", "tags": ["баяр"]}
```

`id` өгөөгүй бол автоматаар үүснэ. Өөрчлөлт `--data` файлд хадгалагдана.

## Python-оос ашиглах

```python
from search_engine import SearchIndex

index = SearchIndex()
index.add({"id": "1", "title": "Хөвсгөл нуур", "body": "Хамгийн гүн цэнгэг устай нуур"})
result = index.search("нуур", limit=5)
for hit in result["hits"]:
    print(hit["id"], hit["score"], hit["highlights"]["title"])
```

## Бүтэц

```
search_engine/
  tokenizer.py   үг хуваах, хэвийн болгох, Левенштейний зай
  query.py       асуулгын задлагч (хэллэг, хасах, талбар)
  index.py       урвуу индекс, BM25F, prefix/fuzzy, snippet, facet
  server.py      HTTP API
  static/        веб интерфэйс
  __main__.py    CLI
tests/           unittest
```

## Тест

```bash
python3 -m unittest discover -s tests -t .
```
