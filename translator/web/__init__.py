"""Novel Translator Studio Web Backend Module."""

from translator.web.app import app, create_app
from translator.web.events import broadcaster


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("translator.web.app:app", host=host, port=port, reload=reload)


__all__ = ["app", "broadcaster", "create_app", "run_server"]

