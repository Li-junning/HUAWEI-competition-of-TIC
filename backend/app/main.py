"""ASGI entry point. Start with uvicorn app.main:app as before."""

from .application import create_app

app = create_app()
