"""Calibrate CPU_ITERS so one /cpu call is ~10 ms of CPU time, alone on a core."""
import sys
import time

sys.path.insert(0, "/opt/data/workspace/r157")
from app import burn  # noqa: E402

for iters in (100_000, 200_000, 260_000, 400_000, 800_000):
    t0 = time.process_time()
    burn(iters)
    dt = time.process_time() - t0
    print(f"{iters:>9,} iters -> {dt*1000:.2f} ms CPU  ({iters/dt:,.0f} iters/s)")
