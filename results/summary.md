# Benchmark Summary

Measured on May 26, 2026 on this local Apple Silicon machine with 10 CPU cores and 128-byte cache lines.

## Main result

At 10 threads and 5,000,000 increments per thread:

- `thread_local_reduction`: `0.009763 s`
- `shared_atomic_relaxed`: `1.867685 s`
- `false_sharing_adjacent_atomics`: `0.484675 s`
- `padded_per_thread_atomics`: `0.016501 s`

That means:

- shared atomic was about `191.3x` slower than local reduction at 10 threads
- false sharing was about `29.4x` slower than padded counters at 10 threads

## Thread sweep highlights

- `2 threads`: shared atomic was `10.8x` slower than local reduction
- `4 threads`: shared atomic was `33.6x` slower than local reduction
- `8 threads`: shared atomic was `133.5x` slower than local reduction
- `10 threads`: shared atomic was `191.3x` slower than local reduction

False sharing versus padding:

- `2 threads`: `3.3x` slower
- `4 threads`: `21.7x` slower
- `8 threads`: `58.1x` slower
- `10 threads`: `29.4x` slower

## Graphs

- [throughput.svg](/Users/samba/Desktop/blogs/cpu-bouncing/results/throughput.svg)
- [latency.svg](/Users/samba/Desktop/blogs/cpu-bouncing/results/latency.svg)
- [benchmark_results.csv](/Users/samba/Desktop/blogs/cpu-bouncing/results/benchmark_results.csv)

## Tooling notes

Direct cache-line contention tooling is best on Linux:

- `perf c2c` is the strongest CLI for detecting cache-to-cache contention and false sharing.

Useful supporting tools on this Mac:

- `sample <pid>` can show where threads are spending CPU time, but it does not directly identify cache-line bouncing.
- `powermetrics --show-cpu-scalability --show-process-energy` can help correlate rising CPU burn or poor scaling with contention, but it is indirect.

What is missing on this machine right now:

- `xctrace` is not currently available on PATH, so the Xcode Instruments CLI workflow is not ready in this shell.
