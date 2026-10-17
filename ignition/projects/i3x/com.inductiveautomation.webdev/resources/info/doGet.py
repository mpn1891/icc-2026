def doGet(request, session):
	try:
		(errorCode, bulkError, error, result) = i3x.handlers.getInfo()
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.info")

	return i3x.handlers.handleResponse(request, errorCode, False, bulkError, error, result)
