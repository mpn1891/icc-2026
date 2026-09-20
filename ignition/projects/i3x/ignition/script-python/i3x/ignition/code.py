# i3x.ignition
# ------------
# The bridge between Ignition's tag/UDT/alarm model and the i3X data model.
# Builds the object graph (folders, UDT instances, alarms and the relationships
# between them), generates JSON-Schema type definitions from UDT definitions,
# and reads live tag values. The heavy structural build (getUdtInstances) is
# cached briefly by i3x.utils; live values and alarm status are read fresh.

IgnitionNamespaceUri = "https://inductiveautomation.com/UDT"
UaCoreUri = "http://opcfoundation.org/UA/"
DATE_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
# Name of the alarm journal profile used for alarm history. Set this to match
# the gateway's configured journal; if none exists, alarm history returns empty.
# Local fork (icc-2026): upstream ships "Journal". This gateway's journal is
# icc26_alarm, on pg_db -- the same connection pg-historian writes to.
ALARM_JOURNAL = "icc26_alarm"
# Local fork (icc-2026).
# The UDT type suffixes whose history comes from an event store, and the
# datasource holding those stores. `i3x.handlers` turns these into a dispatch
# table (`_EVENT_HISTORY`); a type named here is matched by SUFFIX, because a
# typeId carries its provider ("[default]_types_/lims_review").
#
# `lims_review`: thirteen members written from a single MQTT message, so they
# have no independent cadence and the historian's per-member series has to be
# forward-filled back into object states -- which invents states the object was
# never in. `lims.review_event` keeps the member document whole.
#
# `cell_analyzer`: the same disease from the other ingestion. One analysis is
# ~38 OPC nodes the instrument writes as one batch; `result_json` -- the
# vendor's own payload, and the member that makes a row whole -- is not
# historised at all, and five leaf names collide once the historian flattens the
# folders (`sample_id`, `sample_type`, `vessel_id` and `cell_type` exist under
# both `command/` and `result/`, and `osmo` is both the Float8 reading and the
# Boolean module flag). Distinct paths in a stored document cannot collide.
# `qc.analyzer_result` keeps it whole, in the shape /objects/value returns.
#
# Objects whose members DO move independently (a process value and its limits)
# stay on the historian, where forward-fill is the correct semantics.
REVIEW_TYPE = "_types_/lims_review"
ANALYZER_TYPE = "_types_/cell_analyzer"
# One Ignition datasource, both stores -- the `ICC26` connection, which is the
# icc26 database as the icc26 role. NOT `pg_db`, which is the historian's own
# store and will pass a glance in the dropdown before reading nowhere useful.
# Named for what it holds rather than for the first store that used it: it was
# LIMS_DATASOURCE until the analyzer store joined it on 2026-09-19.
EVENT_STORE_DATASOURCE = "ICC26"
# The UDT parameter the analyzer store is keyed by. **The parameter, not the
# instance name**: the instance is `cell-analyzer-01` and its device_id is
# `CELL-ANALYZER-01`. The writer (`opcua_event._device_id`) reads the same
# parameter, so the two sides cannot key on different strings -- get this wrong
# and every query returns [] quietly. Read the same way `_coalesceMillis` reads
# COALESCE_PARAM.
DEVICE_ID_PARAM = "device_id"
# Local fork (icc-2026). This gateway also carries the MQTT Engine, MQTT
# Transmission, MQTT Distributor, pm-sensors and System providers. None of them
# holds a UDT instance the API should expose, so browsing them only adds chaff
# to the i3X address space and makes the structural build behind the 5 s cache
# slower than it needs to be. Restrict the browse to the providers named here.
PROVIDERS = ("default",)
# Local fork (icc-2026). Structure only ever yields containment, composition,
# inheritance and alarm edges; how the plant is wired beyond that (which
# analyzer draws samples from which vessel) is not in the tag tree. A UDT
# instance may declare such edges itself through a String parameter of this
# name holding a JSON list of {"type", "reverse", "targets": [tag paths]}. The
# declaring instance is always the source; the reverse edge on each target is
# filled by the bidirectional pass in getUdtInstances, and both directions are
# to-many. See declaredRelationships, getDeclaredRelationshipTypes and
# PROVENANCE.md.
RELATIONSHIPS_PARAM = "Relationships"
# The relationship ids the server defines itself (i3x.handlers lists them). A
# declaration may not reuse any of these names, in either direction.
BUILTIN_RELATIONSHIP_IDS = ("HasParent", "HasChildren", "HasComponent", "ComponentOf", "InheritedBy", "InheritsFrom", "HasAlarm", "AlarmOf")
# The structural pairs, forward -> reverse in both directions, and which of
# them hold a list. Declared pairs are added to copies of these per build.
BUILTIN_REVERSE = {"HasParent":"HasChildren", "HasChildren":"HasParent", "HasComponent":"ComponentOf", "ComponentOf":"HasComponent", "HasAlarm":"AlarmOf", "AlarmOf":"HasAlarm"}
BUILTIN_TO_MANY = ("HasChildren", "HasComponent", "HasAlarm")
# Local fork (icc-2026). Members written by one message do not necessarily reach
# the historian on one timestamp. Sparkplug carries a payload timestamp that
# Engine applies to every metric in the message, so those members land together;
# a custom namespace has nowhere to put one, so Engine stamps each key as it
# walks the document. Measured 2026-09-19: br-202's valve (Sparkplug) answers one
# sample with one row, br-201's (custom namespace) answers with three rows 10 ms
# apart, the first two holding states the valve was never in -- a sample id with
# no result, then a result with no completion time.
#
# A UDT instance may declare, through an Int8 parameter of this name, the window
# in milliseconds within which distinct stored timestamps are ONE write. It is a
# claim about that instance's writer, which is why it belongs on the instance:
# br-201 and br-202 are the same type reached by different ingestion. 0 -- the
# default, and every object that does not declare it -- keeps upstream's
# behaviour exactly: one row per distinct timestamp. Forward-fill BETWEEN windows
# is untouched, and stays correct. See _queryHistory and PROVENANCE.md.
COALESCE_PARAM = "HistoryCoalesceMs"

