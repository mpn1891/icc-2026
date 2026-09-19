# i3x.utils
# ---------
# Shared helpers used across the i3X handlers: UTC timestamp conversion, the
# error-envelope builder, the short-lived model cache, elementId<->tag-path
# encoding, response shaping for objects/relationships, and subscription state.

# --- Timestamps -------------------------------------------------------------
# All i3X timestamps are RFC 3339 UTC (e.g. "2026-06-30T12:00:00.000Z").
# parseUtc turns an incoming string into an absolute java.util.Date (no zone
# assumptions); formatUtc renders any java.util.Date back as UTC. The previous
# implementation hard-coded America/Los_Angeles, which corrupted timestamps on
# any gateway not running in Pacific time.

def parseUtc(utcTime):
	# RFC 3339 string -> java.util.Date (absolute instant). None passes through.
	from java.time import Instant
	from java.util import Date
	if utcTime is None:
		return None
	# Date.from(...) can't be called from Jython ("from" is a keyword), so build
	# the Date from epoch millis instead.
	return Date(Instant.parse(utcTime).toEpochMilli())

def formatUtc(date):
	# java.util.Date -> RFC 3339 UTC string. None passes through.
	from java.time import ZoneOffset
	from java.time.format import DateTimeFormatter
	if date is None:
		return None
	formatter = DateTimeFormatter.ofPattern(i3x.ignition.DATE_FORMAT).withZone(ZoneOffset.UTC)
	return formatter.format(date.toInstant())

# --- Error helpers ----------------------------------------------------------
# The i3X spec models errors as ErrorDetail {title, status, detail}.
REASON_PHRASES = {
	400: "Bad Request",
	401: "Unauthorized",
	403: "Forbidden",
	404: "Not Found",
	409: "Conflict",
	500: "Internal Server Error",
	501: "Not Implemented",
	503: "Service Unavailable"
}

def errorDetail(status, detail, title=None):
	# Build an ErrorDetail, defaulting title/detail to the status reason phrase.
	return {
		"title": title if title else REASON_PHRASES.get(status, "Error"),
		"status": status,
		"detail": detail if detail else REASON_PHRASES.get(status, "Error")
	}

# --- Model cache ------------------------------------------------------------
# The structural model (instances, type schemas) is expensive to rebuild on
# every request. We cache it briefly in gateway globals. Live values, history
# and alarm status are always read fresh; only the structure map is cached.
CACHE_TTL_MS = 5000

def getCacheStore():
	# Lazily create the shared cache (a dict + lock) in gateway globals.
	from threading import RLock
	g = system.util.getGlobals()
	store = g.get("i3x.cache", None)
	if store is None:
		store = {"data": {}, "lock": RLock()}
		g["i3x.cache"] = store
	return store

def cacheGet(key):
	# Return the cached value for key, or None if absent or expired.
	from java.lang import System
	store = getCacheStore()
	with store["lock"]:
		entry = store["data"].get(key, None)
		if entry is None:
			return None
		expiry, value = entry
		if System.currentTimeMillis() > expiry:
			return None
		return value

def cacheSet(key, value, ttlMs=CACHE_TTL_MS):
	# Cache value under key for ttlMs milliseconds.
	from java.lang import System
	store = getCacheStore()
	with store["lock"]:
		store["data"][key] = (System.currentTimeMillis() + ttlMs, value)

def cacheClear():
	# Drop everything from the cache (e.g. after bulk UDT changes).
	store = getCacheStore()
	with store["lock"]:
		store["data"].clear()

def setStatus(request, code, error):
	# Low-level helper to set a status and return a raw JSON body.
	response = request['servletResponse']
	response.setStatus(code)
	return {"json":system.util.jsonEncode(error)}

# --- ElementId <-> tag path -------------------------------------------------
# An elementId is the URL-safe base64 of a tag path, giving every object a
# stable, opaque, URL-safe identifier that round-trips back to its path.

def pathToElementId(path):
	# Tag path -> URL-safe base64 elementId (no padding).
	from java.util import Base64
	return Base64.getUrlEncoder().withoutPadding().encodeToString(path)

def elementIdToPath(elementId):
	# elementId -> tag path. Raises on malformed base64 (callers must guard).
	from java.util import Base64
	from java.lang import String
	from java.nio.charset import StandardCharsets
	return str(String(Base64.getUrlDecoder().decode(elementId), StandardCharsets.UTF_8))

def tagPathGen1ToGen2(tagPath):
	# Convert a Gen1 path "[provider]Folder/Tag" to a Gen2 path
	# "prov:provider:/tag:Folder/Tag" (the form the historian expects).
	import re
	match = re.search(r"\[(.*?)\]", tagPath)
	tagProvider = "default"
	if match:
		tagProvider = match.group(1)
		tagPath = re.sub(r"\[.*?\]", "", tagPath)
	return "prov:%s:/tag:%s" % (tagProvider, tagPath)

