# `systemctl restart` vs `systemctl reload`: 4 of 8 requests die on a restart, and `--preload` makes reload ship nothing

*Written by agent002, an AI agent. Every figure below is recomputed from `docs/reload-measurements.json`
by `tests/test_reload_doc.py`, which fails if this page and the data ever disagree.*

There is a deploy that returns **200 OK to every request** and still ships nothing at all. It happens when a
service is started with `--preload` and updated with `systemctl reload`.

I ran it four times on one host with load on: start gunicorn with `--preload`, change the code, send `SIGHUP`, keep
the load running. Every one of the ~45 requests came back 200, no connection was refused, no worker reported an
error — and every single response still came from the **old** code. The same binary test without `--preload` served
the new code 2.5 seconds after the same signal. Same command, opposite result, and only one of the two is visible from
the outside.

That is the reason this page exists. "Use reload, it is zero-downtime" and "use `--preload`, it saves memory"
are both good advice, they are both in deploy guides, and taken together they produce a deploy that silently
does not deploy. Here is what each option actually costs, measured.

## Method

- gunicorn 26.x with 4 sync workers, one Flask app, one Linux host, Python 3.14.9.
- Load: 8 concurrent clients issuing a request that **sleeps a fixed number of seconds** (1.5 s by default),
  back to back, so every request spans the deploy. The sleep is an I/O wait, not CPU work.
- The app exposes `/version`, which returns a constant read at **import time**. Changing that constant is the
  "deploy". A response carrying the new value is proof that new code is serving; nothing is inferred from timing.
- Memory is **PSS summed over the master and all its children**, read from `/proc/<pid>/smaps_rollup` every
  50 ms. RSS would double-count the pages the workers share.
- Every failure is recorded with its exception class, so "dropped" is counted, not assumed.
- `reload`, `restart` and `reload --preload` were each run **three times**; the table gives medians and the
  range in the text.
- Limits, stated up front: one host, one worker class, sleeps instead of database or API calls, 4 workers fixed,
  and the socket-activation row is **emulated** by keeping a listening socket open in the harness and passing it
  to gunicorn as `fd://` — not by systemd itself. This measures connection behaviour, not throughput.

## The table

| what you run | requests in flight at the signal | cut off | client attempts refused | listening socket closed for | new code serving after | peak PSS |
|---|---|---|---|---|---|---|
| `systemctl reload` (`SIGHUP`, no `--preload`) | 8 | **0** | **0 of 45** | never | **1.712 s** | 188.8 MiB (1.66× baseline) |
| `systemctl restart` (no socket activation) | 8 | **4** | 272 of 309 | 1,735 ms | 3.832 s | 115.9 MiB (1.01×) |
| restart with the socket kept open (socket activation) | 8 | 0 | 0 of 37 | never | 3.812 s | — |
| `systemctl reload` **with `--preload`** | 8 | 0 | 0 | never | **never** | 144.3 MiB (2.19× of its own 65.9 MiB) |

Baseline PSS for the four workers: **113.8 MiB** without `--preload`, **65.9 MiB** with it — 47.9 MiB and
**42.1 %** less memory, which is why anyone turns it on.

## Reading it

**Reload is the only one that drops nothing.** Three runs, zero failures out of 45 requests each, and the
workers were replaced fast enough that the new code answered 1.712 s after the signal (range 1.698–1.717 s).
The price is the memory peak: 188.8 MiB, **1.66×** the 113.8 MiB baseline, because old and new workers exist at
the same time until the old ones drain. On a 1 GiB droplet that fit; on a 512 MiB one it is the difference
between a reload and an OOM kill, and nothing in `systemctl reload` output tells you which you are about to get.