def getTagProviders():
	# Names of the tag providers the API exposes (the top level of the address
	# space). Local fork: the gateway is browsed as upstream does, but only the
	# providers listed in PROVIDERS are kept. See the note on that constant.
	tagProviders = []
	res = system.tag.browse("")
	for row in res.getResults():
		name = row["name"]
		if name in PROVIDERS:
			tagProviders.append(name)
	return tagProviders

def getUdtDefs(tagProvider, elementId=None):
	# Query a provider's UDT *definitions* (the types). elementId optionally
	# scopes the query to a single definition path.
	query = {
	  "options": {
	    "includeUdtMembers": True,
	    "includeUdtDefinitions": True
	  },
	  "condition": {
	    "tagType": "UdtType",
	    "attributes": {
	      "values": [],
	      "requireAll": True
	    }
	  },
	  "returnProperties": [
	    "tooltip",
	    "documentation",
	    "parameters",
	    "tagType",
	    "quality"
	  ]
	}

	if elementId != None:
		query["condition"]["path"] = elementId

	return system.tag.query(tagProvider, query)

def getUdtInstancesForTagProvider(tagProvider):
	# Query a provider's UDT *instances* (the objects), including their members.
	query = {
	  "options": {
	    "includeUdtMembers": True,
	    "includeUdtDefinitions": False
	  },
	  "condition": {
	    "tagType": "UdtInstance",
	    "attributes": {
	      "values": [],
	      "requireAll": True
	    }
	  },
	  "returnProperties": [
	    "tooltip",
	    "documentation",
	    "parameters",
	    "tagType",
	    "quality"
	  ]
	}

	return system.tag.query(tagProvider, query)

def addFolders(tagProvider, udtInstances, parentPath):
	# Synthesize folder records for every intermediate path segment leading to
	# parentPath. Tag queries return leaf objects but not the folders containing
	# them, so we materialize those folders so the hierarchy is complete.
	if parentPath != "":
		parentPathParts = parentPath.split("/")
		for i in range(len(parentPathParts)):
			path = "/".join(parentPathParts[0:i+1])
			if path not in udtInstances:
				parentParentPath = "/".join(path.split("/")[:-1])
				if parentParentPath == "":
					parentParentPath = "[%s]" % tagProvider
				name = path.split("/")[-1]
				if "[%s]" % tagProvider in name:
					name = name.replace("[%s]" % tagProvider, "")
				udtInstances[path] = {"path":path, "elementId":i3x.utils.pathToElementId(path), "type":"folder", "tagProvider":tagProvider, "parentPath":parentParentPath, "parentUdt":None, "childrenUdts":[], "alarms":[], "namespaceUri":i3x.ignition.UaCoreUri, "typeId":"folder-type", "name":name, "parameters":{}, "alarmObj":{}}

