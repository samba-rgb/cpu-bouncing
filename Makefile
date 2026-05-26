CXX ?= clang++
CXXFLAGS ?= -O3 -std=c++20 -Wall -Wextra -pedantic -pthread

.PHONY: all run clean

all: benchmark

benchmark: benchmark.cpp
	$(CXX) $(CXXFLAGS) benchmark.cpp -o benchmark

run: benchmark
	./benchmark

clean:
	rm -f benchmark
