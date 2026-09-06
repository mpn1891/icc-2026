def handleTimerEvent():
    """Pattern 6 -- the clock that makes the poll a poll.

    Nothing pushes. The particle counter does not know this gateway exists; it
    samples on its own clock into a rolling buffer, and Ignition finds out here.
    That is the whole acquisition, and the detection gap it costs -- up to one
    poll interval plus one sample duration -- is the point of the pattern, not a
    defect in it.

    Thin on purpose. Every decision lives in `particle_counter_poll`: the
    cursor walk, the dedupe floor, the excursion flag, store-before-publish.
    The only thing that belongs in a timer is the cadence.

    **The cadence is `attributes.delay` in resource.json, not
    `config/poll_interval_s`.** That tag documents the number and the poll does
    not read it; this file is the one the gateway obeys. `fixedDelay: true`
    measures from the end of one run to the start of the next, so a poll that
    runs long -- draining a backlog over several pages -- cannot stack runs on
    top of itself. docs/plans/06-poll-particle-counter.md, "Open items".

    No try block, deliberately. `poll()` catches its own failures, writes
    `state/last_error` and returns; and it returns 0 in silence when
    `config/enabled` is false, which is the stall demo rather than a fault.
    """
    particle_counter_poll.poll()