def findUdtParent(udtInstances, udtInstancePath, udtInstance):
	# Link an instance to its nearest *UDT* ancestor (walking up through any
	# intervening folders) and record it under that parent's children/alarms.
	# This is what models UDT composition (HasComponent) vs plain containment.
	parentPath = udtInstance["parentPath"]

	if parentPath != "":
		if udtInstances[parentPath]["type"] == "udt":
			udtInstance["parentUdt"] = parentPath
			if udtInstance["typeId"] == "ignition-alarm":
				udtInstances[parentPath]["alarms"].append(udtInstancePath)
			else:
				udtInstances[parentPath]["childrenUdts"].append(udtInstancePath)
		else:
			parentPath = udtInstances[parentPath]["parentPath"]
			found = False
			while parentPath != "":
				if udtInstances[parentPath]["type"] == "udt":
					udtInstance["parentUdt"] = parentPath
					if udtInstance["typeId"] == "ignition-alarm":
						udtInstances[parentPath]["alarms"].append(udtInstancePath)
					else:
						udtInstances[parentPath]["childrenUdts"].append(udtInstancePath)

					found = True
					break

				parentPath = udtInstances[parentPath]["parentPath"]

			# No UDT ancestor: attach to the immediate parent folder instead.
			if not found and udtInstance["parentPath"] != "":
				if udtInstance["typeId"] == "ignition-alarm":
					udtInstances[udtInstance["parentPath"]]["alarms"].append(udtInstancePath)
				else:
					udtInstances[udtInstance["parentPath"]]["childrenUdts"].append(udtInstancePath)

def getAlarmDetails(row, key):
	# Flatten one of an alarm event's data maps (ackData/clearedData/activeData)
	# into a plain dict, normalizing the ackUser and eventTime fields and dropping
	# the internal "mode" key.
	if key == "ackData":
		obj = row.getAckData()
	elif key == "clearedData":
		obj = row.getClearedData()
	else:
		obj = row.getActiveData()

	data = dict(obj.getRawValueMap()) if obj != None else {}
	newData = {}
	for k, v in data.items():
		if str(k) == "ackUser":
			val = None if v is None else v.toString()
		elif str(k) == "eventTime":
			val = i3x.utils.formatUtc(v)
		else:
			val = v
		newData[str(k)] = val
	if "mode" in newData:
		del newData["mode"]
	return newData

def getAlarmObj(row):
	# Build the i3X alarm value object from an alarm event/status row. eventTime
	# is taken from the data map matching the alarm's current state.
	alarmObj = {}
	alarmObj["source"] = str(row.getSource())
	alarmObj["name"] = row.getName()
	alarmObj["eventId"] = str(row.eventId)
	alarmObj["displayPath"] = row.getDisplayPath().toString()
	alarmObj["count"] = row.getCount()
	alarmObj["label"] = row.getLabel()
	alarmObj["lastEventState"] = row.getLastEventState().name()
	alarmObj["notes"] = row.getNotes()
	alarmObj["priority"] = row.getPriority().name()
	alarmObj["state"] = row.getState().name()
	alarmObj["isActive"] = row.isActive
	alarmObj["isAcked"] = row.isAcked()
	alarmObj["isCleared"] = row.isCleared()
	alarmObj["isShelved"] = row.isShelved()
	alarmObj["ackData"] = getAlarmDetails(row, "ackData")
	alarmObj["clearedData"] = getAlarmDetails(row, "clearedData")
	alarmObj["activeData"] = getAlarmDetails(row, "activeData")

	eventTime = None
	if row.getState().name() == "ActiveUnacked":
		eventTime = alarmObj["activeData"]["eventTime"]
	elif row.getState().name() == "ActiveAcked":
		eventTime = alarmObj["ackData"]["eventTime"]
	elif row.getState().name() == "ClearUnacked":
		eventTime = alarmObj["clearedData"]["eventTime"]
	elif row.getState().name() == "ClearAcked":
		eventTime = alarmObj["ackData"]["eventTime"]

	alarmObj["eventTime"] = eventTime

	return alarmObj

