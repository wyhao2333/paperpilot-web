"""Isolated PDF parser command, shared by Windows and Ubuntu."""
import argparse
import json
import logging
import sys


def parse_pdf(*args, **kwargs):
    from .reflow import parse_pdf as implementation
    return implementation(*args, **kwargs)


def limit_resources():
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (90, 95))
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

logging.getLogger("pdfminer").setLevel(logging.ERROR)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--max-pages",type=int,default=200)
    parser.add_argument("--assets-dir")
    args=parser.parse_args()
    try:
        limit_resources()
        print(json.dumps(parse_pdf(args.path,args.max_pages,args.assets_dir),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error":f"{type(exc).__name__}: {str(exc)[:350]}"},ensure_ascii=False))
        sys.exit(1)
