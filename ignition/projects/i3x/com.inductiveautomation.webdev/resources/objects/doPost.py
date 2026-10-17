def doPost(request, session):
	try:
		requestData = request["postData"]
		elementIds = requestData.get("elementIds", [])
		callType = "list"
		remainingPath = request["remainingPath"]
		if remainingPath != None and remainingPath != "" and remainingPath.endswith("/related"):
			callType = "related"
		elif remainingPath != None and remainingPath != "" and remainingPath.endswith("/value"):
			callType = "value"
		elif remainingPath != None and remainingPath != "" and remainingPath.endswith("/history"):
			callType = "history"

		includeMetadata = requestData.get("includeMetadata", False)
		relationshipType = requestData.get("relationshipType", None)
		maxDepth = requestData.get("maxDepth", 1)
		startTime = requestData.get("startTime", None)
		endTime = requestData.get("endTime", None)

		(errorCode, bulkError, error, result) = i3x.handlers.getObjects(None, includeMetadata, False, elementIds, callType, relationshipType, maxDepth, startTime, endTime)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.objects")

	return i3x.handlers.handleResponse(request, errorCode, True, bulkError, error, result)