def parseAlarms(res):
	# Turn alarm status/journal rows into instance records keyed by source,
	# keeping the most recent event when a source appears more than once.
	alarms = {}
	for row in res:
		source = str(row.getSource())
		tagPath = i3x.utils.tagPathGen2ToGen1(str(row.getSource()))
		tagProvider = i3x.utils.getTagProviderFromPath(tagPath)
		name = row.getName()
		alarmObj = getAlarmObj(row)
		rowObj = {"path":source, "elementId":i3x.utils.pathToElementId(source), "type":"udt", "tagProvider":tagProvider, "parentPath":tagPath, "parentUdt":None, "childrenUdts":[], "alarms":[], "namespaceUri":i3x.ignition.IgnitionNamespaceUri, "typeId":"ignition-alarm", "name":name, "parameters":{}, "alarmObj":alarmObj}

		if source not in alarms:
			alarms[source] = rowObj
		elif system.date.isAfter(i3x.utils.parseUtc(alarmObj["eventTime"]), i3x.utils.parseUtc(alarms[source]["alarmObj"]["eventTime"])):
			alarms[source] = rowObj
	return alarms

def getAlarms(tagProvider):
	# Current alarm status for every alarm in a provider, as instance records.
	res = system.alarm.queryStatus(provider=[tagProvider])
	alarms = parseAlarms(res)
	return alarms

def getAlarmFromSource(source):
	# Current alarm status for a single alarm source (used by stream listeners).
	res = system.alarm.queryStatus(source=[source])
	alarms = parseAlarms(res)
	return alarms[source]

def declaredRelationships(udtInstance, udtInstances, log):
	# Local fork (icc-2026). Parse one instance's RELATIONSHIPS_PARAM into a
	# list of (type, reverse, [target elementIds]). Targets are full tag paths
	# ("[default]icc26/..."); a bare path is taken to be in the instance's own
	# provider. Anything unusable is logged and dropped, never raised: a typo in
	# one instance must not blank the whole address space.
	import json
	raw = udtInstance["parameters"].get(RELATIONSHIPS_PARAM, None)
	if raw == None:
		return []
	raw = unicode(raw).strip()
	if raw == "":
		return []
	where = "%s parameter on %s" % (RELATIONSHIPS_PARAM, udtInstance["path"])
	try:
		decls = json.loads(raw)
	except:
		log.warn("%s is not valid JSON; ignored" % where)
		return []
	if not isinstance(decls, list):
		log.warn("%s must be a JSON list; ignored" % where)
		return []

	ret = []
	for decl in decls:
		if not isinstance(decl, dict):
			log.warn("%s: entry %r is not an object; ignored" % (where, decl))
			continue
		relType = decl.get("type", None)
		reverse = decl.get("reverse", None)
		if not relType or not reverse:
			log.warn("%s: an entry needs both type and reverse; ignored" % where)
			continue
		relType = str(relType).strip()
		reverse = str(reverse).strip()
		if relType == reverse or relType in BUILTIN_RELATIONSHIP_IDS or reverse in BUILTIN_RELATIONSHIP_IDS:
			log.warn("%s: %s/%s clashes with a built-in relationship type or with itself; ignored" % (where, relType, reverse))
			continue
		targets = decl.get("targets", [])
		if not isinstance(targets, list):
			targets = [targets]
		targetIds = []
		for target in targets:
			path = unicode(target).strip()
			if not path.startswith("["):
				path = "[%s]%s" % (udtInstance["tagProvider"], path)
			if path not in udtInstances:
				log.warn("%s: target %s is not an object in this provider; dropped" % (where, path))
				continue
			targetIds.append(udtInstances[path]["elementId"])
		if len(targetIds) == 0:
			log.warn("%s: %s has no resolvable targets; ignored" % (where, relType))
			continue
		ret.append((relType, reverse, targetIds))
	return ret

def getDeclaredRelationshipTypes():
	# Local fork (icc-2026). Relationship types that instances have declared
	# (see RELATIONSHIPS_PARAM), keyed by id, read off the cached graph so the
	# type list and the edges never disagree. First declaration of an id wins.
	ret = {}
	udtInstances = getUdtInstances()
	for udtInstancePath in udtInstances:
		for typeId, obj in udtInstances[udtInstancePath].get("declaredRelationshipTypes", {}).items():
			if typeId not in ret:
				ret[typeId] = obj
	return ret

