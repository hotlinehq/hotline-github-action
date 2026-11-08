"""End of the job: stops the proxy so waiting records are sent, removes the
redirect and the trusted issuer, and writes a short summary."""

import shutil

from .common import CONTAINER, DOCKER_STOP_SECONDS, load_state, redirect_off, state_dir, untrust_ca


def run_teardown(env, system, root=None, log=print):
    state = load_state(env)
    if state is None:  # setup did nothing, or failed open: nothing to undo
        return
    # the redirect first, so the job's own last calls go straight out
    if state["mode"] == "firewall":
        redirect_off(system)
    # stopping gives the proxy time to send what is waiting
    system.try_run("docker", "stop", "-t", DOCKER_STOP_SECONDS, CONTAINER)
    try:
        exited = system.run("docker", "inspect", "--format", "{{.State.ExitCode}}",
                            CONTAINER, capture=True).strip()
    except Exception:
        exited = "unknown"
    system.try_run("docker", "rm", "-f", CONTAINER)
    untrust_ca(system)

    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["### Hotline", "",
                 f"- Mode: `{state['mode']}`, integration `{state['integration']}`"]
        if state["overridden"]:
            lines.append(f"- Proxy image: `{state['image_digest']}` (**not pinned**: the "
                         f"`image` input was set to `{state['image_ref']}`)")
        else:
            lines.append(f"- Proxy image: `{state['image_digest']}` (pinned)")
        if state["region"]:
            lines.append(f"- Runner region: `{state['region']}`")
        lines.append(f"- The proxy stopped with exit code `{exited}`")
        note = state_dir(env) / "watchdog.txt"
        if note.exists():
            lines.append(f"- Watchdog: {note.read_text().strip()}")
        lines.append("- The calls themselves are in your OpenTelemetry destination.")
        with open(summary, "a") as handle:
            handle.write("\n".join(lines) + "\n")
    shutil.rmtree(state_dir(env), ignore_errors=True)
