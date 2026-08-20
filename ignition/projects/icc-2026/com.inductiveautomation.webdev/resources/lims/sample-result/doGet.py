def doGet(request, session):
    """Mount-path probe. Does not publish."""
    return {"json": {
        "ok": True,
        "service": "lims-webhook",
        "methods": ["POST"],
    }}
