# One Jinja2 environment shared by the app factory and every router. It lives in
# its own module because importing it from app.main would be a circular import.
from __future__ import annotations

from pathlib import Path

from starlette.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
