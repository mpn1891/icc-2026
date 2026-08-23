# Pattern 6 -- the poll itself. Once a minute, ask AP Connect what is newer than the
# watermark and publish it. Sixty seconds is the pattern, not a compromise: the lag
# between a completed measurement and its arrival on the backbone is what polling costs,
# and it is only visible next to CDC because it is this long.
#
# All logic lives in the script module so it can be run by hand from the script console
# during a demo: poll_turbidity.tick()
poll_turbidity.tick()
