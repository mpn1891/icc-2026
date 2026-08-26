def doPost(request, session):
    """Pattern 5 -- Debezium HTTP sink. One change event in, one MQTT message out."""
    return bes_cdc.handle(request)
