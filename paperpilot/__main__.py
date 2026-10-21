import argparse
import os
from urllib.parse import urlsplit

import uvicorn

from .config import Config
from .main import create_app


def main():
    parser = argparse.ArgumentParser(description="PaperPilot Web local reader")
    parser.add_argument("--host", default=os.getenv("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("APP_PORT", "8765")))
    args = parser.parse_args()
    config = Config.from_env()
    if not os.getenv("APP_ORIGIN"):
        config.origin = f"http://localhost:{args.port}"
    print(f"\nPaperPilot Web: http://localhost:{args.port}\n", flush=True)
    uvicorn.run(create_app(config), host=args.host, port=args.port, proxy_headers=False, workers=1)


if __name__ == "__main__":
    main()
