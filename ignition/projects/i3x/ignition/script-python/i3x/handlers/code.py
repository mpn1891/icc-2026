# i3x.handlers
# ------------
# Request handlers for the i3X API. The WebDev endpoint resources are thin
# wrappers (doGet/doPost) that delegate here; this module turns Ignition data
# into i3X response payloads and owns the response envelope.
#
# Each handler returns a 4-tuple consumed by handleResponse():
#     (statusCode, bulkError, errorMessage, result)
#   - statusCode:    HTTP status to send (200/206 carry a body; others are errors)
#   - bulkError:     True if any item in a bulk response failed (sets success=false)
#   - errorMessage:  detail string for error responses (ignored on 2xx)
#   - result:        the payload (a list for bulk endpoints, an object otherwise)

SPEC_VERSION = "1.0"

def handleResponse(request, errorCode, isBulk, bulkError, error, result):
	# Serialize a handler's (code, bulkError, error, result) tuple into the i3X
	# response envelope and send it (gzipped when the client asks for it).
	resp = request["servletResponse"]
	resp.setStatus(errorCode)

	# 2xx (200 OK, 206 Partial Content) carry the success/result envelope.
	if 200 <= errorCode < 300:
		ret = {
			"success": not bulkError,
			"results" if isBulk else "result": result
		}
	else:
		# Everything else uses the spec ErrorResponse {success:false, responseDetail}.
		ret = {
			"success": False,
			"responseDetail": i3x.utils.errorDetail(errorCode, error)
		}

	return _respond(request, system.util.jsonEncode(ret))

def _respond(request, jsonStr):
	# Send the JSON body, honoring Accept-Encoding: gzip per the spec.
	# This runs outside the handler try/except, so any failure must fall back to
	# an uncompressed response rather than surfacing as a 500.
	try:
		servletRequest = request.get("servletRequest", None)
		acceptEncoding = servletRequest.getHeader("Accept-Encoding") if servletRequest is not None else None
		if acceptEncoding is not None and "gzip" in str(acceptEncoding).lower():
			from java.lang import String
			from java.io import ByteArrayOutputStream
			from java.util.zip import GZIPOutputStream
			baos = ByteArrayOutputStream()
			gz = GZIPOutputStream(baos)
			gz.write(String(jsonStr).getBytes("UTF-8"))
			gz.close()
			# Returning a byte[] under 'response' makes WebDev base64-encode it,
			# so write the raw gzip bytes straight to the servlet stream instead.
			resp = request["servletResponse"]
			resp.setHeader("Content-Encoding", "gzip")
			resp.setContentType("application/json")
			out = resp.getOutputStream()
			out.write(baos.toByteArray())
			out.flush()
			return None
	except:
		system.util.getLogger("i3x").warn("gzip encoding failed; sending uncompressed response")

	return {'json': jsonStr}

def serverError(loggerName):
	# Build a generic 500 tuple after logging the real traceback server-side, so
	# internal details (paths, stack frames) never leak to the client.
	import traceback
	system.util.getLogger(loggerName).error(traceback.format_exc())
	return (500, False, "Internal server error", None)

def bulkOk(result, elementId=None, subscriptionId=None):
	# Build a successful BulkResultItem. elementId/subscriptionId are included
	# only when relevant to the endpoint (both are optional in the spec).
	item = {"success": True, "result": result}
	if elementId is not None:
		item["elementId"] = elementId
	if subscriptionId is not None:
		item["subscriptionId"] = subscriptionId
	return item

def bulkErr(status, detail, elementId=None, subscriptionId=None):
	# Build a failed BulkResultItem carrying a spec ErrorDetail under responseDetail.
	item = {"success": False, "result": None, "responseDetail": i3x.utils.errorDetail(status, detail)}
	if elementId is not None:
		item["elementId"] = elementId
	if subscriptionId is not None:
		item["subscriptionId"] = subscriptionId
	return item

def getInfo():
	# GET /info: server version and capability matrix. Public health check.
	# Capabilities reflect what is actually implemented; writes are not supported.
	ret = {
		"specVersion": SPEC_VERSION,
		"serverVersion": system.util.getVersion().toString(),
		"serverName": system.tag.readBlocking(["[System]Gateway/SystemName"])[0].value,
		"capabilities": {
			"query": {
				"history": True
			},
			"update": {
				"current": False,
				"history": False
			},
			"subscribe": {
				"stream": True
			}
		}
	}
	return (200, False, None, ret)

