def doGet(request, session):
	try:
		params = request["params"]
		typeId = params.get("typeElementId", None)
		includeMetadata = params.get("includeMetadata", "false").lower() == "true"
		root = params.get("root", "false").lower() == "true"
		(errorCode, bulkError, error, result) = i3x.handlers.getObjects(typeId, includeMetadata, root)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.objects")

	return i3x.handlers.handleResponse(request, errorCode, False, bulkError, error, result)
