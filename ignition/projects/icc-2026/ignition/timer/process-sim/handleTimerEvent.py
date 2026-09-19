def handleTimerEvent():
    """The clock behind the process simulator.

    Thin, like `06-poll`: the cadence lives in `attributes.delay` in
    resource.json beside this file and every decision lives in `process_sim`
    -- the operation profiles, the lag, the noise. `fixedDelay: true` measures
    from the end of one tick to the start of the next.

    No try block. `tick()` catches its own failures per vessel and logs them.
    """
    process_sim.tick()
