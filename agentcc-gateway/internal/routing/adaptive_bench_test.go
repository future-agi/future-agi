// Copyright 2026 Future AGI, Inc.
// SPDX-License-Identifier: Apache-2.0

package routing

import "testing"

func BenchmarkAdaptiveSelect(b *testing.B) {
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

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = s.Select(targets, nil)
	}
}

func BenchmarkAdaptiveSelectParallel(b *testing.B) {
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

	b.ReportAllocs()
	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			_, _ = s.Select(targets, nil)
		}
	})
}
