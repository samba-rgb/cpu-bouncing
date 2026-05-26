#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

struct Result {
  std::string name;
  std::size_t threads;
  std::uint64_t operations;
  double seconds;
};

// Apple Silicon on this machine reports a 128-byte cache line.
// Giving each counter its own aligned slot reduces false sharing.
struct alignas(128) PaddedCounter {
  std::atomic<std::uint64_t> value{0};
};

template <typename Func>
Result run_benchmark(const std::string& name,
                     std::size_t threads,
                     std::uint64_t operations,
                     Func&& func) {
  const auto start = Clock::now();
  func();
  const auto end = Clock::now();
  std::chrono::duration<double> elapsed = end - start;
  return Result{name, threads, operations, elapsed.count()};
}

std::string format_seconds(double seconds) {
  std::ostringstream out;
  out << std::fixed << std::setprecision(6) << seconds;
  return out.str();
}

std::string format_mops(double seconds, std::uint64_t operations) {
  const double mops = static_cast<double>(operations) / seconds / 1'000'000.0;
  std::ostringstream out;
  out << std::fixed << std::setprecision(2) << mops;
  return out.str();
}

std::string format_ns_per_op(double seconds, std::uint64_t operations) {
  const double ns_per_op =
      seconds * 1'000'000'000.0 / static_cast<double>(operations);
  std::ostringstream out;
  out << std::fixed << std::setprecision(2) << ns_per_op;
  return out.str();
}

inline void do_not_optimize(std::uint64_t& value) {
#if defined(__clang__) || defined(__GNUC__)
  asm volatile("" : "+r,m"(value) : : "memory");
#else
  std::atomic_signal_fence(std::memory_order_seq_cst);
#endif
}

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

    if (counter.load(std::memory_order_relaxed) == 0) {
      std::abort();
    }
  });
}

Result bench_false_sharing(std::size_t threads,
                           std::uint64_t iterations_per_thread) {
  std::vector<std::atomic<std::uint64_t>> counters(threads);
  const std::uint64_t operations = threads * iterations_per_thread;

  for (auto& counter : counters) {
    counter.store(0, std::memory_order_relaxed);
  }

  return run_benchmark("false_sharing_adjacent_atomics",
                       threads,
                       operations,
                       [&] {
                         std::vector<std::thread> workers;
                         workers.reserve(threads);

                         for (std::size_t t = 0; t < threads; ++t) {
                           workers.emplace_back([&, t] {
                             for (std::uint64_t i = 0; i < iterations_per_thread;
                                  ++i) {
                               counters[t].fetch_add(1, std::memory_order_relaxed);
                             }
                           });
                         }

                         for (auto& worker : workers) {
                           worker.join();
                         }
                       });
}

Result bench_padded_counters(std::size_t threads,
                             std::uint64_t iterations_per_thread) {
  std::vector<PaddedCounter> counters(threads);
  const std::uint64_t operations = threads * iterations_per_thread;

  for (auto& counter : counters) {
    counter.value.store(0, std::memory_order_relaxed);
  }

  return run_benchmark("padded_per_thread_atomics", threads, operations, [&] {
    std::vector<std::thread> workers;
    workers.reserve(threads);

    for (std::size_t t = 0; t < threads; ++t) {
      workers.emplace_back([&, t] {
        for (std::uint64_t i = 0; i < iterations_per_thread; ++i) {
          counters[t].value.fetch_add(1, std::memory_order_relaxed);
        }
      });
    }

    for (auto& worker : workers) {
      worker.join();
    }
  });
}

void print_header() {
  std::cout << "name,threads,operations,seconds,mops,ns_per_op\n";
}

void print_result(const Result& result) {
  std::cout << result.name << ','
            << result.threads << ','
            << result.operations << ','
            << format_seconds(result.seconds) << ','
            << format_mops(result.seconds, result.operations) << ','
            << format_ns_per_op(result.seconds, result.operations) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
  std::size_t threads =
      std::max<std::size_t>(2, std::thread::hardware_concurrency());
  std::uint64_t iterations_per_thread = 5'000'000;

  if (argc > 1) {
    threads = static_cast<std::size_t>(std::stoull(argv[1]));
  }
  if (argc > 2) {
    iterations_per_thread = std::stoull(argv[2]);
  }

  print_header();
  print_result(bench_thread_local_reduction(threads, iterations_per_thread));
  print_result(bench_shared_atomic(threads, iterations_per_thread));
  print_result(bench_false_sharing(threads, iterations_per_thread));
  print_result(bench_padded_counters(threads, iterations_per_thread));
  return 0;
}
