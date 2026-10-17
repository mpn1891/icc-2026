# i3x.tag
# -------
# Tag-change subscriptions backing the i3X polling subscription model. Each
# monitored item registers an I3XTagChangeListener that, on every value change,
# stages a SyncUpdateEntry on its subscription; clients drain those via
# POST /subscriptions/sync. Listeners run on Ignition tag threads, so all
# mutation of shared subscription state is guarded by the subscription's lock.

from com.inductiveautomation.ignition.common.tags.model.event import TagChangeListener

# Upper bound on staged updates and pending batches per subscription. When a
# bound is hit the oldest entries are dropped and the next sync reports 206.
MAX_QUEUE_SIZE = 1000

class I3XTagChangeListener(TagChangeListener):
	# One listener per monitored tag (a UDT, an alarm state tag, or a composition
	# child). Its job is to translate a tag change into a SyncUpdateEntry and
	# stage it on the owning subscription.
	def __init__(self, elementId, tagPath, udtInstance, subscription):
		self.tagPath = tagPath
		self.elementId = elementId
		self.udtInstance = udtInstance
		self.subscription = subscription

	def tagChanged(self, tagChangeEvent):
		# Build the per-element value snapshot. Alarms re-read their current
		# status; everything else uses the changed tag's VQT (composition children
		# are stripped out here because each child streams under its own listener).
		if self.udtInstance["typeId"] == "ignition-alarm":
			alarmObj = i3x.ignition.getAlarmFromSource(self.udtInstance["path"])["alarmObj"]
			entry = {"value":alarmObj, "quality":"Good", "timestamp":alarmObj["eventTime"]}
		else:
			value = i3x.ignition.getTagValue(self.udtInstance, tagChangeEvent.getValue())
			if value is None:
				return
			vqt = value["value"]
			entry = {"value":vqt["value"], "quality":vqt["quality"], "timestamp":vqt["timestamp"]}

		# SyncUpdateEntry shape: {elementId, value, quality, timestamp}
		update = {"elementId":self.elementId}
		update.update(entry)

		# Stage the update for the next sync(). Mutated from tag threads while
		# sync() reads from web threads, so guard with the subscription lock.
		# On overflow the oldest staged update is dropped and 206 is flagged.
		lock = self.subscription["lock"]
		with lock:
			staged = self.subscription["stagedUpdates"]
			wasFull = staged.maxlen is not None and len(staged) == staged.maxlen
			staged.append(update)
			if wasFull:
				self.subscription["overflow"] = True

def subscribe(elementId, tagPathStr, udtInstance, subscription):
	# Subscribe a new listener for one element and return it (the caller tracks it
	# for later teardown). Alarm elements are monitored via their .State tag.
	from com.inductiveautomation.ignition.gateway import IgnitionGateway
	from com.inductiveautomation.ignition.common.tags.paths.parser import TagPathParser

	context = IgnitionGateway.get()
	tagManager = context.getTagManager()

	if udtInstance["typeId"] == "ignition-alarm":
		tagPathStr = i3x.utils.alarmSourceToStateTagPath(tagPathStr)

	tagPath = TagPathParser.parse(tagPathStr)
	listener = I3XTagChangeListener(elementId, tagPathStr, udtInstance, subscription)
	tagManager.subscribeAsync(tagPath, listener)
	return listener

def unsubscribe(tagPathStr, udtInstance, listener):
	# Remove a previously-registered listener from the tag manager.
	from com.inductiveautomation.ignition.gateway import IgnitionGateway
	from com.inductiveautomation.ignition.common.tags.paths.parser import TagPathParser

	context = IgnitionGateway.get()
	tagManager = context.getTagManager()

	if udtInstance["typeId"] == "ignition-alarm":
		tagPathStr = i3x.utils.alarmSourceToStateTagPath(tagPathStr)

	tagPath = TagPathParser.parse(tagPathStr)
	tagManager.unsubscribeAsync(tagPath, listener)