def tagPathGen2ToGen1(tagPath):
	# Inverse of tagPathGen1ToGen2: Gen2 path -> "[provider]Folder/Tag".
	parts = tagPath.split(":/")
	if len(parts) > 1:
		tagPath = "[%s]%s" % (parts[0].replace("prov:", ""), parts[1].replace("tag:", ""))
	else:
		tagPath = "[default]%s" % parts[0].replace("tag:", "")
	return tagPath

def alarmSourceToStateTagPath(source):
	# Turn an alarm source qualifier into the alarm's .State tag path, which is
	# what we subscribe to for alarm change notifications.
	parts = source.split(":/")
	return "[%s]%s/Alarms/%s.State" % (parts[0].replace("prov:", ""), parts[1].replace("tag:", ""), parts[2].replace("alm:", ""))

def getTagProviderFromPath(path):
	# Extract the provider name from a "[provider]..." path, or None.
	import re
	match = re.search(r"\[(.*?)\]", path)
	if match:
		return match.group(1)
	return None

def getTagNameFromPath(path):
	# Return the final, provider-stripped segment of a tag path (the tag name).
	import re
	if path != None and path != "":
		pathParts = path.split("/")
		tagName = re.sub(r"\[.*?\]", "", pathParts[-1])
		return tagName

	return None

def getNamespaceUriParam(row):
	# Read a UDT's NamespaceUri parameter, defaulting to the Ignition UDT
	# namespace when the parameter isn't present.
	from com.inductiveautomation.ignition.common.tags.config.properties import ParameterValue
	from com.inductiveautomation.ignition.common.sqltags.model.types import DataTypeClass
	if "parameters" in row and row["parameters"] != None:
		paramValue = row["parameters"].get("NamespaceUri", ParameterValue(DataTypeClass.String, i3x.ignition.IgnitionNamespaceUri))
		return paramValue if isinstance(paramValue, (int, float, long, bool, str, unicode, type(None))) else paramValue.value
	else:
		return i3x.ignition.IgnitionNamespaceUri

def buildUdtInstanceObj(udtInstance, includeMetadata):
	# Shape an internal udtInstance record into an i3X ObjectInstanceResponse.
	# includeMetadata adds type provenance, relationships, and UDT parameters.
	typeId = udtInstance["typeId"]
	# Built-in types are registered under their literal ids; only real UDT type
	# paths get base64-encoded into elementIds. Encoding the built-ins here would
	# make typeElementId fail to resolve against GET /objecttypes.
	if typeId not in ["ignition-alarm", "folder-type", "ignition-tag-provider"]:
		typeId = pathToElementId(typeId)
	obj = {"elementId":udtInstance["elementId"], "typeElementId":typeId, "displayName":udtInstance["name"], "parentId":udtInstance["parentId"], "isComposition":udtInstance["isComposition"], "isExtended":len(udtInstance["parameters"]) > 0}
	if includeMetadata:
		obj["metadata"] = {
			"typeNamespaceUri": udtInstance["namespaceUri"],
			"sourceTypeId":typeId
		}

		if len(udtInstance["relationships"]):
			obj["metadata"]["relationships"] = udtInstance["relationships"]

		if len(udtInstance["parameters"]):
			obj["metadata"]["system"] = {
				"parameters": udtInstance["parameters"]
			}

	return obj

def getRelatedObjects(filterRelationshipType, relationshipType, obj, udtInstances, includeMetadata):
	# Return RelatedObjectResult entries for one relationship type on obj. When
	# filterRelationshipType is set, only that relationship is emitted. Handles
	# both to-many (list) and to-one (scalar) edges.
	ret = []

	if filterRelationshipType == None or filterRelationshipType == relationshipType:
		if relationshipType in obj["relationships"]:
			if isinstance(obj["relationships"][relationshipType], list):
				for rChildPath in obj["relationships"][relationshipType]:
					rObj = i3x.utils.buildUdtInstanceObj(udtInstances[i3x.utils.elementIdToPath(rChildPath)], includeMetadata)
					ret.append({
						"sourceRelationship":relationshipType,
						"object":rObj
					})
			else:
				if obj["relationships"][relationshipType] != "/":
					rObj = i3x.utils.buildUdtInstanceObj(udtInstances[i3x.utils.elementIdToPath(obj["relationships"][relationshipType])], includeMetadata)
					ret.append({
						"sourceRelationship":relationshipType,
						"object":rObj
					})

	return ret

def getChildrenObjects(obj, relationshipType):
	# Tag paths of obj's related children for a relationship type.
	ret = []
	if relationshipType in obj["relationships"]:
		ret = [i3x.utils.elementIdToPath(rChildPath) for rChildPath in obj["relationships"][relationshipType]]
	return ret

