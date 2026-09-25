"""Guards against accidentally reintroducing per-row Python loops."""
import random
import time

import pytest

from agents.fixer import run_fixer
from agents.ranker import rank_findings
from agents.scout import run_scout
from conftest import HEADER
from ingest import load_csv

ROWS = 100_000


def synthetic_csv(n: int) -> bytes:
    rnd = random.Random(1)
    lines = [HEADER]
    for i in range(n):
        temp = round(rnd.gauss(71, 1.5), 1)
        date = f"2026-0{rnd.randint(1, 8)}-{rnd.randint(10, 28)}"
        roll = rnd.random()
        if roll < 0.002:
            date = ""
        elif roll < 0.004:
            temp = round(rnd.uniform(90, 130), 1)
        lines.append(f"LOT{i:07d},PROD-{'ABCD'[i % 4]},{date},{rnd.randint(100, 200)},kg,{temp},"
                     f"{round(rnd.gauss(4.15, 0.08), 2)},INSP-01,FAC-01,PASS,run")
        if roll > 0.997:
            lines.append(lines[-1])
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.slow
def test_100k_rows_detect_and_fix_quickly(spec):
    frame = load_csv(synthetic_csv(ROWS), "big.csv", 1_000_000).frame
    started = time.perf_counter()
    scout = run_scout(frame, spec)
    ranked = rank_findings(scout.findings)
    run_fixer(frame, ranked, spec)
    elapsed = time.perf_counter() - started
    assert scout.summary["total"] > 500
    # About 2 s locally; the bound is loose for slow CI runners but still catches per-row
    # Python loops (the pre-vectorised detectors took about 3 minutes at 500k rows).
    assert elapsed < 15, f"{elapsed:.1f}s"
