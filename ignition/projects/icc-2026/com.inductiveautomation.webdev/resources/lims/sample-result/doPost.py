def doPost(request, session):
    """LIMS approval webhook. Shared secret + idempotency; Transmission publishes."""
    return lims_webhook.handle(request)