def _relationshipTypeTable():
	# Every relationship type the server can answer for, keyed by id. The i3X
	# and Ignition set is fixed here; each declares the elementId of its inverse
	# via reverseOf. Local fork (icc-2026): instances may declare further pairs
	# through their Relationships parameter (i3x.ignition.RELATIONSHIPS_PARAM);
	# those are merged in afterwards and never displace a built-in id.
	relationships = {
		"HasParent":{
			"elementId": "HasParent",
			"relationshipId": "HasParent",
			"displayName": "HasParent",
			"namespaceUri": "https://cesmii.org/i3x",
			"reverseOf": "HasChildren"
		},
		"HasChildren":{
			"elementId": "HasChildren",
			"relationshipId": "HasChildren",
			"displayName": "HasChildren",
			"namespaceUri": "https://cesmii.org/i3x",
			"reverseOf": "HasParent"
		},
		"HasComponent":{
			"elementId": "HasComponent",
			"relationshipId": "HasComponent",
			"displayName": "HasComponent",
			"namespaceUri": "https://cesmii.org/i3x",
			"reverseOf": "ComponentOf"
		},
		"ComponentOf":{
			"elementId": "ComponentOf",
			"relationshipId": "ComponentOf",
			"displayName": "ComponentOf",
			"namespaceUri": "https://cesmii.org/i3x",
			"reverseOf": "HasComponent"
		},
		"InheritedBy":{
			"elementId": "InheritedBy",
			"relationshipId": "InheritedBy",
			"displayName": "InheritedBy",
			"namespaceUri": "https://cesmii.org/i3x",
			"reverseOf": "InheritsFrom"
		},
		"InheritsFrom":{
			"elementId": "InheritsFrom",
			"relationshipId": "InheritsFrom",
			"displayName": "InheritsFrom",
			"namespaceUri": "https://cesmii.org/i3x",
			"reverseOf": "InheritedBy"
		},
		"HasAlarm":{
			"elementId": "HasAlarm",
			"relationshipId": "HasAlarm",
			"displayName": "HasAlarm",
			"namespaceUri": i3x.ignition.IgnitionNamespaceUri,
			"reverseOf": "AlarmOf"
		},
		"AlarmOf":{
			"elementId": "AlarmOf",
			"relationshipId": "AlarmOf",
			"displayName": "AlarmOf",
			"namespaceUri": i3x.ignition.IgnitionNamespaceUri,
			"reverseOf": "HasAlarm"
		}
	}
	for typeId, obj in i3x.ignition.getDeclaredRelationshipTypes().items():
		if typeId not in relationships:
			relationships[typeId] = obj
	return relationships

def getRelationshipTypes(namespaceUri=None, elementIds=None):
	# GET /relationshiptypes (namespaceUri=None) lists all relationship types;
	# POST /relationshiptypes/query (elementIds set) returns them in bulk form.
	relationships = _relationshipTypeTable()

	ret = []
	bulkError = False

	if elementIds == None:
		# List form, optionally filtered by namespace.
		for relationship in relationships:
			obj = relationships[relationship]
			if namespaceUri == None or namespaceUri == obj["namespaceUri"]:
				ret.append(obj)
	else:
		# Bulk form: one result item per requested id, in request order.
		for elementId in elementIds:
			if elementId != None:
				if elementId in relationships:
					ret.append(bulkOk(relationships[elementId], elementId=elementId))
				else:
					bulkError = True
					ret.append(bulkErr(404, "Relationship type not found: %s" % elementId, elementId=elementId))

	return (200, bulkError, None, ret)

def getNamespaces():
	# GET /namespaces: the distinct namespace URIs declared by UDT definitions
	# (via the NamespaceUri parameter), plus the OPC-UA core namespace. The
	# displayName is derived from the URI path for readability.
	import urlparse

	ret = []
	namespaces = []

	tagProviders = i3x.ignition.getTagProviders()

	for tagProvider in tagProviders:
		for row in i3x.ignition.getUdtDefs(tagProvider):
			dtNamespaceUri = i3x.utils.getNamespaceUriParam(row)
			if dtNamespaceUri not in namespaces:
				namespaces.append(dtNamespaceUri)

	coreUAFound = False
	for namespaceUri in namespaces:
		prefix = ""
		if namespaceUri.startswith("https://inductiveautomation.com/"):
			prefix = "Ignition "

		if namespaceUri == i3x.ignition.UaCoreUri:
			coreUAFound = True

		ret.append({"uri":namespaceUri, "displayName":prefix+" ".join([word.capitalize() if word.islower() else word for word in urlparse.urlparse(namespaceUri).path[1:].replace("/", " ").split()])})

	# The OPC-UA core namespace backs the built-in folder type; always expose it.
	if not coreUAFound:
		ret.append({"uri":i3x.ignition.UaCoreUri, "displayName":"UA"})

	return (200, False, None, ret)

