# CPU Bouncing: Why "Just Use Atomics" Stops Being Cheap on Multi-Core Machines

When people first learn about lock-free programming, there is a very common mental shortcut:

> atomics are safe, lock-free, and therefore probably cheap

That shortcut is useful for getting started, but it breaks down badly on real multi-core machines.

The problem is not that atomics are incorrect. The problem is that writes to shared memory force hardware coordination between cores. Once multiple cores keep updating the same cache line, performance stops being about instruction count and starts being about ownership transfer.

This post is about that transfer.

I will call it CPU bouncing here, though a more precise description is cache-line bouncing or cache-line ping-pong: the same cache line keeps moving between cores because multiple cores want write access.

## The short version

- A shared atomic increment is correct.
- It is not free.
- Even `memory_order_relaxed` still performs a write.
- A write requires ownership of the cache line.
- If several cores keep writing that line, the line keeps bouncing.
- Throughput collapses, even though every individual operation is still "lock-free".

On this machine, a 10-thread shared atomic increment benchmark took `1.867685 s`, while a per-thread local counter with final reduction took `0.009763 s`.

That is a `191.3x` gap.

This is not because atomics are broken. It is because coherence traffic became the bottleneck.

## The actual machine used here

These measurements were collected on a local Apple Silicon machine with:

- `10` CPU cores
- `128` byte cache lines
- `clang` with `-O3`

The benchmark code is here:

- [benchmark.cpp](/Users/samba/Desktop/blogs/cpu-bouncing/benchmark.cpp:1)

The raw results and generated charts are here:

- [benchmark_results.csv](/Users/samba/Desktop/blogs/cpu-bouncing/results/benchmark_results.csv)
- [throughput.svg](/Users/samba/Desktop/blogs/cpu-bouncing/results/throughput.svg)
- [latency.svg](/Users/samba/Desktop/blogs/cpu-bouncing/results/latency.svg)

## Before the benchmark: the hardware story

Modern cores do not go to DRAM for every load or store. They operate mostly through cache hierarchies.

That is good news until multiple cores touch the same memory line.

Caches are coherent, which means the hardware works to maintain a sane view of memory across cores. The exact protocol varies by architecture, but the classic teaching model is MESI:

- `M`: Modified
- `E`: Exclusive
- `S`: Shared
- `I`: Invalid

![MESI overview](/Users/samba/Desktop/blogs/cpu-bouncing/images/mesi-overview.svg)

The simplest way to think about MESI is:

- `Shared` means multiple cores may hold a clean copy.
- `Exclusive` means one core holds the only clean copy.
- `Modified` means one core owns the only valid dirty copy.
- `Invalid` means a cached copy cannot be used anymore.

The crucial detail is this:

**to write a cache line, a core must gain ownership of it**

That ownership transition is where the pain begins.

If Core 0 and Core 1 both keep incrementing the same atomic counter, then ownership of the cache line holding that counter keeps moving from one core to the other.

![MESI write flow](/Users/samba/Desktop/blogs/cpu-bouncing/images/mesi-write-flow.svg)

The increment itself is tiny. The ownership traffic around the increment is not.

If you want the simplest possible mental model, think of it like this:

- Core 0 has the counter's cache line and is allowed to write it.
- A thread on Core 1 now wants to write the same counter.
- Core 1 cannot just write immediately.
- Hardware first has to take ownership away from Core 0 and give it to Core 1.
- If both threads keep doing this, the line keeps bouncing back and forth.

## Why `memory_order_relaxed` is still not cheap

A lot of engineers learn that `memory_order_relaxed` is the "cheap" atomic ordering. That is true only in one narrow sense: it relaxes ordering constraints compared with stronger modes like acquire-release or sequential consistency.

What it does **not** do is eliminate the need to perform a write safely.

If your code does:

```cpp
counter.fetch_add(1, std::memory_order_relaxed);
```

the CPU still needs exclusive ownership of the cache line for that update. The compiler may emit a lighter-weight ordering pattern, but the coherence system still has to ensure that the write is valid.

This is the key distinction:

- memory ordering answers "what visibility and ordering guarantees do I need?"
- cache coherence answers "which core currently owns the line I want to write?"

You can relax the first question and still get crushed by the second.

## The benchmark

To keep the story focused, I reduced the benchmark to four cases:

1. `thread_local_reduction`
2. `shared_atomic_relaxed`
3. `false_sharing_adjacent_atomics`
4. `padded_per_thread_atomics`

The benchmark source is intentionally small. Each case performs a large number of increments and records:

- total seconds
- million operations per second
- average nanoseconds per operation

The idea is not to build a perfect universal microbenchmark. The goal is to isolate the effect of shared writes and false sharing clearly enough to teach from it.

### Case 1: per-thread local counters

Each thread increments a local variable. At the end, the program adds those local totals together.