def getUdtInstances():
	# Build the full i3X object graph for every provider: tag-provider roots,
	# folders, UDT instances and alarms, plus the relationships among them.
	# Returns a dict keyed by tag path. Expensive, so cached briefly by i3x.utils.
	#
	# The structural model is expensive to build (browses every provider, every
	# UDT instance, and queries alarm status). Cache it briefly so a burst of
	# requests doesn't rebuild it each time. Live values/history are read fresh
	# elsewhere; only the structure (and last-known alarm status) is cached.
	cached = i3x.utils.cacheGet("udtInstances")
	if cached is not None:
		return cached

	ret = {}
	log = system.util.getLogger("i3x.ignition")

	tagProviders = getTagProviders()
	for tagProvider in tagProviders:
		tpPath = "[%s]" % tagProvider
		tpTypes = "[%s]_types_/" % tagProvider
		udtInstances = {}
		# The provider root presents as a tag-provider "folder" object.
		udtInstances[tpPath] = {"path":tpPath, "elementId":i3x.utils.pathToElementId(tpPath), "type":"folder", "tagProvider":tagProvider, "parentPath":"", "parentUdt":None, "childrenUdts":[], "alarms":[], "namespaceUri":i3x.ignition.IgnitionNamespaceUri, "typeId":"ignition-tag-provider", "name":tagProvider, "parameters":{}, "alarmObj":{}}

		# Alarms are first-class objects, attached later to their owning UDT.
		alarms = getAlarms(tagProvider)
		for alarmTagPath in alarms:
			alarm = alarms[alarmTagPath]
			# parseAlarms sets parentPath to the owning *atomic tag*, but an atomic
			# tag is not an i3X object. Re-parent the alarm to the tag's containing
			# folder/UDT so we don't materialize the owning tag as a folder (which
			# would otherwise be expanded by addFolders and leak into that folder's
			# HasChildren). Alarms on UDT member tags already resolve to the UDT via
			# findUdtParent; this makes alarms on plain-folder tags behave the same.
			containerPath = "/".join(alarm["parentPath"].split("/")[:-1])
			if containerPath == "":
				containerPath = tpPath
			alarm["parentPath"] = containerPath
			udtInstances[alarm["path"]] = alarm

		# UDT instances.
		res = i3x.ignition.getUdtInstancesForTagProvider(tagProvider)
		for row in res:
			udtPath = str(row["fullPath"])
			pathParts = udtPath.split("/")
			parentPath = "/".join(pathParts[:-1])
			if parentPath == "":
				parentPath = "[%s]" % tagProvider
			elementId = i3x.utils.pathToElementId(udtPath)
			dtTypeId = "%s%s" % (tpTypes, row["typeId"])

			# Resolve UDT parameter values (a non-empty set marks the instance as
			# "extended" relative to its type).
			parameters = {}
			if "parameters" in row and row["parameters"] != None and len(row["parameters"]) > 0:
				for param in row["parameters"]:
					paramValue = row["parameters"][param]
					parameters[param] = paramValue if isinstance(paramValue, (int, float, long, bool, str, unicode, type(None))) else paramValue.value

			udtInstances[udtPath] = {"path":udtPath, "elementId":elementId, "type":"udt", "tagProvider":tagProvider, "parentPath":parentPath, "parentUdt":None, "childrenUdts":[], "alarms":[], "namespaceUri":i3x.utils.getNamespaceUriParam(row), "typeId":dtTypeId, "name":row["name"], "parameters":parameters, "alarmObj":{}}

		# We need to expand to all of the folders from the path
		for udtInstancePath in udtInstances:
			udtInstance = udtInstances[udtInstancePath]
			addFolders(tagProvider, udtInstances, udtInstance["parentPath"])

		# We need to find the UDT parent if exists
		for udtInstancePath in udtInstances:
			udtInstance = udtInstances[udtInstancePath]
			findUdtParent(udtInstances, udtInstancePath, udtInstance)

		# Derive each object's primary relationships and flags from the structure
		# built above (parent linkage, composition children, alarms, inheritance).
		for udtInstancePath in udtInstances:
			udtInstance = udtInstances[udtInstancePath]

			if udtInstance["parentUdt"] != None:
				parentId = i3x.utils.pathToElementId(udtInstance["parentUdt"])
			else:
				parentId = None if udtInstance["parentPath"] == "" else i3x.utils.pathToElementId(udtInstance["parentPath"])

			relationships = {}

			if udtInstance["typeId"] == "ignition-alarm":
				relationships["AlarmOf"] = parentId
			elif udtInstance["parentUdt"] != None:
				# Inside a UDT the model is composition, not containment. Emitting
				# HasParent here would reverse (HasParent -> HasChildren) into a
				# HasChildren edge on the UDT, surfacing member UDTs and the
				# intervening (non-addressable) folders as the UDT's children.
				# Member UDTs are components of the nearest UDT ancestor; the
				# intervening folders get no surfaced relationship.
				if udtInstance["type"] == "udt":
					relationships["ComponentOf"] = parentId
			elif parentId != None:
				relationships["HasParent"] = parentId

			if udtInstance["type"] == "folder" and len(udtInstance["childrenUdts"]) > 0:
				relationships["HasChildren"] = [i3x.utils.pathToElementId(path) for path in udtInstance["childrenUdts"]]
			elif udtInstance["type"] == "udt" and len(udtInstance["childrenUdts"]) > 0:
				relationships["HasComponent"] = [i3x.utils.pathToElementId(path) for path in udtInstance["childrenUdts"] if not (udtInstances[path]["typeId"] == "folder-type" and udtInstances[path]["parentUdt"] != None)]

			if len(udtInstance["alarms"]) > 0:
				relationships["HasAlarm"] = [i3x.utils.pathToElementId(path) for path in udtInstance["alarms"]]

			udtInstance["parentId"] = parentId
			udtInstance["relationships"] = relationships
			udtInstance["isComposition"] = udtInstance["type"] == "udt" and len(udtInstance["childrenUdts"]) > 0

		# Declared relationships (local fork, see RELATIONSHIPS_PARAM): edges an
		# instance asserts about itself that the tag tree cannot yield. Each
		# declared pair is registered for the reverse pass below, to-many both
		# ways, and remembered on the declaring instance so the type list served
		# by i3x.handlers and the edges always come from the same build. A pair
		# that contradicts one already registered in this build is dropped.
		REVERSE = dict(BUILTIN_REVERSE)
		TO_MANY = list(BUILTIN_TO_MANY)
		for udtInstancePath in udtInstances:
			udtInstance = udtInstances[udtInstancePath]
			if udtInstance["type"] != "udt" or len(udtInstance["parameters"]) == 0:
				continue
			declaredTypes = {}
			for (relType, reverse, targetIds) in declaredRelationships(udtInstance, udtInstances, log):
				if REVERSE.get(relType, reverse) != reverse or REVERSE.get(reverse, relType) != relType:
					log.warn("%s parameter on %s: %s/%s contradicts an earlier declaration; ignored" % (RELATIONSHIPS_PARAM, udtInstancePath, relType, reverse))
					continue
				existing = udtInstance["relationships"].get(relType, [])
				udtInstance["relationships"][relType] = existing + [tid for tid in targetIds if tid not in existing]
				REVERSE[relType] = reverse
				REVERSE[reverse] = relType
				for name in (relType, reverse):
					if name not in TO_MANY:
						TO_MANY.append(name)
				declaredTypes[relType] = {"elementId":relType, "relationshipId":relType, "displayName":relType, "namespaceUri":udtInstance["namespaceUri"], "reverseOf":reverse}
				declaredTypes[reverse] = {"elementId":reverse, "relationshipId":reverse, "displayName":reverse, "namespaceUri":udtInstance["namespaceUri"], "reverseOf":relType}
			if len(declaredTypes):
				udtInstance["declaredRelationshipTypes"] = declaredTypes

		# Ensure every relationship is stored bidirectionally so the graph is
		# traversable from either node (spec: "All relationships MUST be stored
		# bidirectionally"). This fills missing reverse edges - e.g. the
		# HasChildren reverse of a child's HasParent on a UDT parent - without
		# overwriting any to-one edge already set above.
		byElementId = {}
		for udtInstancePath in udtInstances:
			byElementId[udtInstances[udtInstancePath]["elementId"]] = udtInstances[udtInstancePath]

		for udtInstancePath in udtInstances:
			udtInstance = udtInstances[udtInstancePath]
			srcId = udtInstance["elementId"]
			for rel, targets in list(udtInstance["relationships"].items()):
				if rel not in REVERSE:
					continue
				rev = REVERSE[rel]
				targetList = targets if isinstance(targets, list) else [targets]
				for tgt in targetList:
					if tgt == None or tgt == "/" or tgt not in byElementId:
						continue
					targetRels = byElementId[tgt]["relationships"]
					if rev in TO_MANY:
						existing = targetRels.get(rev)
						if existing == None:
							targetRels[rev] = [srcId]
						elif isinstance(existing, list):
							if srcId not in existing:
								existing.append(srcId)
						elif existing != srcId:
							targetRels[rev] = [existing, srcId]
					elif rev not in targetRels:
						# to-one reverse: fill only if absent, never overwrite
						targetRels[rev] = srcId

		ret.update(udtInstances)

	i3x.utils.cacheSet("udtInstances", ret)
	return ret

