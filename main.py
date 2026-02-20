from __future__ import annotations

import time

from src.pipeline.pipeline import run_pipeline


def main() -> None:
    start = time.perf_counter()

    # If this raises, Python will show the traceback (and details are also in log files).
    run_pipeline()

    elapsed_s = time.perf_counter() - start

    # Professional final message (single line)
    print(f"✅ Pipeline executed successfully | duration={elapsed_s:.2f}s")


if __name__ == "__main__":
    main()