```cpp
Result bench_thread_local_reduction(std::size_t threads,
                                    std::uint64_t iterations_per_thread) {
  std::uint64_t sink = 0;
  const std::uint64_t operations = threads * iterations_per_thread;

  return run_benchmark("thread_local_reduction", threads, operations, [&] {
    std::vector<std::thread> workers;
    std::vector<std::uint64_t> locals(threads, 0);
    workers.reserve(threads);

    for (std::size_t t = 0; t < threads; ++t) {
      workers.emplace_back([&, t] {
        std::uint64_t local = 0;
        for (std::uint64_t i = 0; i < iterations_per_thread; ++i) {
          ++local;
          do_not_optimize(local);
        }
        locals[t] = local;
      });
    }

    for (auto& worker : workers) {
      worker.join();
    }

    std::uint64_t total = 0;
    for (std::uint64_t value : locals) {
      total += value;
    }
    sink = total;
    do_not_optimize(sink);
  });
}
```

This is the control group. It keeps hot writes private as long as possible.

### Case 2: one shared atomic

Every thread increments the same atomic counter:

```cpp
Result bench_shared_atomic(std::size_t threads,
                           std::uint64_t iterations_per_thread) {
  std::atomic<std::uint64_t> counter{0};
  const std::uint64_t operations = threads * iterations_per_thread;

  return run_benchmark("shared_atomic_relaxed", threads, operations, [&] {
    std::vector<std::thread> workers;
    workers.reserve(threads);

    for (std::size_t t = 0; t < threads; ++t) {
      workers.emplace_back([&] {
        for (std::uint64_t i = 0; i < iterations_per_thread; ++i) {
          counter.fetch_add(1, std::memory_order_relaxed);
        }
      });
    }

    for (auto& worker : workers) {
      worker.join();
    }
  });
}
```

This is the "safe but contended" case.

### Case 3: false sharing

Each thread gets its own atomic counter, which sounds good at first:

```cpp
std::vector<std::atomic<std::uint64_t>> counters(threads);
```

But those counters are adjacent in memory. If several end up on the same cache line, then the threads are still invalidating each other.

This is the classic trap: **logical independence does not guarantee physical independence.**

### Case 4: padded counters

Now we keep the algorithm the same, but change the layout:

```cpp
struct alignas(128) PaddedCounter {
  std::atomic<std::uint64_t> value{0};
};
```

Then each thread updates its own padded slot.

That makes the key comparison extremely powerful:

- same language
- same compiler
- same operation
- same thread count
- same algorithmic intent
- different memory layout

If performance changes massively, the hardware story is the explanation.

![False sharing vs padding](/Users/samba/Desktop/blogs/cpu-bouncing/images/false-sharing-layout.svg)

## Results

### Raw 10-thread numbers

At `10` threads with `5,000,000` increments per thread:

- `thread_local_reduction`: `0.009763 s`
- `shared_atomic_relaxed`: `1.867685 s`
- `false_sharing_adjacent_atomics`: `0.484675 s`
- `padded_per_thread_atomics`: `0.016501 s`

The two headline comparisons are:

- shared atomic was `191.3x` slower than local reduction
- false sharing was `29.4x` slower than padded counters

### Throughput chart

![Throughput chart](/Users/samba/Desktop/blogs/cpu-bouncing/results/throughput.svg)

The main pattern is what matters:

- local reduction scales well
- padded counters stay healthy
- shared atomic falls off hard
- false sharing also degrades badly, though not always as severely as one shared atomic

### Latency chart

![Latency chart](/Users/samba/Desktop/blogs/cpu-bouncing/results/latency.svg)

Per-operation cost explodes once multiple cores keep competing for write ownership.

### Thread sweep highlights

Shared atomic versus local reduction:

- `2 threads`: `10.8x` slower
- `4 threads`: `33.6x` slower
- `8 threads`: `133.5x` slower
- `10 threads`: `191.3x` slower

False sharing versus padded counters:

- `2 threads`: `3.3x` slower
- `4 threads`: `21.7x` slower
- `8 threads`: `58.1x` slower
- `10 threads`: `29.4x` slower

The shape is important. The more we scale the number of contending threads, the worse the shared-write versions behave.

## Interpreting what happened

### Why local reduction wins

Local reduction keeps the hot loop private:

- each thread writes its own register-backed or cache-local variable
- there is almost no inter-core coherence traffic during the loop
- the shared combine step happens once, at the end

This is exactly what modern CPUs like.

### Why one shared atomic loses

With one shared atomic:

- every thread writes the same cache line
- every increment needs ownership
- ownership keeps moving between cores
- the interconnect and coherence machinery become the real bottleneck

That bottleneck does not care that the instruction is just "increment by one".

### Why false sharing hurts even though each thread has its own variable

False sharing is a layout problem, not a synchronization problem.

