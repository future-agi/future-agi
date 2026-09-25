// Copyright 2026 Future AGI, Inc.
// SPDX-License-Identifier: Apache-2.0

package routing

import (
	"sync"
	"testing"
)

func newActiveAdaptiveStrategy(weights map[string]float64) *AdaptiveStrategy {
	s := &AdaptiveStrategy{minWeight: 0.05}
	s.weights.Store(&adaptiveWeightSnapshot{byProvider: weights})
	s.phase.Store(1)
	return s
}

func TestAdaptiveSelectActiveRespectsWeights(t *testing.T) {
	s := newActiveAdaptiveStrategy(map[string]float64{
		"provider-a": 0.7,
		"provider-b": 0.2,
		"provider-c": 0.1,
	})
	targets := []RoutingTarget{
		{ProviderID: "provider-a", Healthy: true},
		{ProviderID: "provider-b", Healthy: true},
		{ProviderID: "provider-c", Healthy: true},
	}

	counts := make([]int, len(targets))
	for range 100_000 {
		idx, err := s.Select(targets, nil)
		if err != nil {
			t.Fatalf("Select() error = %v", err)
		}
		if idx < 0 || idx >= len(targets) {
			t.Fatalf("Select() index = %d, want [0, %d)", idx, len(targets))
		}
		counts[idx]++
	}

	if !(counts[0] > counts[1] && counts[1] > counts[2]) {
		t.Fatalf("selection counts = %v, want provider-a > provider-b > provider-c", counts)
	}
}

func TestAdaptiveSelectActiveDoesNotAllocate(t *testing.T) {
	s := newActiveAdaptiveStrategy(map[string]float64{
		"provider-a": 0.7,
		"provider-b": 0.2,
		"provider-c": 0.1,
	})
	targets := []RoutingTarget{
		{ProviderID: "provider-a", Healthy: true},
		{ProviderID: "provider-b", Healthy: true},
		{ProviderID: "provider-c", Healthy: true},
	}

	allocs := testing.AllocsPerRun(1_000, func() {
		_, _ = s.Select(targets, nil)
	})
	if allocs != 0 {
		t.Fatalf("Select() allocations = %v, want 0", allocs)
	}
}

func TestAdaptiveGetWeightsReturnsCopy(t *testing.T) {
	s := newActiveAdaptiveStrategy(map[string]float64{"provider-a": 1})

	got := s.GetWeights()
	got["provider-a"] = 0
	got["provider-b"] = 1

	want := s.GetWeights()
	if want["provider-a"] != 1 {
		t.Fatalf("provider-a weight = %v, want 1", want["provider-a"])
	}
	if _, ok := want["provider-b"]; ok {
		t.Fatal("GetWeights() result mutated the published snapshot")
	}
}

func TestAdaptiveSelectConcurrentSnapshotUpdates(t *testing.T) {
	s := newActiveAdaptiveStrategy(map[string]float64{
		"provider-a": 0.7,
		"provider-b": 0.3,
	})
	targets := []RoutingTarget{
		{ProviderID: "provider-a", Healthy: true},
		{ProviderID: "provider-b", Healthy: true},
	}
	snapshots := []*adaptiveWeightSnapshot{
		{byProvider: map[string]float64{"provider-a": 0.7, "provider-b": 0.3}},
		{byProvider: map[string]float64{"provider-a": 0.2, "provider-b": 0.8}},
	}

	var wg sync.WaitGroup
	wg.Add(9)
	go func() {
		defer wg.Done()
		for i := 0; i < 10_000; i++ {
			s.weights.Store(snapshots[i%len(snapshots)])
		}
	}()
	for range 8 {
		go func() {
			defer wg.Done()
			for range 10_000 {
				idx, err := s.Select(targets, nil)
				if err != nil {
					t.Errorf("Select() error = %v", err)
					return
				}
				if idx < 0 || idx >= len(targets) {
					t.Errorf("Select() index = %d, want [0, %d)", idx, len(targets))
					return
				}
			}
		}()
	}
	wg.Wait()
}
