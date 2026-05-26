# How a Tiny Atomic Counter Turned 10 Cores Into a Traffic Jam

_A story about atomics, false sharing, MESI, and the expensive hardware work hidden behind one innocent line of code._

There is a moment in performance work that almost feels unfair.

You look at a line of code. It is tiny. It is correct. It uses an atomic. It even says `memory_order_relaxed`.

And then it becomes the slowest thing in the system.

That was the moment that sent me down this rabbit hole.

The line looked like this:

```cpp
counter.fetch_add(1, std::memory_order_relaxed);
```

It felt impossible that this could be the problem.

No lock.
No blocking API.
No queue.
No allocation.
Just one increment.

And that is exactly why it is such a good trap.

Because when a line of code looks small, we instinctively measure the wrong thing. We think about instruction count. We think about source-code simplicity. We think about how little work the compiler must have to do.

But on a real multi-core machine, the real question is often not:

> how small is this line of code?

It is:

> how many CPU cores are about to fight over the same cache line?

That is the story of this post.

## The first mistake: confusing safe with cheap

Atomics are about correctness.

That part matters. Without atomics, a shared counter updated by many threads is a bug factory.

So when people say, "just use an atomic," they are not wrong about safety.

The mistake comes one step later, when we quietly add a second assumption:

> if it is lock-free, it must also be cheap

That assumption breaks badly on multi-core systems.

A shared atomic increment is still a shared write. And shared writes trigger hardware coordination between cores.

That coordination can become the whole cost.

## The benchmark that broke the illusion

To make this concrete, I reduced the experiment to four tiny cases:

1. `thread_local_reduction`
2. `shared_atomic_relaxed`
3. `false_sharing_adjacent_atomics`
4. `padded_per_thread_atomics`

The names sound dense, but the ideas are simple.

### Case 1: each thread keeps its own private counter

Every thread increments a local variable. At the end, the program adds the totals together.

This is the healthy version. Most writes stay private. Cores do not need to argue much.

### Case 2: every thread increments one shared atomic

Now every thread hits the same counter:

```cpp
counter.fetch_add(1, std::memory_order_relaxed);
```

This is still correct.

It is also where things start getting ugly.

### Case 3: every thread gets its own counter, but the counters sit next to each other

This one is sneaky.

The code looks independent. Thread 0 updates one counter. Thread 1 updates a different counter. Thread 2 updates yet another.

But if those counters live side by side in memory, they may still share the same cache line.

That means the hardware can still make the threads interfere with each other.

This is false sharing.

### Case 4: same idea, different layout

Now I keep the algorithm almost identical, but pad the counters so each hot counter gets its own space:

```cpp
struct alignas(128) PaddedCounter {
  std::atomic<std::uint64_t> value{0};
};
```

This case is beautiful because it changes very little at the code level, but it changes a lot at the hardware level.

If the padded version suddenly gets much faster, then the real problem was not the arithmetic. It was the memory layout.

![Cache line bouncing sequence](/Users/samba/Desktop/blogs/cpu-bouncing/images/cache-bounce-sequence.svg)

If you want the whole post in one image, that is basically it: one tiny line of memory, many cores, and far too much ownership handoff.

## What the machine said

I ran the benchmark on a local Apple Silicon machine with:

- 10 CPU cores
- 128-byte cache lines
- `clang -O3`

At 10 threads, with 5,000,000 increments per thread, the results were:

- `thread_local_reduction`: `0.009763 s`
- `shared_atomic_relaxed`: `1.867685 s`
- `false_sharing_adjacent_atomics`: `0.484675 s`
- `padded_per_thread_atomics`: `0.016501 s`

Those numbers are not subtle.

The shared atomic version was about **191.3x slower** than the local reduction version.

The false-sharing version was about **29.4x slower** than the padded version.

That is not a tiny optimization gap.

That is the hardware saying, very clearly:

> your threads are spending more time fighting than doing useful work

![Throughput chart](/Users/samba/Desktop/blogs/cpu-bouncing/results/throughput.svg)

The throughput chart tells the emotional version of the story: the "good" designs keep climbing, while the contended ones hit the ceiling and start dragging the whole machine sideways.

![Latency chart](/Users/samba/Desktop/blogs/cpu-bouncing/results/latency.svg)

The latency chart tells the mechanical version: every extra handoff has a price, and once enough threads join the fight, the price compounds.

The shape of the charts matters more than any single number.

As thread count rises:

