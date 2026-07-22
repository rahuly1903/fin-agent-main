"""
Structured Logging Module

Provides JSON-formatted logging with request IDs for observability and audit trails.
"""

import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from contextvars import ContextVar

# Context variable to store request ID across async contexts
request_id_var: ContextVar[str] = ContextVar('request_id', default='no-request-id')


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that outputs logs as JSON for easy parsing by log aggregators.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        
        # Add extra fields if present
        if hasattr(record, 'extra_data') and record.extra_data:
            log_entry["data"] = record.extra_data
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add source location for errors
        if record.levelno >= logging.ERROR:
            log_entry["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }
        
        return json.dumps(log_entry)


class StructuredLogger:
    """
    Wrapper around Python's logging module that provides structured logging methods.
    """
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
    
    def _log(self, level: int, message: str, data: Optional[Dict[str, Any]] = None, **kwargs):
        """Internal method to log with extra data."""
        extra = {'extra_data': data} if data else {}
        self.logger.log(level, message, extra=extra, **kwargs)
    
    def debug(self, message: str, data: Optional[Dict[str, Any]] = None):
        self._log(logging.DEBUG, message, data)
    
    def info(self, message: str, data: Optional[Dict[str, Any]] = None):
        self._log(logging.INFO, message, data)
    
    def warning(self, message: str, data: Optional[Dict[str, Any]] = None):
        self._log(logging.WARNING, message, data)
    
    def error(self, message: str, data: Optional[Dict[str, Any]] = None, exc_info: bool = False):
        self._log(logging.ERROR, message, data, exc_info=exc_info)
    
    def critical(self, message: str, data: Optional[Dict[str, Any]] = None, exc_info: bool = False):
        self._log(logging.CRITICAL, message, data, exc_info=exc_info)
    
    # Convenience methods for common financial operations
    def log_request(self, endpoint: str, method: str, params: Optional[Dict[str, Any]] = None):
        """Log an incoming API request."""
        self.info(f"API request: {method} {endpoint}", {
            "endpoint": endpoint,
            "method": method,
            "params": params or {},
        })
    
    def log_response(self, endpoint: str, status_code: int, duration_ms: float):
        """Log an API response."""
        self.info(f"API response: {endpoint} -> {status_code}", {
            "endpoint": endpoint,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
        })
    
    def log_llm_call(self, model: str, prompt_tokens: Optional[int] = None, action: str = "started"):
        """Log an LLM API call."""
        self.info(f"LLM call {action}: {model}", {
            "model": model,
            "action": action,
            "prompt_tokens": prompt_tokens,
        })
    
    def log_db_query(self, query_type: str, table: str, duration_ms: Optional[float] = None):
        """Log a database query."""
        self.debug(f"DB query: {query_type} on {table}", {
            "query_type": query_type,
            "table": table,
            "duration_ms": round(duration_ms, 2) if duration_ms else None,
        })
    
    def log_financial_action(self, action: str, ticker: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        """Log a financial action for audit purposes."""
        self.info(f"Financial action: {action}", {
            "action": action,
            "ticker": ticker,
            **(details or {}),
        })
    
    def log_data_upload(self, filename: str, companies_count: int, status: str):
        """Log a data upload operation."""
        self.warning(f"Data upload: {filename}", {
            "filename": filename,
            "companies_count": companies_count,
            "status": status,
        })


def setup_logging(level: str = "INFO", json_format: bool = True) -> StructuredLogger:
    """
    Initialize the logging system.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, output JSON-formatted logs; otherwise use standard format
        
    Returns:
        The main application logger
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    if json_format:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | [%(request_id)s] %(message)s',
            defaults={'request_id': 'no-request-id'}
        ))
    
    root_logger.addHandler(handler)
    
    return get_logger("fin_agent")


def get_logger(name: str) -> StructuredLogger:
    """Get a structured logger for a specific module."""
    return StructuredLogger(name)


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid.uuid4())[:8]


def set_request_id(request_id: str):
    """Set the current request ID for the context."""
    request_id_var.set(request_id)


def get_request_id() -> str:
    """Get the current request ID from context."""
    return request_id_var.get()