def getObjectTypes(namespaceUri=None, elementIds=None):
	# GET /objecttypes (namespaceUri filter) / POST /objecttypes/query (elementIds).
	# Returns the JSON-Schema definition for each type: the three built-in types
	# (folder, tag provider, alarm) plus every UDT definition across all providers.

	# The three built-in (non-UDT) types, registered under literal elementIds.
	folderObj = {"elementId":"folder-type", "displayName":"Folder", "namespaceUri":i3x.ignition.UaCoreUri, "sourceTypeId":"folder-type", "version":"1.0.0", "schema":{"type":"object", "description":"Represents a folder", "related":{"relationshipType":"HasChildren"}}}
	tagProviderObj = {"elementId":"ignition-tag-provider", "displayName":"Tag Provider", "namespaceUri":i3x.ignition.IgnitionNamespaceUri, "sourceTypeId":"ignition-tag-provider", "version":"1.0.0", "schema":{"type":"object", "description":"Represents an Ignition tag provider", "related":{"relationshipType":"HasChildren"}}}
	alarmObj = {"elementId":"ignition-alarm", "displayName":"Alarm", "namespaceUri":i3x.ignition.IgnitionNamespaceUri, "sourceTypeId":"ignition-alarm", "version":"1.0.0", "schema":{"type":"object", "description":"Represents an Ignition alarm", "related":{"relationshipType":"AlarmOf"}, "properties":{
		"source": {
			"type": "string"
		},
		"name": {
			"type": "string"
		},
		"eventId": {
			"type": "string"
		},
		"displayPath": {
			"type": "string"
		},
		"count": {
			"type": "integer"
		},
		"label": {
			"type": "string"
		},
		"lastEventState": {
			"type": "string"
		},
		"notes": {
			"type": "string"
		},
		"priority": {
			"type": "string"
		},
		"state": {
			"type": "string"
		},
		"isActive": {
			"type": "boolean"
		},
		"isAcked": {
			"type": "boolean"
		},
		"isCleared": {
			"type": "boolean"
		},
		"isShelved": {
			"type": "boolean"
		},
		"activeData": {
			"type": "object"
		},
		"clearedData": {
			"type": "object"
		},
		"ackData": {
			"type": "object"
		}
	}}}

	from collections import OrderedDict

	bulkError = False

	# Build (and briefly cache) the full type map keyed by elementId: the three
	# built-in types plus every UDT definition across all providers. Serving
	# from this map lets bulk queries preserve request order and report unknown
	# ids as per-item 404s without decoding caller-supplied strings.
	allTypes = i3x.utils.cacheGet("objectTypes")
	if allTypes is None:
		allTypes = OrderedDict()
		allTypes["folder-type"] = folderObj
		allTypes["ignition-tag-provider"] = tagProviderObj
		allTypes["ignition-alarm"] = alarmObj
		for tagProvider in i3x.ignition.getTagProviders():
			for row in i3x.ignition.getUdtDefs(tagProvider):
				dtNamespaceUri = i3x.utils.getNamespaceUriParam(row)
				dtElementId = i3x.utils.pathToElementId(str(row["fullPath"]))
				allTypes[dtElementId] = {"elementId":dtElementId, "displayName":row["name"], "namespaceUri":dtNamespaceUri, "sourceTypeId":dtElementId, "version":"1.0.0", "schema":i3x.ignition.buildSchema(row, tagProvider, dtNamespaceUri)}
		i3x.utils.cacheSet("objectTypes", allTypes)

	if elementIds is not None:
		# Bulk: preserve request order, per-item 404 for unknown ids.
		ret = []
		for elementId in elementIds:
			if elementId in allTypes:
				ret.append(bulkOk(allTypes[elementId], elementId=elementId))
			else:
				bulkError = True
				ret.append(bulkErr(404, "Object type not found: %s" % elementId, elementId=elementId))
		return (200, bulkError, None, ret)

	# Non-bulk: optionally filter by namespace.
	ret = [obj for obj in allTypes.values() if namespaceUri == None or obj["namespaceUri"] == namespaceUri]
	return (200, False, None, ret)

def _indexByElementId(udtInstances):
	# Map elementId -> instance for direct lookups instead of scanning.
	idx = {}
	for path in udtInstances:
		idx[udtInstances[path]["elementId"]] = udtInstances[path]
	return idx

def _resolveObject(elementId, byElementId):
	# The addressable object for elementId, or None when unknown or when it is an
	# intermediate folder nested under a UDT (not an object in its own right).
	udtInstance = byElementId.get(elementId, None)
	if udtInstance is None or (udtInstance["type"] == "folder" and udtInstance["parentUdt"] != None):
		return None
	return udtInstance

