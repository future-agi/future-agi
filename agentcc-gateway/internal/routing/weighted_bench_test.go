// Copyright 2026 Future AGI, Inc.
// SPDX-License-Identifier: Apache-2.0

package routing

import "testing"

// BenchmarkWeightedSelect measures the cost of picking one target from a
// weighted pool.
func BenchmarkWeightedSelect(b *testing.B) {
	s := &WeightedStrategy{}
	targets := []RoutingTarget{
		{ProviderID: "provider-a", Weight: 70, Healthy: true},
		{ProviderID: "provider-b", Weight: 20, Healthy: true},
		{ProviderID: "provider-c", Weight: 10, Healthy: true},
	}
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_, _ = s.Select(targets, nil)
	}
}

// BenchmarkWeightedSelectLarge measures at a more demanding pool size —
// 16 weighted targets, matching a large multi-provider deployment.
func BenchmarkWeightedSelectLarge(b *testing.B) {
	s := &WeightedStrategy{}
	targets := make([]RoutingTarget, 16)
	for i := range targets {
		targets[i] = RoutingTarget{ProviderID: "p", Weight: (i * 7) + 1, Healthy: true}
	}
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_, _ = s.Select(targets, nil)
	}
}
