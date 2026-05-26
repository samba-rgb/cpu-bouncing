# Tooling Notes: `sample` and `powermetrics`

This section captures what happened when we tried to inspect the benchmark externally on this local macOS machine.

## Commands tried

### `sample`

We ran the benchmark long enough to catch the shared atomic phase:

```bash
./benchmark 10 200000000
```

Then sampled the running process:

```bash
sample <pid> 3 10 -file results/sample-benchmark.txt
```

Saved output:

- [sample-benchmark.txt](/Users/samba/Desktop/blogs/cpu-bouncing/results/sample-benchmark.txt)

### `powermetrics`

We tried:

```bash
powermetrics --show-cpu-scalability --show-process-energy -i 1000 -n 2 -o results/powermetrics.txt
```

But on this machine it failed because `powermetrics` requires superuser privileges.

## What `sample` showed

The `sample` report is useful, but indirect.

It showed:

- the main thread blocked in `std::thread::join()`
- the worker threads executing the `bench_shared_atomic(...)` lambda
- no higher-level blocking primitive inside the workers because they were busy doing work

That means `sample` can confirm:

- which phase of the benchmark is hot
- how many worker threads are active
- that the program is spending time inside the shared atomic workload

What it does **not** confirm directly:

- cache-line ownership transfer
- false sharing
- MESI state transitions

So `sample` is useful for a blog as supporting evidence, not as proof of cache-line bouncing by itself.

## What `powermetrics` would have helped with

If run as root, `powermetrics` can help correlate:

- CPU scalability
- process energy
- high CPU burn during poor throughput

That is still indirect evidence, but it is helpful when telling the story:

- the machine is working hard
- throughput is still bad
- contention is likely wasting cycles

In this session, though, we could not collect it because of the permission requirement.

## Best interpretation for the blog

Use the tools like this:

- `sample`: "The threads are hot inside the contended shared atomic loop."
- benchmark delta: "Changing ownership and layout changes performance dramatically."
- `powermetrics` if available: "The CPU burns energy without scaling well."

That combination is persuasive, even though only Linux `perf c2c` gives the most direct CLI evidence for cache-to-cache contention.