- the local version stays strong
- the padded version stays strong
- the shared atomic version collapses
- the false-sharing version degrades badly

That pattern is the fingerprint of shared-write contention.

## What “shared atomic” actually means

When I first started reading about this, the phrase "shared atomic" sounded almost too obvious to deserve explanation.

But it turns out it is worth spelling out.

A shared atomic means multiple threads are updating the exact same atomic variable.

Like this:

```cpp
std::atomic<std::uint64_t> counter{0};

// many threads do this
counter.fetch_add(1, std::memory_order_relaxed);
```

This is safe.

The final count is correct.

But safety is not the same as scalability.

The problem is that every core that wants to update `counter` must write the same memory location. And a core cannot safely write a cache line unless the coherence machinery says that core currently owns it.

So the line does not just sit there peacefully while ten threads increment it.

It gets passed around.

Over and over.

## The missing layer: caches and coherence

To understand why this happens, we need to stop looking only at C++ and start looking at the machine.

Modern CPUs are built around cache hierarchies. Caches are what make ordinary memory access feel fast.

But caches create a problem too: if several cores keep their own copies of the same data, how do they avoid drifting into disagreement?

That is the job of cache coherence.

The classic teaching model is MESI:

- `M` = Modified
- `E` = Exclusive
- `S` = Shared
- `I` = Invalid

![MESI overview](/Users/samba/Desktop/blogs/cpu-bouncing/images/mesi-overview.png)

Here is the friendly version of those states:

- `Modified`: one core has changed the cache line, and memory has not been updated yet
- `Exclusive`: one core has the only clean copy, so it can usually upgrade to a write cheaply
- `Shared`: multiple cores may read the line, but nobody may just write it in place
- `Invalid`: the local copy is no longer trustworthy and must not be used

You do not need to memorize every transition arrow to understand the main point.

What you really need to notice is that reads and writes live very different lives.

A read can often happen from a shared copy.

A write is more demanding. A write wants control.

That is why `Shared` is comfortable for read-heavy data, but painful for write-heavy data. The moment a core wants to modify a shared line, the protocol has to kick the other copies out of the way.

The one sentence that matters most is this:

> to write a cache line, a core must own it

That single sentence explains most of the benchmark.

## The story of one increment under MESI

Imagine the counter lives inside cache line `X`.

Core 0 has recently updated that counter, so Core 0 currently owns line `X`.

Now a thread running on Core 1 wants to do this:

```cpp
counter.fetch_add(1, std::memory_order_relaxed);
```

At the source-code level, that still looks tiny.

At the hardware level, this is closer to a handoff:

1. Core 1 wants to write line `X`.
2. Core 1 cannot just write if Core 0 already owns it.
3. The coherence system has to intervene.
4. Core 0 is invalidated or downgraded.
5. Ownership moves to Core 1.
6. Only then can Core 1 safely perform the write.

That is the hidden work.

![Shared atomic under MESI](/Users/samba/Desktop/blogs/cpu-bouncing/images/mesi-write-flow.png)

This is where the MESI states stop being theory and start becoming time on a stopwatch.

One likely mental model looks like this:

- Core 0 had the line in `Modified` or `Exclusive`
- Core 1 wants to write, so Core 0 can no longer keep that version as-is
- Core 0 gets downgraded or invalidated
- Core 1 receives the line and now becomes the owner
- after Core 1 writes, it may now hold the line in `Modified`

Then the whole dance repeats in the opposite direction.

And if the next increment happens back on Core 0, the line moves back again.

That is why people call it cache-line bouncing or cache-line ping-pong.

The expensive part is not `+1`.

The expensive part is the repeated ownership transfer around `+1`.

## Why `memory_order_relaxed` does not save you

This part confused me early on, so it is worth saying slowly.

`memory_order_relaxed` can reduce ordering constraints.

What it does not do is remove the need for a valid write.

If multiple cores keep writing the same atomic, the coherence machinery still has to maintain correctness at the cache-line level.

So there are really two different questions here:

- memory ordering asks: what visibility guarantees do I need?
- cache coherence asks: which core currently owns the line I want to write?

You can relax the first question and still get crushed by the second.

That is why "but I used relaxed atomics" is not a defense against contention.

## What false sharing means

Shared atomics are the obvious version of the problem.

False sharing is the sneaky version.

In false sharing, threads are not updating the same variable. They are updating different variables that just happen to live on the same cache line.

Think about this setup:

```cpp
std::vector<std::atomic<std::uint64_t>> counters(threads);
```

Then:

