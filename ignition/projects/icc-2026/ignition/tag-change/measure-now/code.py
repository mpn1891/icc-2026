# Pattern 6 -- the operator prop. A Haze 3001 is started by a person at the instrument,
# not by the historian; this tag stands in for that person so every row on stage exists
# because somebody caused it.
#
# Rising edge only, and never on the gateway's initial subscription: a restart with the
# tag left true would otherwise file a measurement nobody asked for.
#
# measure_now() POSTs to the simulator and writes this tag back to false. It does not
# INSERT -- Ignition holds SELECT only on the apconnect catalog (checkpoint 10).
if not initialChange and currentValue and currentValue.value:
	if not (previousValue and previousValue.value):
		poll_turbidity.measure_now()
