"""Vercel serverless entry point.

Wraps the FastAPI ASGI app with Mangum so Vercel's AWS Lambda-compatible
runtime can invoke it.  Every incoming request is routed here via
vercel.json's rewrite rules.
"""

from mangum import Mangum

# main.py lives one directory up; Python's import resolves it because
# Vercel sets the project root on sys.path automatically.
from main import app

# Mangum adapter — the `handler` name is what Vercel expects.
handler = Mangum(app, lifespan="off")
