"""Takes the redirect out if the proxy stops during the job, so the job never
loses its network because of a recording tool. Two failed checks in a row
count as stopped."""

from .common import READYZ, load_state, redirect_off, state_dir


def run_watchdog(env, system, root=None, log=print):
    state = load_state(env)
    if state is None:
        return
    # HOTLINE_CI_WATCHDOG_TICKS bounds the loop for tests; 0 means no limit
    limit = int(env.get("HOTLINE_CI_WATCHDOG_TICKS") or 0)
    failures = ticks = 0
    while (state_dir(env) / "state.json").exists():
        failures = 0 if system.http_get(READYZ) is not None else failures + 1
        if failures >= 2:
            redirect_off(system)
            (state_dir(env) / "watchdog.txt").write_text(
                "the proxy stopped; the redirect was taken out")
            return
        ticks += 1
        if limit and ticks >= limit:
            return
        system.sleep(3)