If `counter[0]` and `counter[1]` sit on the same cache line, then:

- Thread 0 updates `counter[0]`
- the line becomes modified in Thread 0's core
- Thread 1 updates `counter[1]`
- ownership must move, even though it is a different variable

The threads are not fighting over a variable name. They are fighting over a cache line.

### Why padding fixes so much

Padding changes the physical placement of the data:

- Thread 0 mostly owns one line
- Thread 1 mostly owns another
- invalidations drop sharply
- throughput returns much closer to the sharded baseline

This is one of the cleanest experiments you can show in a blog because the code is almost the same while the outcome is dramatically different.

## What "CPU bouncing" is and is not

This phrase can mean slightly different things depending on context, so it helps to be precise.

This post is **not** about:

- OS scheduler thread migration
- NUMA remote memory placement
- context-switch overhead

Those are real performance topics, but they are not the main phenomenon shown here.

This post **is** about:

- coherence traffic
- ownership transfer of cache lines
- repeated invalidation and refetch of lines under shared writes

If you want the most precise wording in the article, use **cache-line bouncing** or **cache-line ping-pong**. If you use "CPU bouncing" in the title, define it early so readers know you mean coherence-induced bouncing.

## How to detect this in real systems

There is no single magic "you have cache-line bouncing" message on every platform, so detection usually comes from a combination of symptoms and tools.

### Symptom-based detection

If a workload:

- uses many cores
- spends time in atomics or tight shared-write loops
- scales worse as thread count increases
- improves dramatically when sharded or padded

then cache-line contention is a strong suspect.

### Differential diagnosis through code changes

One of the best practical methods is to change layout or ownership while preserving semantics:

- replace one shared counter with per-thread counters and reduce later
- batch updates before publishing them
- add padding between frequently written per-thread slots

If the numbers improve dramatically, you have learned more than many profilers can tell you directly.

### CLI and profiler tools

On Linux:

- `perf c2c` is the strongest CLI for direct cache-to-cache contention analysis
- it can help identify hot cache lines and false sharing patterns

On this local macOS setup:

- `sample <pid>` is available and useful for seeing where CPU time goes
- `powermetrics --show-cpu-scalability --show-process-energy` is available and can help correlate poor scaling and energy burn with contention
- `xctrace` is not currently on PATH here, so the full Instruments CLI flow is not available in this shell

That means the strongest evidence in this repo comes from the benchmark deltas themselves, not from a direct Apple cache-to-cache report.

### What happened when we actually tried them here

I ran a longer shared-atomic-heavy benchmark and attached `sample` to the live process.

The `sample` output showed:

- the main thread waiting in `std::thread::join()`
- the worker threads running inside the `bench_shared_atomic(...)` worker lambda

That is useful because it confirms the hot path is the shared atomic workload.

But it still does not prove cache-line bouncing directly. `sample` is a stack snapshot tool, not a coherence profiler.

I also tried:

```bash
powermetrics --show-cpu-scalability --show-process-energy -i 1000 -n 2 -o results/powermetrics.txt
```

On this machine it failed because `powermetrics` must be run as superuser.

The captured tooling artifacts are here:

- [sample-benchmark.txt](/Users/samba/Desktop/blogs/cpu-bouncing/results/sample-benchmark.txt)
- [tooling-notes.md](/Users/samba/Desktop/blogs/cpu-bouncing/results/tooling-notes.md)

## Practical advice for real code

If you care about throughput on multi-core systems, prefer these patterns:

- shard hot counters per thread or per core
- batch writes and publish less often
- reduce at boundaries instead of updating one global atomic in the hot path
- pad frequently written per-thread structures
- separate read-mostly data from write-heavy data
- benchmark with increasing thread counts, not just one thread

Patterns to be suspicious of:

- one global atomic updated in every request
- arrays of per-thread counters packed tightly together
- spin-heavy loops around one frequently written flag
- microbenchmarks that claim atomics are cheap because they only used one thread

## A subtle but important conclusion

The lesson here is not "never use atomics".

The real lesson is:

**atomics solve correctness problems, not scalability problems**

A shared atomic is often exactly the right tool for correctness. But once it becomes a hot write location across cores, the hardware cost may dominate everything else.

That is why some lock-free designs scale beautifully while others collapse. The difference is not just in API choice. It is in how much shared write ownership they force the hardware to manage.

## Where this post can go next

There are a few natural follow-ups:

- compare `memory_order_relaxed` with stronger orderings
- port the benchmark to Go or Java to show the effect is not C++-specific
- add a spinlock and a two-thread ping-pong section as an appendix
- measure scheduler migration and distinguish it from coherence traffic

For the main article, though, I would keep the core story narrow:

1. local writes scale
2. shared writes bounce
3. false sharing is a layout trap
4. padding and sharding recover performance

That is already enough to change how many engineers think about "safe" concurrent code.
