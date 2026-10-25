"""Explicit, bounded database waits shared by application and migrations."""
import os


def positive_integer(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def connection_arguments(*, migrations=False):
    prefix = "MIGRATION" if migrations else "DB"
    statement = positive_integer(f"{prefix}_STATEMENT_TIMEOUT_MS", 120000 if migrations else 5000)
    lock = positive_integer(f"{prefix}_LOCK_TIMEOUT_MS", 10000 if migrations else 2000)
    idle = positive_integer(f"{prefix}_IDLE_TRANSACTION_TIMEOUT_MS", 60000 if migrations else 10000)
    return {
        "connect_timeout": positive_integer("DB_CONNECT_TIMEOUT_SECONDS", 3),
        "options": (f"-csearch_path=ecommerce,public -cstatement_timeout={statement} "
                    f"-clock_timeout={lock} -cidle_in_transaction_session_timeout={idle}"),
    }
