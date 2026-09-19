def doGet(request, session):
	try:
		params = request["params"]
		namespaceUri = params.get("namespaceUri", None)
		(errorCode, bulkError, error, result) = i3x.handlers.getRelationshipTypes(namespaceUri)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.relationshiptypes")

	return i3x.handlers.handleResponse(request, errorCode, False, bulkError, error, result)
