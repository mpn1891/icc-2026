def doPost(request, session):
	try:
		requestData = request["postData"]
		elementIds = requestData.get("elementIds", [])
		(errorCode, bulkError, error, result) = i3x.handlers.getRelationshipTypes(None, elementIds)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.relationshiptypes")

	return i3x.handlers.handleResponse(request, errorCode, True, bulkError, error, result)