def parseTags(path, config):
	# Recursively turn a UDT definition's tag tree into JSON-Schema "properties".
	# Returns (referencedTypes, properties): atomic tags map to typed properties
	# (with defaults/eng limits), folders nest as objects, and nested UDT members
	# become $ref-s to their own type, collected in referencedTypes.
	tagProvider = path.split("/")[0]
	DATA_TYPE_MAPPINGS = {"Int1":"integer", "Int2":"integer", "Int4":"integer", "Int8":"integer", "Float4":"number", "Float8":"number", "Boolean":"boolean", "String":"string", "DateTime":"string", "Int1Array":"array", "Int2Array":"array", "Int4Array":"array", "Int8Array":"array", "Float4Array":"array", "Float8Array":"array", "StringArray":"array", "DateTimeArray":"array", "ByteArray":"array", "DataSet":"string", "Document":"object"}

	types = []
	tags = {}
	if "tags" in config:
		for row in config["tags"]:
			name = row["name"]
			tagType = str(row["tagType"])
			dataType = str(row.get("dataType", "Int4"))

			newPath = "%s/%s" % (path, row["path"])

			if tagType == "AtomicTag":
				tag = {"type":[DATA_TYPE_MAPPINGS.get(dataType, "string"), "null"]}

				if "tooltip" in row and row["tooltip"] != None and row["tooltip"] != "":
					tag["description"] = row["tooltip"]

				if "value" in row:
					if dataType == "DateTime":
						tag["default"] = i3x.utils.formatUtc(row["value"])
					elif dataType == "DateTimeArray":
						newVal = []
						for val in row["value"]:
							newVal.append(i3x.utils.formatUtc(val))
						tag["default"] = newVal
					elif dataType == "DataSet":
						tag["default"] = system.dataset.toCSV(row["value"], True)
					elif dataType == "Document":
						tag["default"] = row["value"].toDict()
					else:
						tag["default"] = row["value"]

				if "engUnit" in row:
					tag["engUnit"] = row["engUnit"]

				if "engLow" in row:
					tag["engLow"] = row["engLow"]

				if "engHigh" in row:
					tag["engHigh"] = row["engHigh"]

				tags[row["name"]] = tag
			elif tagType == "Folder":
				(subTypes, subTags) = parseTags(newPath, row)
				tag = {
					"type":"object",
					"properties":subTags
				}

				if "tooltip" in row and row["tooltip"] != None and row["tooltip"] != "":
					tag["description"] = row["tooltip"]

				tags[row["name"]] = tag

				for subType in subTypes:
					if subType not in types:
						types.append(subType)
			elif tagType == "UdtInstance":
				dtPath = i3x.utils.pathToElementId("%s/%s" % (tagProvider, row["typeId"]))
				types.append(dtPath)

				tag = {"$ref":"#/types/%s" % (dtPath)}

				if "tooltip" in row and row["tooltip"] != None and row["tooltip"] != "":
					tag["description"] = row["tooltip"]

				tags[row["name"]] = tag

	return types, tags

