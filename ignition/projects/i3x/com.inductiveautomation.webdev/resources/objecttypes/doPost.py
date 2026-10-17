def doPost(request, session):
	try:
		requestData = request["postData"]
		elementIds = requestData.get("elementIds", [])
		(errorCode, bulkError, error, result) = i3x.handlers.getObjectTypes(None, elementIds)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.objecttypes")

	return i3x.handlers.handleResponse(request, errorCode, True, bulkError, error, result)
