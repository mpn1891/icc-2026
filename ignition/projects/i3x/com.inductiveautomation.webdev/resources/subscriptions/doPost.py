def doPost(request, session):
	try:
		requestData = request["postData"]
		remainingPath = request["remainingPath"]

		def endsWith(suffix):
			return remainingPath != None and remainingPath != "" and remainingPath.endswith(suffix)

		# SSE streaming: open a Server-Sent Events stream for the subscription.
		# streamSubscription writes directly to the servlet response and returns
		# None (or a normal error response for a bad clientId/subscriptionId).
		if endsWith("/stream"):
			return i3x.handlers.streamSubscription(request, requestData)

		callType = "create"
		isBulk = False
		if endsWith("/register"):
			callType = "register"
			isBulk = True
		elif endsWith("/unregister"):
			callType = "unregister"
			isBulk = True
		elif endsWith("/sync"):
			callType = "sync"
		elif endsWith("/delete"):
			callType = "delete"
			isBulk = True
		elif endsWith("/list"):
			callType = "list"
			isBulk = True

		(errorCode, bulkError, error, result) = i3x.handlers.getSubscriptions(callType, requestData)
	except:
		(errorCode, bulkError, error, result) = i3x.handlers.serverError("i3x.subscriptions")
		isBulk = False

	return i3x.handlers.handleResponse(request, errorCode, isBulk, bulkError, error, result)