def _bulkPerObject(elementIds, fn):
	# Shared driver for the bulk object endpoints: resolve each requested id and
	# apply fn(elementId, udtInstance, udtInstances) to build its result, wrapping
	# it in a bulk item. Unknown/unaddressable ids become per-item 404s.
	udtInstances = i3x.ignition.getUdtInstances()
	byElementId = _indexByElementId(udtInstances)
	ret = []
	bulkError = False
	for elementId in elementIds:
		udtInstance = _resolveObject(elementId, byElementId)
		if udtInstance is None:
			bulkError = True
			ret.append(bulkErr(404, "Element not found: %s" % elementId, elementId=elementId))
		else:
			ret.append(bulkOk(fn(elementId, udtInstance, udtInstances), elementId=elementId))
	return (200, bulkError, None, ret)

def _currentValue(udtInstance, udtInstances, maxDepth):
	# CurrentValueResult for one object: an alarm's status object, or a UDT's live
	# value with composition children recursed under "components" up to maxDepth.
	# Folders and providers have no value, so report GoodNoData.
	if udtInstance["typeId"] == "ignition-alarm":
		alarmObj = udtInstance["alarmObj"]
		return {"value":alarmObj, "quality":"Good", "timestamp":alarmObj["eventTime"], "isComposition":False}

	# value/quality/timestamp are required by CurrentValueResult.
	elementObj = {"value":None, "quality":"GoodNoData", "timestamp":i3x.utils.formatUtc(system.date.now()), "isComposition":udtInstance["isComposition"]}
	if udtInstance["typeId"] != "folder-type" and udtInstance["typeId"] != "ignition-tag-provider":
		childrenValues = {}
		quality = None
		timestamp = None
		value = system.tag.readBlocking([udtInstance["path"]])[0]
		value = i3x.ignition.getTagValue(udtInstance, value)
		if value != None:
			elementObj.update(value["value"])
			childrenValues = value["childrenValues"]
			quality = value["value"]["quality"]
			timestamp = value["value"]["timestamp"]
		i3x.utils.addChildrenValues(udtInstances, udtInstance, elementObj, childrenValues, quality, timestamp, 2, maxDepth)
	return elementObj

def _queryHistory(tags, startDate, endDate):
	# Return raw stored historian points, forward-filled into composite rows.
	#
	# Each tag is queried individually: multi-path queryRawPoints returns nothing
	# on this historian (per-tag stored timestamps don't align), whereas a
	# single-path WIDE query returns every stored point. includeBounds=True also
	# captures each tag's value carried in from just before the window.
	#
	# Output has one row per distinct in-window change timestamp (the union across
	# all tags). At each row every tag shows its last recorded value as of that
	# timestamp (last-observation-carried-forward) rather than null - so a tag
	# that changes rarely (e.g. HOA) keeps showing its value across the many rows
	# where a frequently-changing tag (e.g. Amps) moves. A tag is null only before
	# its first recorded value.
	startMillis = startDate.getTime()
	endMillis = endDate.getTime()

	series = {}          # tag -> [(millis, value), ...] sorted ascending (all points, incl. bounds)
	changeMillis = set() # in-window timestamps where some tag actually has a point
	tsByKey = {}         # in-window millis -> original timestamp Date
	for tag in tags:
		res = system.historian.queryRawPoints(paths=[tag], startTime=startDate, endTime=endDate, returnFormat="WIDE", includeBounds=True)
		cols = res.getColumnNames()
		pts = []
		if len(cols) >= 2:
			for row in res:
				timestamp = row[0]
				key = timestamp.getTime()
				pts.append((key, row[1]))
				# Only in-window points create rows; out-of-window (bound) points
				# still seed the carry-forward for the earliest in-window rows.
				if startMillis <= key <= endMillis:
					changeMillis.add(key)
					tsByKey[key] = timestamp
		pts.sort()
		series[tag] = pts

	# Walk the change timestamps in order, advancing each tag's carried value.
	historyValues = []
	idx = dict((tag, 0) for tag in tags)
	last = dict((tag, None) for tag in tags)
	for key in sorted(changeMillis):
		rowValues = {}
		for tag in tags:
			pts = series[tag]
			i = idx[tag]
			while i < len(pts) and pts[i][0] <= key:
				last[tag] = pts[i][1]
				i += 1
			idx[tag] = i
			rowValues[tag] = last[tag]
		rowValues["t_stamp"] = tsByKey[key]
		historyValues.append(rowValues)
	return historyValues

