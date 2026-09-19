def doGet(request, session):
	try:
		params = request["params"]
		namespaceUri = params.get("namespaceUri", None)
		(errorCode, bulkError, error, result) = i3x.handlers.getObjectTypes(namespaceUri)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.objecttypes")

	return i3x.handlers.handleResponse(request, errorCode, False, bulkError, error, result)
