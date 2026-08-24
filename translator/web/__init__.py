"""Novel Translator Studio Web Backend Module.

The package intentionally avoids importing the FastAPI application eagerly.  This
keeps core modules importable without constructing routers and their global worker
singletons as a side effect.
"""

from typing import Any


def create_app(*args: Any, **kwargs: Any) -> Any:
    from translator.web.app import create_app as factory

    return factory(*args, **kwargs)


def run_server(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("translator.web.app:app", host=host, port=port, reload=reload)


def __getattr__(name: str) -> Any:
    if name == "app":
        from translator.web.app import app

        return app
    if name == "broadcaster":
        from translator.web.events import broadcaster

        return broadcaster
    raise AttributeError(name)


__all__ = ["app", "broadcaster", "create_app", "run_server"]