def _reviewHistory(udtInstance, startDate, endDate, log):
	# Local fork (icc-2026). HistoricalValueResult entries for one vessel's review
	# object, read from lims.review_event instead of the tag historian.
	#
	# The stored document is ALREADY the object's member document, in the encoding
	# /objects/value uses, so it is handed back untouched. This side deliberately
	# does no mapping: the shape is defined once, in model_feed.write_review, and
	# the i3x project cannot import that module -- neither project inherits the
	# other -- so a mapping written here would be a second copy free to drift from
	# the writer. Storing it pre-shaped is what buys the passthrough.
	#
	# Scoped to the vessel via its nearest UDT ancestor: the review hangs under
	# <vessel>/qc_data, and equipment_id carries the vessel name ("br-201").
	# Unscoped, every vessel would answer with every vessel's reviews.
	if udtInstance["parentUdt"] == None:
		log.warn("Review history for %s: no parent vessel, returning empty" % udtInstance["elementId"])
		return []
	equipmentId = str(udtInstance["parentUdt"]).split("/")[-1]

	# Ordered by ts (the acquisition instant), oldest first, as the spec wants a
	# HistoricalValueResult. A failure here logs and returns empty rather than
	# 500ing the whole bulk request, exactly as the alarm-journal branch does.
	try:
		rows = system.db.runPrepQuery(
			"SELECT ts, document FROM lims.review_event "
			"WHERE equipment_id = ? AND ts BETWEEN ? AND ? ORDER BY ts",
			[equipmentId, startDate, endDate], i3x.ignition.LIMS_DATASOURCE)
	except:
		import traceback
		log.warn("Review history query failed for %s (datasource '%s'): %s" % (udtInstance["elementId"], i3x.ignition.LIMS_DATASOURCE, traceback.format_exc().splitlines()[-1]))
		return []

	values = []
	for row in rows:
		values.append({"value":system.util.jsonDecode(str(row["document"])), "quality":"Good", "timestamp":i3x.utils.formatUtc(row["ts"])})
	return values

def _historyValue(udtInstance, udtInstances, maxDepth, startDate, endDate, log):
	# HistoricalValueResult for one object: alarm-journal events for an alarm, or
	# historian values for a UDT's tags (recursed to maxDepth under "components").
	elementObj = {"values":[], "isComposition":udtInstance["isComposition"]}
	udtInstancePath = udtInstance["path"]

	def childHistory(childInstance):
		# Local fork (icc-2026): give the composition children the same dispatch
		# this function makes at its top level, so an object is the same object
		# whether it is asked for by id or reached through its parent. Without it
		# the children path is historian-only, and a review read through its vessel
		# came back as forward-filled member rows -- nulls where the live object had
		# values, and no `document` member at all, that one not being historised.
		# None means "not special": leave the child to the generic path.
		if str(childInstance["typeId"]).endswith(i3x.ignition.REVIEW_TYPE):
			return _reviewHistory(childInstance, startDate, endDate, log)
		return None

	if udtInstance["typeId"] == "ignition-alarm":
		# Alarm history comes from the alarm journal profile named by
		# i3x.ignition.ALARM_JOURNAL, filtered to this alarm's source. If the
		# journal is missing or misconfigured, log a clear warning and return
		# empty history rather than failing the whole request with a 500.
		try:
			res = system.alarm.queryJournal(startDate, endDate, journalName=i3x.ignition.ALARM_JOURNAL, source=udtInstancePath)
			for row in res:
				alarmObj = i3x.ignition.getAlarmObj(row)
				elementObj["values"].append({"value":alarmObj, "quality":"Good", "timestamp":alarmObj["eventTime"], "isComposition":False})
		except:
			import traceback
			log.warn("Alarm journal query failed for %s (journal '%s'): %s" % (udtInstance["elementId"], i3x.ignition.ALARM_JOURNAL, traceback.format_exc().splitlines()[-1]))
	elif str(udtInstance["typeId"]).endswith(i3x.ignition.REVIEW_TYPE):
		# Local fork (icc-2026): this object's history is an event store, not the
		# tag historian. See _reviewHistory and i3x.ignition.REVIEW_TYPE.
		elementObj["values"] = _reviewHistory(udtInstance, startDate, endDate, log)
	elif udtInstance["typeId"] != "folder-type" and udtInstance["typeId"] != "ignition-tag-provider":
		# Collect the historizable leaf tags (recursing to maxDepth), query the
		# historian for all of them at once, then shape into the response.
		tagConfig = system.tag.getConfiguration(udtInstancePath, True)
		if len(tagConfig) and "tags" in tagConfig[0]:
			objs = {"tags":[], "objects":{}}
			tags = i3x.utils.getTags(udtInstancePath, objs, tagConfig[0]["tags"], 1, maxDepth)
			if len(tags):
				historyValues = _queryHistory(tags, startDate, endDate)
				i3x.utils.addChildrenHistory(elementObj, objs, historyValues, udtInstances, childHistory)
	return elementObj

