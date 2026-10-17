def doGet(request, session):
	try:
		(errorCode, bulkError, error, result) = i3x.handlers.getNamespaces()
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.namespaces")

	return i3x.handlers.handleResponse(request, errorCode, False, bulkError, error, result)
