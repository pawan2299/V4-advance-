"""Centralized error handling — structured error responses and alerting."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, request, g

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error with HTTP status code."""

    def __init__(self, message: str, status_code: int = 500, details: str = ""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class ValidationError(AppError):
    def __init__(self, message: str, details: str = ""):
        super().__init__(message, status_code=400, details=details)


class RateLimitError(AppError):
    def __init__(self, message: str = "Rate limit exceeded", details: str = ""):
        super().__init__(message, status_code=429, details=details)


class AuthenticationError(AppError):
    def __init__(self, message: str = "Authentication failed", details: str = ""):
        super().__init__(message, status_code=401, details=details)


def _build_error_response(status_code: int, error: str, details: str = "", request_id: str = "") -> dict:
    """Build a structured JSON error response."""
    return {
        "error": error,
        "status": status_code,
        "details": details,
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def register_error_handlers(app: Flask) -> None:
    """Register centralized error handlers on the Flask app."""

    @app.errorhandler(AppError)
    def _handle_app_error(exc: AppError):
        request_id = getattr(g, "request_id", "unknown")
        logger.warning(
            "AppError [%s] %s — %s | request_id=%s",
            exc.status_code,
            exc.message,
            exc.details,
            request_id,
        )
        return jsonify(_build_error_response(exc.status_code, exc.message, exc.details, request_id)), exc.status_code

    @app.errorhandler(404)
    def _handle_404(_exc):
        request_id = getattr(g, "request_id", "unknown")
        return jsonify(_build_error_response(404, "Not found", request_id=request_id)), 404

    @app.errorhandler(405)
    def _handle_405(_exc):
        request_id = getattr(g, "request_id", "unknown")
        return jsonify(_build_error_response(405, "Method not allowed", request_id=request_id)), 405

    @app.errorhandler(429)
    def _handle_429(_exc):
        request_id = getattr(g, "request_id", "unknown")
        return jsonify(_build_error_response(429, "Too many requests", request_id=request_id)), 429

    @app.errorhandler(500)
    def _handle_500(exc):
        request_id = getattr(g, "request_id", "unknown")
        logger.error(
            "Unhandled exception | path=%s method=%s request_id=%s\n%s",
            request.path,
            request.method,
            request_id,
            traceback.format_exc(),
        )
        return jsonify(_build_error_response(500, "Internal server error", request_id=request_id)), 500

    @app.errorhandler(Exception)
    def _handle_generic(exc):
        request_id = getattr(g, "request_id", "unknown")
        logger.error(
            "Unhandled exception (generic) | path=%s request_id=%s\n%s",
            request.path,
            request_id,
            traceback.format_exc(),
        )
        return jsonify(_build_error_response(500, "Internal server error", request_id=request_id)), 500