def _listObjects(typeId, includeMetadata, root):
	# GET /objects: list every addressable object, optionally filtered by type id
	# or restricted to roots. Intermediate folders nested under a UDT are skipped.
	if typeId != None:
		typeId = i3x.utils.elementIdToPath(typeId)
	udtInstances = i3x.ignition.getUdtInstances()
	ret = []
	for udtInstancePath in udtInstances:
		udtInstance = udtInstances[udtInstancePath]
		if typeId != None and typeId != udtInstance["typeId"]:
			continue
		if udtInstance["type"] == "folder" and udtInstance["parentUdt"] != None:
			continue
		if root and udtInstance["parentId"] != None:
			continue
		ret.append(i3x.utils.buildUdtInstanceObj(udtInstance, includeMetadata))
	return (200, False, None, ret)

def _listObjectsByIds(elementIds, includeMetadata):
	# POST /objects/list: the object record for each requested elementId.
	return _bulkPerObject(elementIds, lambda elementId, udtInstance, udtInstances: i3x.utils.buildUdtInstanceObj(udtInstance, includeMetadata))

def _relatedObjects(elementIds, relationshipType, includeMetadata):
	# POST /objects/related: objects reachable from each requested object across
	# every edge type (optionally filtered by relationshipType).
	edgeTypes = ("HasParent", "AlarmOf", "ComponentOf", "HasChildren", "HasComponent", "HasAlarm")
	# Local fork (icc-2026): declared pairs are walked after the structural set.
	edgeTypes = edgeTypes + tuple(sorted(i3x.ignition.getDeclaredRelationshipTypes()))
	def related(elementId, udtInstance, udtInstances):
		retObj = []
		for edge in edgeTypes:
			retObj.extend(i3x.utils.getRelatedObjects(relationshipType, edge, udtInstance, udtInstances, includeMetadata))
		return retObj
	return _bulkPerObject(elementIds, related)

def _objectValues(elementIds, maxDepth):
	# POST /objects/value: current value for each requested object.
	return _bulkPerObject(elementIds, lambda elementId, udtInstance, udtInstances: _currentValue(udtInstance, udtInstances, maxDepth))

def _objectHistory(elementIds, maxDepth, startTime, endTime):
	# POST /objects/history: historical values for each requested object over the
	# RFC 3339 range [startTime, endTime] (both required, validated up front).
	if startTime == None or endTime == None:
		return (400, False, "startTime and endTime are required (RFC 3339)", None)
	try:
		startDate = i3x.utils.parseUtc(startTime)
		endDate = i3x.utils.parseUtc(endTime)
	except:
		return (400, False, "startTime and endTime must be RFC 3339 timestamps", None)

	log = system.util.getLogger("i3x.objects")
	return _bulkPerObject(elementIds, lambda elementId, udtInstance, udtInstances: _historyValue(udtInstance, udtInstances, maxDepth, startDate, endDate, log))

def getObjects(typeId=None, includeMetadata=False, root=None, elementIds=None, callType="list", relationshipType=None, maxDepth=1, startTime=None, endTime=None):
	# Entry point for every Object endpoint; dispatches to a focused helper.
	# elementIds is None for the non-bulk GET /objects listing; otherwise this is
	# a bulk request keyed by elementId and callType selects the operation:
	#   list    - POST /objects/list
	#   related - POST /objects/related
	#   value   - POST /objects/value
	#   history - POST /objects/history
	if elementIds is None:
		return _listObjects(typeId, includeMetadata, root)
	if callType == "related":
		return _relatedObjects(elementIds, relationshipType, includeMetadata)
	if callType == "value":
		return _objectValues(elementIds, maxDepth)
	if callType == "history":
		return _objectHistory(elementIds, maxDepth, startTime, endTime)
	return _listObjectsByIds(elementIds, includeMetadata)

def _subscribeItem(subscription, elementId, maxDepth, udtInstances):
	# Expand to the element plus its composition descendants (per maxDepth) and
	# subscribe a listener to each, so every child streams its own update.
	expanded = i3x.utils.expandMonitoredItem(elementId, maxDepth, udtInstances)
	listeners = []
	for (listenElementId, tagPath, udtInstance) in expanded:
		listener = i3x.tag.subscribe(listenElementId, tagPath, udtInstance, subscription)
		listeners.append((tagPath, udtInstance, listener))
	subscription["listeners"][elementId] = listeners
	subscription["monitoredItems"][elementId] = maxDepth

