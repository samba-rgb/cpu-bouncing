# CPU Bouncing Benchmark Notes

This workspace is for building evidence for a blog post about why "just use atomics" can break down on multi-core machines.

## What we want to show

- A per-thread local counter with a final reduction is cheap and scalable.
- A shared atomic counter is correct, but it can become expensive because every increment needs cache-line ownership.
- False sharing is sneaky: threads can touch different variables and still fight over the same cache line.
- Padding or sharding can recover much of that lost throughput.

## Benchmark cases

- `thread_local_reduction`: each thread increments a private counter and we sum at the end.
- `shared_atomic_relaxed`: all threads increment the same atomic with relaxed ordering.
- `false_sharing_adjacent_atomics`: each thread increments its own atomic, but adjacent counters can still share cache lines.
- `padded_per_thread_atomics`: same as above, but counters are padded to the cache-line size to reduce bouncing.

## How to detect cache bouncing

- Compare a shared-write version against a sharded version. If `shared_atomic_relaxed` is much slower than `thread_local_reduction`, the shared cache line is a likely bottleneck.
- Compare adjacent counters against padded counters. If `false_sharing_adjacent_atomics` is much slower than `padded_per_thread_atomics`, layout is causing false sharing.
- Scale thread count up and watch throughput. If more threads make the program slower or flatline early, that often points to ownership handoff on a shared line.
- Use hardware profilers when available. On Linux, `perf c2c` is the best CLI for cache-to-cache contention. On macOS, Instruments counters and CPU profiling can support the diagnosis, but they are less direct.
- Change only memory layout, not logic. If adding padding changes performance dramatically, that is strong evidence that bouncing rather than algorithmic work was the problem.

## What the metrics mean

- `seconds`: total runtime for that benchmark case.
- `mops`: million operations per second. Bigger is better.
- `ns_per_op`: average nanoseconds per operation. Smaller is better.

The important comparison is not one absolute number. It is the gap between the local or sharded cases and the shared or falsely shared cases.

## Suggested blog narrative

1. Start with the intuition most people have: atomics are "lock-free", so they feel close to free.
2. Show why correctness is not the same as scalability.
3. Introduce cache coherence in plain language: one core must own a cache line before writing it.
4. Use `shared_atomic_relaxed` versus `thread_local_reduction` to show the cost of contention.
5. Use false sharing versus padding to show how layout alone changes throughput.
6. Close with practical advice: shard work, batch updates, reduce writes to shared lines, and measure.

## Run

```bash
make
./benchmark 10 5000000
python3 run_benchmarks.py
python3 plot_results.py
```