def buildSchema(row, tagProvider, dtNamespaceUri):
	# Build the full JSON-Schema for one UDT definition: its own member
	# properties, inheritance (allOf + $ref to the parent type, with inherited
	# members removed), the related-types block (InheritedBy/InheritsFrom/
	# HasComponent), and the UDT's parameters.
	tpTypes = "[%s]_types_/" % tagProvider
	config = system.tag.getConfiguration(str(row["fullPath"]), True)[0]

	(types, tags) = parseTags(str(row["fullPath"]), config)

	schema = {}

	if "tooltip" in row and row["tooltip"] != None and row["tooltip"] != "":
		schema["description"] = row["tooltip"]

	# Find subtypes that inherit from this definition (its InheritedBy edges).
	query = {
	  "options": {
	    "includeUdtMembers": True,
	    "includeUdtDefinitions": True
	  },
	  "condition": {
	    "hierarchy": {
	      "typeId": str(row["fullPath"]),
	      "relationship": "SubType"
	    },
	    "tagType": "UdtType",
	    "attributes": {
	      "values": [],
	      "requireAll": True
	    }
	  }
	}

	relatedSchema = {}
	res = system.tag.query(tagProvider, query)
	if len(res):
		if "related" not in relatedSchema:
			relatedSchema["related"] = {}

		subTypes = []
		for subType in res:
			subTypes.append(i3x.utils.pathToElementId(str(subType["fullPath"])))

		relatedSchema["related"]["InheritedBy"] = subTypes

	if "typeId" in row and row["typeId"] != None and row["typeId"] != "":
		if "related" not in relatedSchema:
			relatedSchema["related"] = {}

		relatedSchema["related"]["InheritsFrom"] = i3x.utils.pathToElementId("%s%s" % (tpTypes, row["typeId"]))

	if len(types):
		if "related" not in relatedSchema:
			relatedSchema["related"] = {}

		relatedSchema["related"]["HasComponent"] = types


	if "related" in relatedSchema and "InheritsFrom" in relatedSchema["related"]:
		# This type inherits: express it as allOf[parent $ref, own members], with
		# members already defined on the parent removed to avoid duplication.
		parentConfig = system.tag.getConfiguration(i3x.utils.elementIdToPath(relatedSchema["related"]["InheritsFrom"]), True)[0]
		parentTags = []
		if "tags" in parentConfig:
			for row in parentConfig["tags"]:
				parentTags.append(row["name"])
		newTags = {}
		for tag in tags:
			if tag not in parentTags:
				newTags[tag] = tags[tag]

		schema.update({
			"allOf": [
				{"$ref": "#/types/%s" % relatedSchema["related"]["InheritsFrom"]},
				{
					"type":"object",
					"properties":newTags
				}
			]
		})
	else:
		schema.update({
			"type":"object",
			"properties":tags
		})

	if "related" in relatedSchema and "HasComponent" in relatedSchema["related"]:
		schema["related"] = {"relationshipType":"HasComponent", "types":relatedSchema["related"]["HasComponent"]}
	elif "related" in relatedSchema and "InheritsFrom" in relatedSchema["related"]:
		schema["related"] = {"relationshipType":"InheritsFrom", "types":[relatedSchema["related"]["InheritsFrom"]]}
	elif "related" in relatedSchema and "InheritedBy" in relatedSchema["related"]:
		schema["related"] = {"relationshipType":"InheritedBy", "types":relatedSchema["related"]["InheritedBy"]}

	if "parameters" in row and row["parameters"] != None and len(row["parameters"]) > 0:
		schema["parameters"] = {}
		for param in row["parameters"]:
			paramValue = row["parameters"][param]
			schema["parameters"][param] = paramValue if isinstance(paramValue, (int, float, long, bool, str, unicode, type(None))) else paramValue.value

	return schema

def getTagValue(udtInstance, value):
	# Shape a live UDT QualifiedValue into {value, quality, timestamp}, splitting
	# the composition children out of the value document (returned separately as
	# childrenValues) so the caller can attach them recursively.
	if value != None:
		children = i3x.utils.getChildrenObjectNames(udtInstance, "HasComponent")
		objValue = value.value.toDict()
		childrenValues = i3x.utils.removeChildren(objValue, children)
		quality = value.quality.toString()
		timestamp = i3x.utils.formatUtc(value.timestamp)
		return {"value":{"value":objValue, "quality":quality, "timestamp":timestamp}, "childrenValues":childrenValues}

	return None