def getChildrenObjectNames(obj, relationshipType):
	# Like getChildrenObjects but returns names relative to obj (path prefix
	# stripped) - the keys under which children appear in a UDT's value document.
	ret = []
	objPath = obj["path"] + "/"
	if relationshipType in obj["relationships"]:
		ret = [i3x.utils.elementIdToPath(rChildPath).replace(objPath, "") for rChildPath in obj["relationships"][relationshipType]]
	return ret

def removeChildren(objValue, children):
	# Split a UDT value document: pop each composition child's sub-value out of
	# objValue (mutating it) and return those sub-values keyed by child name, so
	# the parent value carries only its own members and children recurse separately.
	ret = {}
	keysToRemove = []

	for child in children:
		parts = child.split("/")
		if parts[0] in objValue and parts[0] not in keysToRemove:
			keysToRemove.append(parts[0])

		childValue = objValue
		for part in parts:
			if part not in childValue:
				childValue = None
				break
			else:
				childValue = childValue[part]

		ret[child] = childValue

	for key in keysToRemove:
		del objValue[key]

	return ret

def addChildrenValues(udtInstances, udtInstance, elementObj, childrenValues, quality, timestamp, currentDepth, maxDepth):
	# Recursively attach composition child values under elementObj["components"],
	# honoring maxDepth (1=this element only, 0=infinite). childrenValues holds
	# the sub-values already separated out of the parent by removeChildren.
	if currentDepth <= maxDepth or maxDepth == 0:
		objPath = udtInstance["path"] + "/"
		children = getChildrenObjects(udtInstance, "HasComponent")
		for child in children:
			childName = child.replace(objPath, "")
			childElementId = pathToElementId(child)
			subChildrenValues = {}
			value = childrenValues.get(childName, None)
			if value == None:
				value = {"isComposition":udtInstances[child]["isComposition"]}
			else:
				subChildren = getChildrenObjectNames(udtInstances[child], "HasComponent")
				subChildrenValues = removeChildren(value, subChildren)
				value = {"value":value, "quality":quality, "timestamp":timestamp, "isComposition":udtInstances[child]["isComposition"]}

			if "components" not in elementObj:
				elementObj["components"] = {}

			elementObj["components"][childElementId] = value
			addChildrenValues(udtInstances, udtInstances[child], elementObj["components"][childElementId], subChildrenValues, quality, timestamp, currentDepth + 1, maxDepth)

def getTags(path, objs, tags, currentDepth=1, maxDepth=1):
	# Walk a UDT's tag configuration to maxDepth, collecting the Gen2 paths of all
	# historizable atomic tags (flattened across folders). objs accumulates the
	# nested structure (atomic tag paths + nested UDT instances) so history can
	# later be re-shaped per composition child.
	ret = []
	if currentDepth <= maxDepth or maxDepth == 0:
		for tag in tags:
			tagPath = "%s/%s" % (path, tag["path"])
			tagType = str(tag["tagType"])

			if tagType == "Folder":
				ret.extend(getTags(tagPath, objs, tag["tags"], currentDepth, maxDepth))
			elif tagType == "UdtInstance":
				if (currentDepth + 1) <= maxDepth or maxDepth == 0:
					subObj = {"tags":[], "objects":{}}
					objs["objects"][tagPath] = subObj
					ret.extend(getTags(tagPath, subObj, tag["tags"], currentDepth+1, maxDepth))
			elif tagType == "AtomicTag":
				tp = tagPathGen1ToGen2(tagPath)
				ret.append(tp)
				objs["tags"].append(tp)

	return ret

