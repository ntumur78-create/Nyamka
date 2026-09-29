"""Командын мөрийн интерфэйс.

    python -m search_engine serve  --data data/documents.json --port 8000
    python -m search_engine search "монгол түүх" --data data/documents.json
    python -m search_engine index  ./docs --data data/documents.json
"""

import argparse
import os
import re
import sys

from .index import SearchIndex
from .server import create_server

DEFAULT_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "documents.json")
TEXT_EXTENSIONS = {".txt", ".md", ".rst"}


def _load(path: str) -> SearchIndex:
    return SearchIndex.load(path) if os.path.exists(path) else SearchIndex()


def cmd_serve(args) -> int:
    index = _load(args.data)
    server = create_server(index, args.host, args.port, data_path=args.data)
    print(f"{len(index)} баримт ачааллаа. http://{args.host}:{server.server_address[1]} хаягаар нээнэ үү.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_search(args) -> int:
    index = _load(args.data)
    filters = {"category": args.category} if args.category else None
    result = index.search(args.query, limit=args.limit, filters=filters)
    if result["suggestion"]:
        print(f"Та «{result['suggestion']}» гэж хайсан уу?")
    print(f"{result['total']} үр дүн ({result['took_ms']} мс)\n")
    for i, hit in enumerate(result["hits"], 1):
        title = re.sub(r"</?mark>", "*", hit["highlights"].get("title", hit["id"]))
        body = re.sub(r"</?mark>", "*", hit["highlights"].get("body", ""))
        print(f"{i}. {title}  [{hit['score']}]")
        if body:
            print(f"   {body}")
    return 0


def cmd_index(args) -> int:
    index = _load(args.data)
    count = 0
    for root, _, files in os.walk(args.directory):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in TEXT_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            first_line = text.strip().splitlines()[0] if text.strip() else name
            index.add(
                {
                    "id": os.path.relpath(path, args.directory),
                    "title": first_line.lstrip("# ").strip() or name,
                    "body": text,
                    "category": os.path.basename(root) if root != args.directory else "",
                    "path": path,
                }
            )
            count += 1
    index.save(args.data)
    print(f"{count} файл индекслэв. Нийт {len(index)} баримт → {args.data}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="search_engine", description="Nyamka хайлтын систем")
    parser.add_argument("--data", default=DEFAULT_DATA, help="баримтуудын JSON файл")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="веб сервер асаах")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("search", help="командын мөрөөс хайх")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--category")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("index", help="хавтас дахь текст файлуудыг индекслэх")
    p.add_argument("directory")
    p.set_defaults(func=cmd_index)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