- Thread 0 updates `counters[0]`
- Thread 1 updates `counters[1]`
- Thread 2 updates `counters[2]`

At the code level, this looks independent.

At the hardware level, if those counters sit close enough together, several of them may occupy the same cache line.

And coherence works at cache-line granularity, not variable-name granularity.

So the machine sees multiple cores trying to write the same line, even though the programmer sees different variables.

That is why it is called false sharing.

The sharing is not in the logic.

The sharing is in the layout.

![False sharing vs padded counters](/Users/samba/Desktop/blogs/cpu-bouncing/images/false-sharing-layout.png)

That is why false sharing is so frustrating to debug. You can read the code and feel proud that every thread has its own variable, while the CPU quietly sees one crowded apartment of hot writes.

## The most convincing result in the whole experiment

The result that changed my mental model the most was not the shared atomic benchmark.

It was the padded counters.

Because padding does not feel like a grand algorithmic breakthrough.

I did not remove threads.
I did not switch languages.
I did not turn the benchmark into something unrealistic.
I mostly changed how close hot counters sit in memory.

And the performance came roaring back.

That is a powerful lesson, because it shows that some concurrency problems are really memory-layout problems wearing an algorithm costume.

## Can tools catch this directly?

I tried a couple of tools on macOS to see what kind of supporting evidence they would give.

I used:

- `sample`
- `powermetrics --show-cpu-scalability --show-process-energy`

`sample` worked.

It showed the worker threads spending their time inside the shared atomic benchmark, while the main thread waited in `std::thread::join()`.

That is useful.

It confirms that the hot region is where we think it is.

But `sample` does not directly tell you:

- this specific cache line is bouncing
- this exact slowdown is false sharing
- this is a MESI ownership handoff bottleneck

It is a stack snapshot, not a coherence oracle.

`powermetrics` was interesting for a different reason: on this machine, it required superuser privileges, so I could not use it as part of the normal run.

That is a useful lesson too.

Performance work rarely hands you one magical yes-or-no tool. More often, you assemble the truth from:

- benchmark behavior
- controlled code changes
- profiler hints
- hardware-aware reasoning

And once those pieces line up, the story becomes hard to deny.

If you want the best direct tool for this class of problem, Linux `perf c2c` is much closer to the thing people wish existed everywhere.

## What I would tell my past self now

If I could talk to the version of myself who trusted that tiny atomic increment, I would say four things.

Do not confuse correctness with scalability.

Do not confuse lock-free with contention-free.

Do not trust the shape of a one-thread benchmark.

And whenever many threads write often, ask this before anything else:

> how many cache lines are they fighting over?

That question is often more important than the number of instructions in the loop.

## Practical advice

If your code has:

- one global counter in a hot path
- many threads updating the same queue position or flag
- tightly packed per-thread counters
- good single-thread performance and terrible multi-thread scaling

then this family of problems should be high on your list.

The usual fixes are simple in spirit:

- make writes private for longer
- batch work locally before publishing
- reduce at the end when possible
- shard shared counters
- pad hot fields that many cores update independently

In short:

publish less often, and stop making your cores fight over the same line.

## The line was never just a line

I started with one innocent atomic increment.

I ended with a much healthier respect for mechanical sympathy.

That tiny line of code was never just a tiny line of code.

It was a contract with the coherence machinery of the CPU.

And on a multi-core machine, that machinery can become the loudest, slowest, most expensive part of the whole design.

So the next time someone says:

> it is just an atomic

you should hear the hidden question behind it:

> and how many cores are about to fight over that cache line?

That is where the real story begins.

## Sources, code, and assets

Benchmark source:

- [benchmark.cpp](/Users/samba/Desktop/blogs/cpu-bouncing/benchmark.cpp:1)

Results:

- [benchmark_results.csv](/Users/samba/Desktop/blogs/cpu-bouncing/results/benchmark_results.csv)
- [summary.md](/Users/samba/Desktop/blogs/cpu-bouncing/results/summary.md)
- [tooling-notes.md](/Users/samba/Desktop/blogs/cpu-bouncing/results/tooling-notes.md)

Images:

- [MESI overview PNG](/Users/samba/Desktop/blogs/cpu-bouncing/images/mesi-overview.png)
- [Shared atomic MESI PNG](/Users/samba/Desktop/blogs/cpu-bouncing/images/mesi-write-flow.png)
- [False sharing PNG](/Users/samba/Desktop/blogs/cpu-bouncing/images/false-sharing-layout.png)