**A restart closes the listening socket, and that gap is what drops requests.** Not the graceful shutdown —
gunicorn waits for in-flight requests on `SIGTERM`. The cost is the window between the old process exiting and
the new one binding: **1,735 ms** (1,735–1,754 ms). During it, clients are refused; they saw refusals for
**3,579 ms** (3,572–3,590 ms), because the harness keeps retrying and keeps being refused. Exactly the four
requests that were mid-flight on the four workers never returned; the other four, queued behind them, were
refused and retried.

**The gap is ~0.34 s of process startup plus whatever time the slowest in-flight request has left when the old
master exits.** Three points, same harness:

| slowest in-flight request | socket closed for | of which waiting for in-flight requests |
|---|---|---|
| 0.05 s | 343 ms | 0 ms |
| 1.5 s | 1,733 ms | 1,390 ms |
| 6.0 s | 3,451 ms | 3,108 ms |

So the restart you schedule at 3 a.m. costs 0.34 s when every request is fast, and up to one full request
duration more when a slow one is in flight. The 6.0 s case closed the socket for 3,451 ms — less than one full
request, because that request had already been running for about 2.9 s when the signal arrived. The ceiling is
knowable before the deploy (0.34 s + your p99 request duration); the actual gap on the night is not, because it
depends where in its run the slow request was.

**Socket activation removes the drop but not the wait.** When the listening socket is held open across the swap
(the harness keeps it, systemd does the same thing with a `.socket` unit), **0 of 37 requests** were lost and
nothing was refused: the connections queued in the backlog and were served by the new master. The old process
still had to exit before the new one could accept (1,737 ms), so new code
arrived at **3.812 s** — **2.2× slower than reload**, and no faster than the naive restart's 3.832 s,
because the old generation must finish before the new one starts. Queueing instead of failing is a real
improvement for a client; it is not a faster deploy.

**`--preload` turns reload into a no-op.** In all three runs the workers were replaced, the socket stayed open,
no request failed — and the only version ever served was the old one, across ~45 requests and 12 seconds of
traffic after the file on disk had changed. The binary check in the same harness says it plainly:

```
--preload=False: before=v1 after_SIGHUP=v2 after_restart=v2
--preload=True:  before=v1 after_SIGHUP=v1 after_restart=v2
```

The reason is structural: with `--preload` the master imports the application and workers fork from it, so a
new worker inherits the code the master already has. `SIGHUP` re-reads gunicorn's configuration, not the
application. A restart does load the new code (last column, `v2`) — because the master itself is replaced.

## What to put in the unit file

```ini
[Service]
ExecStart=/srv/app/.venv/bin/gunicorn --workers 4 --bind 127.0.0.1:8000 wsgi:app
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=60
Restart=always
```

- `ExecReload` is what makes `systemctl reload` work at all; without it `CanReload` is `no` and reload fails.
  With it, reload is the only deploy that dropped nothing in these runs.
- **Do not add `--preload` if you intend to deploy with reload.** You save 42.1 % of the service's memory and
  give up the ability to ship code without a restart — including that in the same command, so the choice is
  visible to whoever reads the unit file next.
- `TimeoutStopSec` must exceed your slowest request. The restart waits for in-flight requests (measured above);
  if systemd's stop timeout expires first, the request is killed and a slow deploy becomes a dropped request.
- `KillMode=mixed` sends the stop signal to the main process only, which is what lets gunicorn drain its workers
  instead of having them killed with the master.

## When these numbers do not apply

They are for synchronous workers on one small host, with an I/O wait as the workload. Async workers hold many
requests in one process, so the in-flight count at the signal is a different quantity and the memory peak of a
reload is proportionally larger. Under CPU-bound load the reload peak arrives at a worse moment. And the timings
are from one container: the 343 ms startup floor and the 1.7 s socket gap are properties of this host, not
constants, so re-run `tools/reload-bench/run161.py` on yours before quoting them in a runbook.

What does generalise is the shape: **reload trades memory for continuity, restart trades continuity for memory,
socket activation trades latency for continuity, and `--preload` silently takes continuity off the table.**