def _unsubscribeItem(subscription, elementId):
	# Tear down every listener created for a monitored item and forget it.
	for (tagPath, udtInstance, listener) in subscription["listeners"].get(elementId, []):
		i3x.tag.unsubscribe(tagPath, udtInstance, listener)
	if elementId in subscription["listeners"]:
		del subscription["listeners"][elementId]
	if elementId in subscription["monitoredItems"]:
		del subscription["monitoredItems"][elementId]

def streamSubscription(request, requestData):
	# POST /subscriptions/stream: push staged updates to the client as Server-Sent
	# Events (at-most-once, no sequence numbers). Reuses the same staged-update
	# queue that tag-change listeners feed; sync is blocked while a stream is open.
	# Writes directly to the servlet stream and returns None so WebDev doesn't
	# append its own body. NOTE: this holds a gateway web thread for the life of
	# the stream (WebDev has no async-servlet API) - fine for a modest number of
	# concurrent streams, not thousands.
	import time
	from java.lang import String
	log = system.util.getLogger("i3x.subscriptions")

	clientId = requestData.get("clientId", None)
	if clientId is None:
		return handleResponse(request, 400, False, False, "clientId is required", None)
	subscriptionId = requestData.get("subscriptionId", None)
	subscriptionIds = i3x.utils.getSubscriptions(clientId)
	if subscriptionId is None or subscriptionId not in subscriptionIds:
		return handleResponse(request, 404, False, False, "Subscription not found", None)

	subscription = subscriptionIds[subscriptionId]

	# Single stream per subscription: bump the token so any stream already running
	# for this subscription sees the change on its next poll and exits.
	with subscription["lock"]:
		subscription["streamToken"] = subscription.get("streamToken", 0) + 1
		myToken = subscription["streamToken"]
		subscription["streaming"] = True

	resp = request["servletResponse"]
	resp.setStatus(200)
	resp.setContentType("text/event-stream")
	resp.setHeader("Cache-Control", "no-cache")
	out = resp.getOutputStream()

	POLL_SECONDS = 0.25
	HEARTBEAT_POLLS = 40   # ~10s of idle between keep-alive comments
	idle = 0
	try:
		while True:
			with subscription["lock"]:
				# A newer stream for this subscription has taken over - close this one.
				if subscription["streamToken"] != myToken:
					break
				staged = subscription["stagedUpdates"]
				updates = list(staged)
				staged.clear()

			# One SSE event per drain: a JSON array of {elementId,value,quality,timestamp}.
			if updates:
				out.write(String("data: %s\n\n" % system.util.jsonEncode(updates)).getBytes("UTF-8"))
				out.flush()
				idle = 0
			else:
				idle += 1
				if idle >= HEARTBEAT_POLLS:
					# Comment line: keeps the connection alive and surfaces a client
					# disconnect (flush throws) during idle periods.
					out.write(String(": keep-alive\n\n").getBytes("UTF-8"))
					out.flush()
					idle = 0

			time.sleep(POLL_SECONDS)
	except:
		import traceback
		log.info("Stream closed for subscription %s: %s" % (subscriptionId, traceback.format_exc().splitlines()[-1]))
	finally:
		# Only clear the flag if we're still the active stream (a newer stream may
		# have superseded us and now owns the streaming state).
		with subscription["lock"]:
			if subscription.get("streamToken", 0) == myToken:
				subscription["streaming"] = False

	return None

