"""API process entrypoint."""

import uvicorn

from coinwatch.api.app import create_app

API_HOST = "127.0.0.1"
API_PORT = 8420


def main() -> None:
    """Serve the API on localhost until the process is stopped."""
    uvicorn.run(create_app(), host=API_HOST, port=API_PORT, log_config=None)


if __name__ == "__main__":
    main()
