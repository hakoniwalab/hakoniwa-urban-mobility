#!/usr/bin/env python3
"""Hakoniwa asset that keeps simulation time in step with wall-clock time.

The Conductor advances world time only while every asset is less than
max_delay behind it, so an asset whose own time follows the wall clock bounds
world time to wall time + max_delay (+ the Core delta). One pacer therefore
paces every simulator of an Urban Composition (Car plant, Drone service, or
both), and the simulators run without their own wall-clock sleeps on every
OS (the Car plant with --realtime-sync-cycle-msec 0, the Drone service with
--real-sleep-msec 0).

Deadlock-freedom requires this asset's delta to be at most the Conductor's
max_delay (max_i dT_i <= M): the Drone service's built-in Conductor uses
20 ms, the Car plant's 100 ms.

Every --report-sec it prints
  [pacer] wall=<s>s sim=<s>s rtf=<sim/wall> idle=<%>
idle is the share of the last interval the pacer spent waiting for the wall
clock. rtf stays near 1 until the simulators cannot keep up, so idle shows
the remaining headroom: near 0 % means the simulation is at its limit.

Adapted from hakoniwa-fpv-drone tools/fpv_realtime_pacer.py.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("config_path", help="Hakoniwa asset config declaring no robots or PDUs")
    result.add_argument("--asset-name", default="UrbanRealtimePacer")
    result.add_argument("--delta-msec", type=int, default=10)
    result.add_argument(
        "--max-delay-msec", type=int, default=20,
        help="the Conductor owner's max_delay (Drone service 20 ms, Car plant 100 ms)",
    )
    result.add_argument("--report-sec", type=float, default=2.0)
    result.add_argument(
        "--sleep-to-deadline", action="store_true",
        help="sleep until the next delta boundary of wall time instead of a fixed "
        "delta, so world time advances one step per boundary (Windows opt-in; "
        "hakoniwalab/hakoniwa-urban-mobility#85)",
    )
    return result


def sleep_seconds(start: float, now: float, asset_usec: int, delta_usec: int, to_deadline: bool) -> float:
    """How long the pacer sleeps after catching up with wall time.

    A fixed delta lets the wake-ups drift against the delta boundaries, so a
    wake-up advances 0, 1 or 2 steps and world time moves in bursts (a 20 ms
    WebBridge then sends at 10/20/31 ms on Windows). Sleeping to the next
    boundary keeps one step per boundary.
    """
    if not to_deadline:
        return delta_usec / 1_000_000
    return max(0.0, start + (asset_usec + delta_usec) / 1_000_000 - now)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 0 < args.delta_msec <= args.max_delay_msec:
        print(
            f"ERROR: --delta-msec must be in (0, {args.max_delay_msec}] to avoid a time-guard deadlock",
            file=sys.stderr,
        )
        return 2
    delta_usec = args.delta_msec * 1000

    import hakopy

    def on_manual_timing_control(context) -> int:
        # hakopy discards exceptions raised in callbacks; log them explicitly.
        try:
            return pace()
        except Exception:
            traceback.print_exc()
            sys.stderr.flush()
            return 1

    def pace() -> int:
        print("[pacer] manual timing control started", flush=True)
        start = time.monotonic()
        report = {"next": start + args.report_sec, "last": start, "idle": 0.0}

        def maybe_report(now: float) -> None:
            # Checked while advancing too: a simulation at its limit never
            # leaves the advance loop, and that is when the report matters.
            if now < report["next"]:
                return
            sim_sec = hakopy.simulation_time() / 1_000_000
            wall_sec = now - start
            idle = 100.0 * report["idle"] / max(now - report["last"], 1e-9)
            print(
                f"[pacer] wall={wall_sec:.1f}s sim={sim_sec:.1f}s rtf={sim_sec / wall_sec:.3f} idle={idle:.0f}%",
                flush=True,
            )
            report.update(next=now + args.report_sec, last=now, idle=0.0)

        while True:
            now = time.monotonic()
            # Follow elapsed wall time rather than counting sleeps, so coarse
            # OS sleep granularity (notably on Windows) does not slow the run.
            wall_usec = int((now - start) * 1_000_000)
            while hakopy.asset_current_time() + delta_usec <= wall_usec:
                # hakopy.usleep returns True after advancing, False once the
                # simulation stops (not an errno-style 0 on success).
                if not hakopy.usleep(delta_usec):
                    print("[pacer] simulation stopped", flush=True)
                    return 0
                maybe_report(time.monotonic())
            maybe_report(now)
            slept = time.monotonic()
            time.sleep(sleep_seconds(
                start, slept, hakopy.asset_current_time(), delta_usec, args.sleep_to_deadline
            ))
            report["idle"] += time.monotonic() - slept

    callbacks = {
        "on_initialize": lambda context: 0,
        "on_simulation_step": None,
        "on_manual_timing_control": on_manual_timing_control,
        "on_reset": lambda context: 0,
    }
    if not hakopy.asset_register(
        args.asset_name, args.config_path, callbacks, delta_usec, hakopy.HAKO_ASSET_MODEL_CONTROLLER
    ):
        print("ERROR: hakopy.asset_register() failed", file=sys.stderr)
        return 1
    print(f"[pacer] registered {args.asset_name}: delta={args.delta_msec} ms", flush=True)
    result = hakopy.start()
    print(f"[pacer] finished: hakopy.start() returned {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
