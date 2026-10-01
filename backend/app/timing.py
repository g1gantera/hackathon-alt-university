"""Clock limits shared by transport validation, simulation and planning."""

import asyncio

# JSON numbers must round-trip exactly through the browser.
MAX_SPEED=2**53-1
LIVE_REPLAN_SPEED_LIMIT=60
UPDATE_INTERVAL_MS=500


async def run_periodic(callback, *, clock=None, sleep=None):
    """Anchor updates to monotonic deadlines instead of adding work to each wait.

    After an overrun, skip expired deadlines. Never flood clients with a backlog
    of synthetic updates or advance the model for time spent unable to process it.
    Clock/sleep injection allows deterministic drift and stall checks.
    """
    clock=clock or asyncio.get_running_loop().time
    sleep=sleep or asyncio.sleep
    interval=UPDATE_INTERVAL_MS/1000
    deadline=clock()+interval
    while True:
        delay=deadline-clock()
        if delay>0:
            await sleep(delay)
            continue  # An early wakeup must not run a premature model step.
        callback()
        deadline+=interval
        now=clock()
        if deadline<=now:
            deadline+=(int((now-deadline)//interval)+1)*interval


class ModelClock:
    """Convert each half-second update to exact, whole model seconds.

    Carry fractional model seconds for odd multipliers (7x -> 3, 4, 3, 4).
    Pauses, reset, speed changes and replanning holds discard that fraction.
    Integer arithmetic also preserves the maximum browser-safe multiplier.
    """
    def __init__(self):
        self.context=None
        self.remainder=0

    def step(self, *, epoch, speed, running, held):
        context=(epoch,speed,running,held)
        if context!=self.context:
            self.context=context
            self.remainder=0
        if not running or held:
            return 0
        seconds,self.remainder=divmod(self.remainder+speed*UPDATE_INTERVAL_MS,1000)
        return seconds


def planning_headroom(state):
    # Faster clocks are held by Simulator while a replacement is calculated.
    return state['speed']*6 if state.get('running') and state['speed']<=LIVE_REPLAN_SPEED_LIMIT else 0
