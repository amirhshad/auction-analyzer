"""Entry point: python -m execution.webapp"""
import uvicorn

uvicorn.run("execution.webapp.app:app", host="0.0.0.0", port=8000, reload=True)
