"""One shape for every error: `{"error": {"code", "message"}}`, with the status it travels on."""

from .. import beacon, envelope, store as _store, transport


class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.extra = extra              # more fields for the error body, e.g. a 409's cautions


def bad(message):
    return ApiError(400, "bad_request", message)


def not_found(message):
    return ApiError(404, "not_found", message)


def classify(exc):
    """`(status, code, message)` for any exception a handler or a job may raise."""
    if isinstance(exc, ApiError):
        return exc.status, exc.code, exc.message
    if isinstance(exc, envelope.NodeIdError):
        return 400, "bad_request", str(exc)
    if isinstance(exc, envelope.EnvelopeError):
        return 400, "refused", str(exc)
    if isinstance(exc, _store.StoreError):
        if str(exc) == "no such device":
            return 404, "not_found", str(exc)
        return 400, "store", str(exc)
    if isinstance(exc, transport.BrokerError):
        return 502, "broker", str(exc)
    if isinstance(exc, beacon.BeaconError):
        return 502, "beacon", str(exc)
    return 500, "internal", f"{type(exc).__name__}: {exc}"


def body(code, message):
    return {"error": {"code": code, "message": message}}