def getSubscriptions(callType, requestData=None):
	# Backs every /subscriptions endpoint, dispatched by callType
	# (create/list/delete/register/unregister/sync). Subscriptions are scoped to
	# a clientId so one client can never see or mutate another's subscriptions.
	log = system.util.getLogger("i3x.subscriptions")

	bulkError = False

	clientId = requestData.get("clientId", None)
	if clientId is None:
		return (400, False, "clientId is required", None)

	subscriptionIds = i3x.utils.getSubscriptions(clientId)
	if callType == "list":
		# Report each requested subscription's monitored items, or a 404 per id.
		ret = []
		for subscriptionId in requestData.get("subscriptionIds", []):
			if subscriptionId in subscriptionIds:
				subscription = subscriptionIds[subscriptionId]
				monitoredObjects = [{"elementId":eid, "maxDepth":md} for eid, md in subscription["monitoredItems"].items()]
				obj = {"subscriptionId":subscriptionId, "displayName":subscription["displayName"], "monitoredObjects":monitoredObjects}
				ret.append(bulkOk(obj, subscriptionId=subscriptionId))
			else:
				bulkError = True
				ret.append(bulkErr(404, "Subscription not found: %s" % subscriptionId, subscriptionId=subscriptionId))
	elif callType == "create":
		displayName = requestData.get("displayName", clientId)
		subscriptionId = i3x.utils.createSubscription(clientId, displayName)
		ret = {"clientId":clientId, "subscriptionId":subscriptionId, "displayName":displayName}
	elif callType == "delete":
		ret = []
		for subscriptionId in requestData.get("subscriptionIds", []):
			if subscriptionId in subscriptionIds:
				subscription = subscriptionIds[subscriptionId]
				with subscription["lock"]:
					for elementId in list(subscription["monitoredItems"].keys()):
						_unsubscribeItem(subscription, elementId)
				i3x.utils.deleteSubscription(clientId, subscriptionId)
				ret.append(bulkOk(None, subscriptionId=subscriptionId))
			else:
				bulkError = True
				ret.append(bulkErr(404, "Subscription not found: %s" % subscriptionId, subscriptionId=subscriptionId))
	elif callType == "register":
		subscriptionId = requestData.get("subscriptionId", None)
		inElementIds = requestData.get("elementIds", [])
		maxDepth = requestData.get("maxDepth", 1)
		if maxDepth is None:
			maxDepth = 1

		if subscriptionId == None or subscriptionId not in subscriptionIds:
			return (404, False, "Subscription not found", None)

		ret = []
		udtInstances = i3x.ignition.getUdtInstances()
		validElementIds = set(inst["elementId"] for inst in udtInstances.values())
		subscription = subscriptionIds[subscriptionId]

		for elementId in inElementIds:
			if elementId not in validElementIds:
				bulkError = True
				ret.append(bulkErr(404, "Element not found: %s" % elementId, elementId=elementId, subscriptionId=subscriptionId))
			else:
				with subscription["lock"]:
					# Re-registering replaces the prior monitor so its listeners
					# are cleaned up rather than leaked (registration is idempotent).
					if elementId in subscription["monitoredItems"]:
						_unsubscribeItem(subscription, elementId)
					_subscribeItem(subscription, elementId, maxDepth, udtInstances)
				ret.append(bulkOk(None, elementId=elementId, subscriptionId=subscriptionId))
	elif callType == "unregister":
		subscriptionId = requestData.get("subscriptionId", None)
		inElementIds = requestData.get("elementIds", [])

		if subscriptionId == None or subscriptionId not in subscriptionIds:
			return (404, False, "Subscription not found", None)

		ret = []
		subscription = subscriptionIds[subscriptionId]
		for elementId in inElementIds:
			if elementId not in subscription["monitoredItems"]:
				bulkError = True
				ret.append(bulkErr(404, "Element not found: %s" % elementId, elementId=elementId, subscriptionId=subscriptionId))
			else:
				with subscription["lock"]:
					_unsubscribeItem(subscription, elementId)
				ret.append(bulkOk(None, elementId=elementId, subscriptionId=subscriptionId))
	elif callType == "sync":
		# Acknowledge prior batches and return all pending ones. Staged updates
		# (appended by tag-change listeners) are bundled into a new batch here.
		subscriptionId = requestData.get("subscriptionId", None)
		lastSequenceNumber = requestData.get("lastSequenceNumber", None)

		if subscriptionId == None or subscriptionId not in subscriptionIds:
			return (404, False, "Subscription not found", None)

		subscription = subscriptionIds[subscriptionId]
		with subscription["lock"]:
			# Streaming and sync are mutually exclusive for a subscription; the
			# client must close the stream before polling sync.
			if subscription.get("streaming", False):
				return (409, False, "Subscription has an open stream; close the stream before calling sync", None)

			batches = subscription["batches"]

			# lastSequenceNumber == -1 acknowledges (clears) the entire queue;
			# otherwise drop every batch at or below the client's high-water mark.
			if lastSequenceNumber == -1:
				batches.clear()
			elif lastSequenceNumber != None:
				while batches and batches[0]["sequenceNumber"] <= lastSequenceNumber:
					batches.popleft()

			# Bundle everything staged since the last sync into one new batch.
			staged = subscription["stagedUpdates"]
			if len(staged):
				batch = {"sequenceNumber":subscription["sequenceNumber"], "updates":list(staged)}
				subscription["sequenceNumber"] += 1
				staged.clear()
				wasFull = batches.maxlen is not None and len(batches) == batches.maxlen
				batches.append(batch)
				if wasFull:
					subscription["overflow"] = True

			# 206 signals the client that some updates were dropped on overflow.
			overflow = subscription["overflow"]
			subscription["overflow"] = False
			ret = list(batches)

		return (206 if overflow else 200, False, None, ret)

	return (200, bulkError, None, ret)
