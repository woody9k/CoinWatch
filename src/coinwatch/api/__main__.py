"""API process entrypoint."""

import uvicorn
from fastapi import FastAPI

API_HOST = "127.0.0.1"
API_PORT = 8420


def create_app() -> FastAPI:
    """Build the CoinWatch HTTP application."""
    app = FastAPI(title="CoinWatch")

    @app.get("/api/status")
    def status() -> dict[str, str]:
        """Report that the API process is up."""
        return {"status": "ok", "service": "coinwatch"}

    return app


def main() -> None:
    """Serve the API until the process is stopped."""
    uvicorn.run(create_app(), host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
