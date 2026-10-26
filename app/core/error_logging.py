"""Safe, fixed descriptions of rejected requests; never log exception detail/input."""
import logging

from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError, TimeoutError as PoolTimeoutError
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)

# Only application-owned strings are translated; arbitrary detail is never logged.
HTTP_REASONS = {
    "Invalid or expired credentials": ("authentication_failed", "Credentials are missing, invalid or expired."),
    "Admin access required": ("admin_required", "The authenticated account lacks administrator permission."),
    "Access denied": ("ownership_required", "The account cannot access the requested resource."),
    "Email already exists": ("email_already_registered", "Registration conflicts with an existing account."),
    "Customer not found": ("customer_not_found", "The customer required for the operation does not exist."),
    "Product not found": ("product_not_found", "A requested product does not exist; the order was rejected."),
    "Order not found": ("order_not_found", "The requested order does not exist."),
    "Product has no valid price": ("invalid_product_price", "A product has no valid price; the order was rejected."),
    "Insufficient stock": ("insufficient_stock", "Available stock cannot fulfill the order; no order changes were committed."),
}
AUTH_REASONS = {
    "credentials_missing": "The request has no supported bearer credentials.",
    "token_expired": "The bearer token has expired.",
    "token_invalid": "The bearer token or its claims failed validation.",
    "account_unavailable": "The token does not identify an available account.",
}
FIELDS = {
    "body", "query", "path", "header", "name", "email", "password", "items",
    "product_id", "quantity", "unit_price", "stock", "limit", "offset", "id",
}
VALIDATION_TYPES = {
    "missing", "extra_forbidden", "greater_than", "greater_than_equal", "less_than_equal",
    "int_parsing", "int_type", "string_type", "string_too_short", "string_too_long",
    "too_short", "too_long", "list_type", "model_attributes_type", "value_error", "json_invalid",
}


def database_unavailable(exc):
    """Only transient availability failures are 503; constraints/bugs remain 500."""
    if isinstance(exc, PoolTimeoutError):
        return True
    if isinstance(exc, DBAPIError):
        state = getattr(exc.orig, "sqlstate", None)
        if exc.connection_invalidated:
            return True
        if state:
            return state.startswith(("08", "53")) or state in {
                "57P01", "57P02", "57P03", "57014", "55P03", "40001", "40P01",
            }
        return isinstance(exc, (OperationalError, InterfaceError))
    return False


def server_error_response(status_code):
    """Never forward exception detail or arbitrary headers in server errors."""
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Service temporarily unavailable" if status_code == 503 else "Internal server error"},
    )


def unexpected_error_details(exc):
    if isinstance(exc, DBAPIError):
        state = getattr(exc.orig, "sqlstate", None)
        transient_reasons = {
            "57014": ("database_statement_timeout", "A database statement was cancelled or exceeded its execution limit."),
            "55P03": ("database_lock_timeout", "A required database lock was unavailable within its wait limit."),
            "40001": ("database_serialization_failure", "A concurrent transaction prevented this transaction from completing."),
            "40P01": ("database_deadlock", "PostgreSQL detected a deadlock and aborted the transaction."),
        }
        if state in transient_reasons:
            reason, description = transient_reasons[state]
            return {"reason": reason, "description": description}
    if isinstance(exc, IntegrityError):
        return {"reason": "database_constraint_failed",
                "description": "A database integrity constraint rejected the operation."}
    if isinstance(exc, OperationalError):
        return {"reason": "database_operation_failed",
                "description": "A database operation failed; check database availability and server logs."}
    if isinstance(exc, PoolTimeoutError):
        return {"reason": "database_pool_timeout",
                "description": "No database connection became available before the pool timeout."}
    if isinstance(exc, ResponseValidationError):
        return {"reason": "response_validation_failed",
                "description": "The application result does not match its response schema."}
    return {"reason": "unexpected_error",
            "description": "An unexpected application error interrupted the request; inspect the safe stack locations."}


async def log_http_error(request, exc):
    reason, description = HTTP_REASONS.get(
        exc.detail if isinstance(exc.detail, str) else "",
        {404: ("route_not_found", "No route matches the request."),
         405: ("method_not_allowed", "The route does not support this HTTP method.")}.get(
            exc.status_code, ("http_request_rejected", "The request was rejected by the application.")),
    )
    auth_reason = getattr(exc, "log_reason", None)
    if auth_reason in AUTH_REASONS:
        reason, description = auth_reason, AUTH_REASONS[auth_reason]
    logger.log(logging.ERROR if exc.status_code >= 500 else logging.WARNING,
               "request_rejected", extra={"reason": reason, "description": description,
                                          "status_code": exc.status_code})
    if exc.status_code >= 500:
        return server_error_response(exc.status_code)
    return await http_exception_handler(request, exc)


async def log_validation_error(request, exc):
    errors = exc.errors()
    issues = []
    for error in errors[:20]:
        # Extra field names and malformed JSON locations can themselves be secrets.
        location = [part if isinstance(part, str) and part in FIELDS else "<redacted>"
                    for part in error.get("loc", ())[:8]]
        error_type = error.get("type")
        issues.append({"field": location, "type": error_type if error_type in VALIDATION_TYPES
                       else "validation_error"})
    logger.warning("request_rejected", extra={
        "reason": "request_validation_failed", "description": "Request fields failed validation.",
        "status_code": 422, "validation_errors": issues, "error_count": len(errors),
    })
    return await request_validation_exception_handler(request, exc)


def install_error_logging(app):
    app.add_exception_handler(HTTPException, log_http_error)
    app.add_exception_handler(RequestValidationError, log_validation_error)