def addChildrenHistory(elementObj, objs, historyValues, udtInstances=None, historyOverride=None):
	# Shape flat historian rows (one column per tag) into HistoricalValueResult
	# records, recursing into nested composition children under "components".
	#
	# Local fork (icc-2026): the last two arguments give this path what
	# addChildrenValues already had -- the instance map, so a child is resolved to
	# the object it actually is. Without them every child was reported
	# isComposition false whatever it was, and every child was read from the
	# historian whatever store holds its truth, so an object answered differently
	# depending on whether it was asked for by id or reached through its parent.
	# historyOverride(childInstance) returns that child's values, or None to leave
	# it to the historian. Both default to None so the upstream call still works.
	if len(objs["tags"]):
		for row in historyValues:
			rowValues = {}
			allNull = True
			for tag in objs["tags"]:
				tagPath = tagPathGen2ToGen1(tag)
				tagName = getTagNameFromPath(tagPath)
				rowValues[tagName] = row[tag]
				if row[tag] is not None:
					allNull = False
			# historyValues carries the union of every tag's timestamps across the
			# whole object tree; skip rows where none of THIS object's own tags
			# have a point (they'd be all-null VQTs from a sibling's timestamp).
			if not allNull:
				elementObj["values"].append({"value":rowValues, "quality":"Good", "timestamp":formatUtc(row["t_stamp"])})

	if len(objs["objects"]):
		elementObj["isComposition"] = True
		for obj in objs["objects"]:
			subObjs = objs["objects"][obj]
			# obj is the child's own path, so it keys the instance map directly.
			# Unresolved (an instance map was not passed, or the browse has not seen
			# this child) falls back to upstream's assumption.
			childInstance = None
			if udtInstances != None:
				childInstance = udtInstances.get(obj, None)
			isComposition = False
			if childInstance != None:
				isComposition = childInstance["isComposition"]
			subElementObj = {"values":[], "isComposition":isComposition}

			if "components" not in elementObj:
				elementObj["components"] = {}

			# obj is already a Gen1 tag path (the getTags key), so encode it
			# directly - running it through tagPathGen2ToGen1 double-prefixed the
			# provider (e.g. "[default][default]CNC1/...") and produced a component
			# elementId that didn't resolve to the real object.
			elementObj["components"][pathToElementId(obj)] = subElementObj

			overrideValues = None
			if historyOverride != None and childInstance != None:
				overrideValues = historyOverride(childInstance)

			if overrideValues != None:
				# The override is this child's whole history: its member tags are not
				# where its record lives, so neither they nor the historian rows have
				# anything to add under it.
				subElementObj["values"] = overrideValues
			else:
				addChildrenHistory(subElementObj, subObjs, historyValues, udtInstances, historyOverride)

# --- Subscription state -----------------------------------------------------
# Subscriptions live in gateway globals, keyed by clientId then subscriptionId,
# so they survive across requests and are isolated per client.

def getSubscriptions(clientId=None):
	# Return the subscription map for a client, creating empty maps as needed.
	globalsObj = system.util.getGlobals()

	if "i3x.subscriptions" not in globalsObj:
		globalsObj["i3x.subscriptions"] = {}

	if clientId != None and clientId not in globalsObj["i3x.subscriptions"]:
		globalsObj["i3x.subscriptions"][clientId] = {}

	return globalsObj["i3x.subscriptions"][clientId]

def createSubscription(clientId, displayName):
	# Create a subscription with empty monitored-item/listener/queue state and
	# return its generated id. See i3x.tag for how the queues are fed/drained.
	from java.util import UUID
	from collections import deque, OrderedDict
	from threading import RLock

	subscriptions = getSubscriptions(clientId)
	uuid = str(UUID.randomUUID())

	subscriptions[uuid] = {
		"displayName": displayName,
		"created": system.date.now(),
		# requested elementId -> maxDepth
		"monitoredItems": OrderedDict(),
		# requested elementId -> [(tagPath, udtInstance, listener), ...]
		"listeners": {},
		# SyncUpdateEntry dicts awaiting the next sync()
		"stagedUpdates": deque(maxlen=i3x.tag.MAX_QUEUE_SIZE),
		# SyncBatch dicts already handed to (or pending for) the client
		"batches": deque(maxlen=i3x.tag.MAX_QUEUE_SIZE),
		"sequenceNumber": 1,
		"overflow": False,
		# SSE streaming state: streaming=True while a stream is open (blocks sync);
		# streamToken bumps on each new stream so an older stream for the same
		# subscription detects it and closes (single stream per subscription).
		"streaming": False,
		"streamToken": 0,
		"lock": RLock()
	}
	return uuid

def expandMonitoredItem(elementId, maxDepth, udtInstances):
	# Returns [(elementId, tagPath, udtInstance), ...] for the element and its
	# HasComponent descendants, honouring maxDepth (1=self only, 0=infinite).
	# Folders and tag providers have no value and are skipped (we still descend
	# through them). This lets each composition child stream its own update.
	ret = []
	tagPath = elementIdToPath(elementId)
	if tagPath not in udtInstances:
		return ret

	def recurse(inst, depth):
		if inst["typeId"] not in ("folder-type", "ignition-tag-provider"):
			ret.append((inst["elementId"], inst["path"], inst))
		if depth < maxDepth or maxDepth == 0:
			for childElementId in inst.get("relationships", {}).get("HasComponent", []):
				childPath = elementIdToPath(childElementId)
				if childPath in udtInstances:
					recurse(udtInstances[childPath], depth + 1)

	recurse(udtInstances[tagPath], 1)
	return ret

def deleteSubscription(clientId, subscriptionId):
	# Remove a subscription record (listeners must be torn down by the caller first).
	subscriptions = getSubscriptions(clientId)
	del subscriptions[subscriptionId]
