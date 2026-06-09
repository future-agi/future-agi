package routing

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

func makeTargets(ids ...string) []RoutingTarget {
	targets := make([]RoutingTarget, len(ids))
	for i, id := range ids {
		targets[i] = RoutingTarget{
			ProviderID: id,
			Weight:     1,
			Priority:   0,
			Healthy:    true,
		}
	}
	return targets
}

// ---------------------------------------------------------------------------
// Strategy Tests
// ---------------------------------------------------------------------------

// 1. RoundRobin - even distribution across targets, wraps around
func TestRoundRobin_EvenDistribution(t *testing.T) {
	rr := NewRoundRobin()
	targets := makeTargets("a", "b", "c")

	counts := make(map[int]int)
	const iterations = 300
	for i := 0; i < iterations; i++ {
		idx, err := rr.Select(targets, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		counts[idx]++
	}

	// Each target should be selected exactly iterations/len(targets) times.
	expected := iterations / len(targets)
	for i := 0; i < len(targets); i++ {
		if counts[i] != expected {
			t.Errorf("target %d: got %d selections, want %d", i, counts[i], expected)
		}
	}

	// Verify wrap-around: indices should cycle 0,1,2,0,1,2,...
	rr2 := NewRoundRobin()
	for i := 0; i < 7; i++ {
		idx, _ := rr2.Select(targets, nil)
		wantIdx := i % len(targets)
		if idx != wantIdx {
			t.Errorf("iteration %d: got index %d, want %d", i, idx, wantIdx)
		}
	}
}

// 2. RoundRobin - single target returns index 0
func TestRoundRobin_SingleTarget(t *testing.T) {
	rr := NewRoundRobin()
	targets := makeTargets("only")

	for i := 0; i < 10; i++ {
		idx, err := rr.Select(targets, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if idx != 0 {
			t.Errorf("iteration %d: got index %d, want 0", i, idx)
		}
	}
}

// 3. Weighted - distribution proportional to weights (statistical test)
func TestWeighted_ProportionalDistribution(t *testing.T) {
	ws := &WeightedStrategy{}
	targets := []RoutingTarget{
		{ProviderID: "heavy", Weight: 70, Healthy: true},
		{ProviderID: "medium", Weight: 20, Healthy: true},
		{ProviderID: "light", Weight: 10, Healthy: true},
	}

	counts := make(map[int]int)
	const iterations = 10000
	for i := 0; i < iterations; i++ {
		idx, err := ws.Select(targets, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		counts[idx]++
	}

	totalWeight := 70 + 20 + 10
	tolerance := 0.05 // 5% tolerance
	for i, expected := range []int{70, 20, 10} {
		ratio := float64(counts[i]) / float64(iterations)
		expectedRatio := float64(expected) / float64(totalWeight)
		diff := math.Abs(ratio - expectedRatio)
		if diff > tolerance {
			t.Errorf("target %d (weight %d): ratio=%.4f, expected=%.4f, diff=%.4f > tolerance %.4f",
				i, expected, ratio, expectedRatio, diff, tolerance)
		}
	}
}

// 4. Weighted - targets with weight 0 get default weight 1
func TestWeighted_ZeroWeightDefaultsToOne(t *testing.T) {
	ws := &WeightedStrategy{}
	targets := []RoutingTarget{
		{ProviderID: "a", Weight: 0, Healthy: true},
		{ProviderID: "b", Weight: 0, Healthy: true},
	}

	counts := make(map[int]int)
	const iterations = 10000
	for i := 0; i < iterations; i++ {
		idx, err := ws.Select(targets, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		counts[idx]++
	}

	// Both should get roughly equal selection (50/50)
	ratio := float64(counts[0]) / float64(iterations)
	if math.Abs(ratio-0.5) > 0.05 {
		t.Errorf("expected ~50/50 distribution, got target 0: %.1f%%, target 1: %.1f%%",
			ratio*100, (1-ratio)*100)
	}
}

// 5. LeastLatency - picks target with lowest EWMA latency
func TestLeastLatency_PicksLowest(t *testing.T) {
	tracker := NewLatencyTracker(0.3)
	tracker.Record("slow", 500*time.Millisecond)
	tracker.Record("fast", 50*time.Millisecond)
	tracker.Record("medium", 200*time.Millisecond)

	targets := []RoutingTarget{
		{ProviderID: "slow", Healthy: true},
		{ProviderID: "fast", Healthy: true},
		{ProviderID: "medium", Healthy: true},
	}

	s := &LeastLatencyStrategy{}
	idx, err := s.Select(targets, tracker)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if idx != 1 {
		t.Errorf("expected index 1 (fast), got %d", idx)
	}
}

// 6. LeastLatency - with no tracker data, picks first target (latency=0 for all)
func TestLeastLatency_NoDataPicksFirst(t *testing.T) {
	tracker := NewLatencyTracker(0.3)
	targets := makeTargets("a", "b", "c")

	s := &LeastLatencyStrategy{}
	idx, err := s.Select(targets, tracker)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if idx != 0 {
		t.Errorf("expected index 0 with no data, got %d", idx)
	}
}

// 7. LeastLatency - nil tracker returns index 0
func TestLeastLatency_NilTracker(t *testing.T) {
	targets := makeTargets("a", "b")

	s := &LeastLatencyStrategy{}
	idx, err := s.Select(targets, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if idx != 0 {
		t.Errorf("expected index 0 with nil tracker, got %d", idx)
	}
}

// 8. CostOptimized - picks lowest priority group
func TestCostOptimized_PicksLowestPriority(t *testing.T) {
	co := NewCostOptimized()
	targets := []RoutingTarget{
		{ProviderID: "expensive", Priority: 10, Healthy: true},
		{ProviderID: "cheap", Priority: 1, Healthy: true},
		{ProviderID: "medium", Priority: 5, Healthy: true},
	}

	idx, err := co.Select(targets, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if idx != 1 {
		t.Errorf("expected index 1 (cheap, priority 1), got %d", idx)
	}
}

// 9. CostOptimized - round-robins within same priority group
func TestCostOptimized_RoundRobinWithinGroup(t *testing.T) {
	co := NewCostOptimized()
	targets := []RoutingTarget{
		{ProviderID: "a", Priority: 1, Healthy: true},
		{ProviderID: "expensive", Priority: 10, Healthy: true},
		{ProviderID: "b", Priority: 1, Healthy: true},
	}

	// The lowest priority group has indices 0 and 2.
	// Should round-robin between them.
	results := make([]int, 6)
	for i := 0; i < 6; i++ {
		idx, err := co.Select(targets, nil)
		if err != nil {
			t.Fatalf("iteration %d: unexpected error: %v", i, err)
		}
		results[i] = idx
	}

	// Expected: alternating 0, 2, 0, 2, 0, 2
	for i, got := range results {
		var want int
		if i%2 == 0 {
			want = 0
		} else {
			want = 2
		}
		if got != want {
			t.Errorf("iteration %d: got index %d, want %d (results: %v)", i, got, want, results)
		}
	}
}

// 10. NewStrategy - valid names return correct types
func TestNewStrategy_ValidNames(t *testing.T) {
	tests := []struct {
		name     string
		wantName string
		wantType string
	}{
		{"round-robin", "round-robin", "*routing.RoundRobinStrategy"},
		{"weighted", "weighted", "*routing.WeightedStrategy"},
		{"least-latency", "least-latency", "*routing.LeastLatencyStrategy"},
		{"cost-optimized", "cost-optimized", "*routing.CostOptimizedStrategy"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			s, err := NewStrategy(tc.name)
			if err != nil {
				t.Fatalf("NewStrategy(%q) returned error: %v", tc.name, err)
			}
			if s.Name() != tc.wantName {
				t.Errorf("Name() = %q, want %q", s.Name(), tc.wantName)
			}
			gotType := fmt.Sprintf("%T", s)
			if gotType != tc.wantType {
				t.Errorf("type = %s, want %s", gotType, tc.wantType)
			}
		})
	}
}

// 11. NewStrategy - empty string defaults to round-robin
func TestNewStrategy_EmptyDefaultsToRoundRobin(t *testing.T) {
	s, err := NewStrategy("")
	if err != nil {
		t.Fatalf("NewStrategy(\"\") returned error: %v", err)
	}
	if s.Name() != "round-robin" {
		t.Errorf("Name() = %q, want \"round-robin\"", s.Name())
	}
}

// 12. NewStrategy - invalid name returns error
func TestNewStrategy_InvalidNameReturnsError(t *testing.T) {
	_, err := NewStrategy("magic-routing")
	if err == nil {
		t.Fatal("expected error for invalid strategy name, got nil")
	}
}

// ---------------------------------------------------------------------------
// LatencyTracker Tests
// ---------------------------------------------------------------------------

// 13. Record and Get - basic recording
func TestLatencyTracker_RecordAndGet(t *testing.T) {
	tracker := NewLatencyTracker(1.0) // alpha=1 means EWMA = last value exactly.
	tracker.Record("p1", 100*time.Millisecond)

	got := tracker.Get("p1")
	if got != 100.0 {
		t.Errorf("Get(p1) = %.2f, want 100.0", got)
	}
}

// 14. EWMA update - values converge toward recent observations
func TestLatencyTracker_EWMAConvergence(t *testing.T) {
	alpha := 0.5
	tracker := NewLatencyTracker(alpha)

	// First record sets the EWMA directly.
	tracker.Record("p1", 100*time.Millisecond)
	got := tracker.Get("p1")
	if got != 100.0 {
		t.Fatalf("after first record: got %.2f, want 100.0", got)
	}

	// Second record: EWMA = alpha*new + (1-alpha)*old = 0.5*200 + 0.5*100 = 150
	tracker.Record("p1", 200*time.Millisecond)
	got = tracker.Get("p1")
	expected := 150.0
	if math.Abs(got-expected) > 0.01 {
		t.Errorf("after second record: got %.2f, want %.2f", got, expected)
	}

	// Third record: EWMA = 0.5*200 + 0.5*150 = 175
	tracker.Record("p1", 200*time.Millisecond)
	got = tracker.Get("p1")
	expected = 175.0
	if math.Abs(got-expected) > 0.01 {
		t.Errorf("after third record: got %.2f, want %.2f", got, expected)
	}
}

// 15. Unknown provider returns 0
func TestLatencyTracker_UnknownProviderReturnsZero(t *testing.T) {
	tracker := NewLatencyTracker(0.3)
	got := tracker.Get("nonexistent")
	if got != 0 {
		t.Errorf("Get(nonexistent) = %.2f, want 0", got)
	}
}

// Test that invalid alpha values get clamped to 0.3
func TestLatencyTracker_InvalidAlpha(t *testing.T) {
	// alpha <= 0 should default to 0.3
	tracker := NewLatencyTracker(0)
	tracker.Record("p", 100*time.Millisecond)
	tracker.Record("p", 200*time.Millisecond)
	// EWMA = 0.3*200 + 0.7*100 = 60 + 70 = 130
	got := tracker.Get("p")
	expected := 130.0
	if math.Abs(got-expected) > 0.01 {
		t.Errorf("with alpha=0 (default 0.3): got %.2f, want %.2f", got, expected)
	}

	// alpha > 1 should default to 0.3
	tracker2 := NewLatencyTracker(1.5)
	tracker2.Record("p", 100*time.Millisecond)
	tracker2.Record("p", 200*time.Millisecond)
	got2 := tracker2.Get("p")
	if math.Abs(got2-expected) > 0.01 {
		t.Errorf("with alpha=1.5 (default 0.3): got %.2f, want %.2f", got2, expected)
	}
}

// ---------------------------------------------------------------------------
// Router Tests
// ---------------------------------------------------------------------------

func validProviderIDs() map[string]bool {
	return map[string]bool{
		"openai":    true,
		"anthropic": true,
		"azure":     true,
	}
}

func validModelProviders() map[string][]string {
	return map[string][]string{
		"gpt-4":   {"openai", "azure"},
		"claude":  {"anthropic"},
		"gpt-3.5": {"openai"},
	}
}

// 16. NewRouter - valid config creates router
func TestNewRouter_ValidConfig(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 70},
				{Provider: "azure", Weight: 30},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter returned error: %v", err)
	}
	if r.Strategy() != "round-robin" {
		t.Errorf("Strategy() = %q, want \"round-robin\"", r.Strategy())
	}
}

// 17. NewRouter - invalid strategy returns error
func TestNewRouter_InvalidStrategy(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "invalid-strategy",
	}

	_, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err == nil {
		t.Fatal("expected error for invalid strategy, got nil")
	}
}

// 18. NewRouter - unknown provider in targets returns error
func TestNewRouter_UnknownProvider(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "nonexistent-provider", Weight: 1},
			},
		},
	}

	_, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err == nil {
		t.Fatal("expected error for unknown provider, got nil")
	}
}

// 19. HasTargets - true for multi-provider models
func TestRouter_HasTargets_True(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 70},
				{Provider: "azure", Weight: 30},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}
	if !r.HasTargets("gpt-4") {
		t.Error("HasTargets(gpt-4) = false, want true")
	}
}

// 20. HasTargets - false for single-provider models
func TestRouter_HasTargets_False(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}
	// "claude" has only 1 provider, so no auto-targets are built.
	if r.HasTargets("claude") {
		t.Error("HasTargets(claude) = true, want false")
	}
	// Unknown model also returns false.
	if r.HasTargets("unknown-model") {
		t.Error("HasTargets(unknown-model) = true, want false")
	}
}

// 21. Select - round-robin distributes across targets
func TestRouter_Select_RoundRobin(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	counts := make(map[string]int)
	for i := 0; i < 100; i++ {
		result, err := r.Select("gpt-4")
		if err != nil {
			t.Fatalf("Select error: %v", err)
		}
		counts[result.Target.ProviderID]++
		if result.StrategyName != "round-robin" {
			t.Errorf("StrategyName = %q, want \"round-robin\"", result.StrategyName)
		}
	}

	if counts["openai"] != 50 || counts["azure"] != 50 {
		t.Errorf("expected 50/50 distribution, got openai=%d azure=%d", counts["openai"], counts["azure"])
	}
}

// 22. Select - unhealthy targets are excluded
func TestRouter_Select_UnhealthyExcluded(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	r.SetHealthy("azure", false)

	for i := 0; i < 10; i++ {
		result, err := r.Select("gpt-4")
		if err != nil {
			t.Fatalf("Select error: %v", err)
		}
		if result.Target.ProviderID != "openai" {
			t.Errorf("expected only openai (azure unhealthy), got %s", result.Target.ProviderID)
		}
	}
}

// 23. Select - all unhealthy returns error
func TestRouter_Select_AllUnhealthy(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	r.SetHealthy("openai", false)
	r.SetHealthy("azure", false)

	_, err = r.Select("gpt-4")
	if err == nil {
		t.Fatal("expected error when all targets unhealthy, got nil")
	}
}

// 24. Select - model override is returned in result
func TestRouter_Select_ModelOverride(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1, ModelOverride: "gpt-4-turbo"},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	result, err := r.Select("gpt-4")
	if err != nil {
		t.Fatalf("Select error: %v", err)
	}
	if result.Target.ModelOverride != "gpt-4-turbo" {
		t.Errorf("ModelOverride = %q, want \"gpt-4-turbo\"", result.Target.ModelOverride)
	}
}

// 25. Select - unknown model returns error
func TestRouter_Select_UnknownModel(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	_, err = r.Select("nonexistent-model")
	if err == nil {
		t.Fatal("expected error for unknown model, got nil")
	}
}

// 26. SetHealthy - marks provider unhealthy across all models
func TestRouter_SetHealthy_AcrossModels(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"model-a": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
			"model-b": {
				{Provider: "openai", Weight: 1},
				{Provider: "anthropic", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	r.SetHealthy("openai", false)

	// model-a: only azure should be available
	for i := 0; i < 5; i++ {
		result, err := r.Select("model-a")
		if err != nil {
			t.Fatalf("Select(model-a) error: %v", err)
		}
		if result.Target.ProviderID != "azure" {
			t.Errorf("model-a: expected azure, got %s", result.Target.ProviderID)
		}
	}

	// model-b: only anthropic should be available
	for i := 0; i < 5; i++ {
		result, err := r.Select("model-b")
		if err != nil {
			t.Fatalf("Select(model-b) error: %v", err)
		}
		if result.Target.ProviderID != "anthropic" {
			t.Errorf("model-b: expected anthropic, got %s", result.Target.ProviderID)
		}
	}

	// Restore health.
	r.SetHealthy("openai", true)
	// Now openai should be selectable again for model-a.
	found := false
	for i := 0; i < 20; i++ {
		result, _ := r.Select("model-a")
		if result.Target.ProviderID == "openai" {
			found = true
			break
		}
	}
	if !found {
		t.Error("after SetHealthy(openai, true), openai was never selected for model-a")
	}
}

// 27. RecordLatency - updates latency tracker
func TestRouter_RecordLatency(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "least-latency",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	// Record latencies: azure is faster.
	r.RecordLatency("openai", 500*time.Millisecond)
	r.RecordLatency("azure", 50*time.Millisecond)

	// Least-latency should now prefer azure.
	result, err := r.Select("gpt-4")
	if err != nil {
		t.Fatalf("Select error: %v", err)
	}
	if result.Target.ProviderID != "azure" {
		t.Errorf("expected azure (lower latency), got %s", result.Target.ProviderID)
	}
}

// 28. Auto-target building - models with multiple providers get auto-targets
func TestRouter_AutoTargets_MultipleProviders(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		// No explicit targets - auto-build should kick in.
	}

	modelProviders := map[string][]string{
		"gpt-4":  {"openai", "azure"},
		"claude": {"anthropic"},
	}

	r, err := NewRouter(cfg, validProviderIDs(), modelProviders)
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	// gpt-4 has 2 providers, should have auto-targets.
	if !r.HasTargets("gpt-4") {
		t.Error("HasTargets(gpt-4) = false, want true (auto-built)")
	}
	if r.TargetCount("gpt-4") != 2 {
		t.Errorf("TargetCount(gpt-4) = %d, want 2", r.TargetCount("gpt-4"))
	}

	// claude has 1 provider, should NOT have auto-targets.
	if r.HasTargets("claude") {
		t.Error("HasTargets(claude) = true, want false (single provider)")
	}
}

// 29. Auto-target building - explicit config takes precedence over auto
func TestRouter_ExplicitConfigTakesPrecedence(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 100},
			},
		},
	}

	modelProviders := map[string][]string{
		"gpt-4": {"openai", "azure"},
	}

	r, err := NewRouter(cfg, validProviderIDs(), modelProviders)
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	// Explicit config has only 1 target, even though modelProviders has 2.
	// HasTargets returns false because len(targets) is 1 (not > 1).
	if r.HasTargets("gpt-4") {
		t.Error("HasTargets(gpt-4) = true, want false (explicit config has 1 target)")
	}
	if r.TargetCount("gpt-4") != 1 {
		t.Errorf("TargetCount(gpt-4) = %d, want 1 (explicit config takes precedence)", r.TargetCount("gpt-4"))
	}

	// The single target should be openai with weight 100.
	result, err := r.Select("gpt-4")
	if err != nil {
		t.Fatalf("Select error: %v", err)
	}
	if result.Target.ProviderID != "openai" {
		t.Errorf("expected openai from explicit config, got %s", result.Target.ProviderID)
	}
	if result.Target.Weight != 100 {
		t.Errorf("expected weight 100, got %d", result.Target.Weight)
	}
}

// ---------------------------------------------------------------------------
// Additional edge cases
// ---------------------------------------------------------------------------

// NewRouter with empty config (no targets, no model providers)
func TestNewRouter_EmptyConfig(t *testing.T) {
	cfg := config.RoutingConfig{}
	r, err := NewRouter(cfg, map[string]bool{}, map[string][]string{})
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}
	if r.Strategy() != "round-robin" {
		t.Errorf("Strategy() = %q, want \"round-robin\" (default)", r.Strategy())
	}
}

// Weight normalization in NewRouter (weight <= 0 becomes 1)
func TestNewRouter_WeightNormalization(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 0},
				{Provider: "azure", Weight: -5},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	// Both should get weight 1 after normalization.
	result1, _ := r.Select("gpt-4")
	if result1.Target.Weight != 1 {
		t.Errorf("expected weight normalized to 1, got %d", result1.Target.Weight)
	}
}

// Concurrent Select calls (verifies -race safety for concurrent reads).
// Note: Router.Select reads targets outside the lock after copying the slice
// header, which means concurrent Select+SetHealthy is a known race in the
// production code. This test exercises the safe concurrent patterns only:
// concurrent Select calls (read-only on targets) and concurrent RecordLatency
// (protected by LatencyTracker's own mutex).
func TestRouter_ConcurrentSelect(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	var wg sync.WaitGroup

	// Concurrent Select calls.
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				_, _ = r.Select("gpt-4")
			}
		}()
	}

	// Concurrent RecordLatency calls.
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 50; j++ {
				r.RecordLatency("openai", 100*time.Millisecond)
				r.RecordLatency("azure", 80*time.Millisecond)
			}
		}()
	}

	wg.Wait()
}

// Concurrent SetHealthy calls (verifies write-write safety).
func TestRouter_ConcurrentSetHealthy(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 50; j++ {
				r.SetHealthy("openai", false)
				r.SetHealthy("openai", true)
			}
		}()
	}
	wg.Wait()
}

// RoundRobin Name()
func TestRoundRobin_Name(t *testing.T) {
	rr := NewRoundRobin()
	if rr.Name() != "round-robin" {
		t.Errorf("Name() = %q, want \"round-robin\"", rr.Name())
	}
}

// Weighted Name()
func TestWeighted_Name(t *testing.T) {
	ws := &WeightedStrategy{}
	if ws.Name() != "weighted" {
		t.Errorf("Name() = %q, want \"weighted\"", ws.Name())
	}
}

// LeastLatency Name()
func TestLeastLatency_Name(t *testing.T) {
	s := &LeastLatencyStrategy{}
	if s.Name() != "least-latency" {
		t.Errorf("Name() = %q, want \"least-latency\"", s.Name())
	}
}

// CostOptimized Name()
func TestCostOptimized_Name(t *testing.T) {
	co := NewCostOptimized()
	if co.Name() != "cost-optimized" {
		t.Errorf("Name() = %q, want \"cost-optimized\"", co.Name())
	}
}

// TargetCount returns correct value
func TestRouter_TargetCount(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}

	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	if got := r.TargetCount("gpt-4"); got != 2 {
		t.Errorf("TargetCount(gpt-4) = %d, want 2", got)
	}
	if got := r.TargetCount("nonexistent"); got != 0 {
		t.Errorf("TargetCount(nonexistent) = %d, want 0", got)
	}
}

// CostOptimized with all same priority
func TestCostOptimized_AllSamePriority(t *testing.T) {
	co := NewCostOptimized()
	targets := []RoutingTarget{
		{ProviderID: "a", Priority: 5, Healthy: true},
		{ProviderID: "b", Priority: 5, Healthy: true},
		{ProviderID: "c", Priority: 5, Healthy: true},
	}

	counts := make(map[int]int)
	for i := 0; i < 9; i++ {
		idx, err := co.Select(targets, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		counts[idx]++
	}

	// All same priority => round-robin across all 3, each gets 3.
	for i := 0; i < 3; i++ {
		if counts[i] != 3 {
			t.Errorf("target %d: got %d, want 3", i, counts[i])
		}
	}
}

// LeastLatency picks provider with 0 latency (unknown) over known high latency
func TestLeastLatency_PrefersUnknownOverHighLatency(t *testing.T) {
	tracker := NewLatencyTracker(0.3)
	tracker.Record("known-slow", 1000*time.Millisecond)
	// "unknown" has no records, so Get returns 0 => treated favorably.

	targets := []RoutingTarget{
		{ProviderID: "known-slow", Healthy: true},
		{ProviderID: "unknown", Healthy: true},
	}

	s := &LeastLatencyStrategy{}
	idx, err := s.Select(targets, tracker)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if idx != 1 {
		t.Errorf("expected index 1 (unknown, latency=0), got %d", idx)
	}
}

// LatencyTracker concurrent Record and Get
func TestLatencyTracker_ConcurrentAccess(t *testing.T) {
	tracker := NewLatencyTracker(0.3)

	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			provider := fmt.Sprintf("provider-%d", id%3)
			for j := 0; j < 100; j++ {
				tracker.Record(provider, time.Duration(j)*time.Millisecond)
				_ = tracker.Get(provider)
			}
		}(i)
	}
	wg.Wait()
}

// ---------------------------------------------------------------------------
// Failover Test Helpers
// ---------------------------------------------------------------------------

// failoverCallHelper returns a FailoverCallFunc that returns errors based on
// a per-provider map. If the provider has a nil error entry it succeeds.
func failoverCallHelper(providerErrors map[string]error) FailoverCallFunc {
	return func(ctx context.Context, providerID string, modelOverride string) error {
		if err, ok := providerErrors[providerID]; ok {
			return err
		}
		return nil // unknown provider succeeds by default
	}
}

// callRecorder wraps a FailoverCallFunc and records the providers that were called.
type callRecorder struct {
	calls []string
	inner FailoverCallFunc
}

func newCallRecorder(inner FailoverCallFunc) *callRecorder {
	return &callRecorder{inner: inner}
}

func (cr *callRecorder) Func() FailoverCallFunc {
	return func(ctx context.Context, providerID string, modelOverride string) error {
		cr.calls = append(cr.calls, providerID)
		return cr.inner(ctx, providerID, modelOverride)
	}
}

// makeFailoverRouter creates a Router with the given providers for a single model.
func makeFailoverRouter(t *testing.T, model string, providerIDs ...string) *Router {
	t.Helper()
	pids := make(map[string]bool)
	targets := make([]config.RoutingTargetConfig, 0, len(providerIDs))
	for _, pid := range providerIDs {
		pids[pid] = true
		targets = append(targets, config.RoutingTargetConfig{
			Provider: pid,
			Weight:   1,
		})
	}
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			model: targets,
		},
	}
	r, err := NewRouter(cfg, pids, nil)
	if err != nil {
		t.Fatalf("makeFailoverRouter: %v", err)
	}
	return r
}

// ---------------------------------------------------------------------------
// Failover ShouldFailover Tests
// ---------------------------------------------------------------------------

// 1. TestFailover_ShouldFailover_500 — 500 error returns true
func TestFailover_ShouldFailover_500(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, nil, nil, nil)

	err := &models.APIError{Status: 500, Type: "server_error", Code: "internal_error", Message: "internal server error"}
	if !f.ShouldFailover(err) {
		t.Error("ShouldFailover(500) = false, want true")
	}
}

// 2. TestFailover_ShouldFailover_429 — 429 returns true
func TestFailover_ShouldFailover_429(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, nil, nil, nil)

	err := &models.APIError{Status: 429, Type: "rate_limit_error", Code: "rate_limit_exceeded", Message: "rate limited"}
	if !f.ShouldFailover(err) {
		t.Error("ShouldFailover(429) = false, want true")
	}
}

// 3. TestFailover_ShouldFailover_400 — 400 client error returns false
func TestFailover_ShouldFailover_400(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, nil, nil, nil)

	err := &models.APIError{Status: 400, Type: "invalid_request_error", Code: "bad_request", Message: "bad request"}
	if f.ShouldFailover(err) {
		t.Error("ShouldFailover(400) = true, want false")
	}
}

// 4. TestFailover_ShouldFailover_401 — 401 auth error returns false
func TestFailover_ShouldFailover_401(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, nil, nil, nil)

	err := &models.APIError{Status: 401, Type: "authentication_error", Code: "invalid_api_key", Message: "unauthorized"}
	if f.ShouldFailover(err) {
		t.Error("ShouldFailover(401) = true, want false")
	}
}

// 5. TestFailover_ShouldFailover_Timeout — context.DeadlineExceeded returns true
func TestFailover_ShouldFailover_Timeout(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
		OnTimeout:   true,
	}, nil, nil, nil)

	if !f.ShouldFailover(context.DeadlineExceeded) {
		t.Error("ShouldFailover(DeadlineExceeded) = false, want true")
	}
}

// 6. TestFailover_ShouldFailover_NilError — nil returns false
func TestFailover_ShouldFailover_NilError(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, nil, nil, nil)

	if f.ShouldFailover(nil) {
		t.Error("ShouldFailover(nil) = true, want false")
	}
}

// 7. TestFailover_ShouldFailover_TimeoutDisabled — timeout with OnTimeout=false returns false
func TestFailover_ShouldFailover_TimeoutDisabled(t *testing.T) {
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
		OnTimeout:   false,
	}, nil, nil, nil)

	if f.ShouldFailover(context.DeadlineExceeded) {
		t.Error("ShouldFailover(DeadlineExceeded) with OnTimeout=false = true, want false")
	}
}

// ---------------------------------------------------------------------------
// Failover Execute Tests
// ---------------------------------------------------------------------------

// 8. TestFailover_Execute_FirstSuccess — first provider succeeds, no failover
func TestFailover_Execute_FirstSuccess(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	providerErrors := map[string]error{
		"openai": nil,
		"azure":  nil,
	}
	recorder := newCallRecorder(failoverCallHelper(providerErrors))

	result, err := f.Execute(context.Background(), "gpt-4", recorder.Func())
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result.Attempts != 1 {
		t.Errorf("Attempts = %d, want 1", result.Attempts)
	}
	if result.FallbackUsed {
		t.Error("FallbackUsed = true, want false")
	}
	if len(recorder.calls) != 1 {
		t.Errorf("expected 1 call, got %d: %v", len(recorder.calls), recorder.calls)
	}
}

// 9. TestFailover_Execute_FailoverToSecond — first fails with 500, second succeeds
func TestFailover_Execute_FailoverToSecond(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	providerErrors := map[string]error{
		"openai": &models.APIError{Status: 500, Type: "server_error", Code: "internal_error", Message: "server error"},
		"azure":  nil,
	}
	recorder := newCallRecorder(failoverCallHelper(providerErrors))

	result, err := f.Execute(context.Background(), "gpt-4", recorder.Func())
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result.Attempts != 2 {
		t.Errorf("Attempts = %d, want 2", result.Attempts)
	}
	if result.ProviderID != "azure" {
		t.Errorf("ProviderID = %q, want \"azure\"", result.ProviderID)
	}
	if !result.FallbackUsed {
		t.Error("FallbackUsed = false, want true")
	}
	if len(recorder.calls) != 2 {
		t.Errorf("expected 2 calls, got %d: %v", len(recorder.calls), recorder.calls)
	}
}

// 10. TestFailover_Execute_AllFail — all providers fail, returns last error
func TestFailover_Execute_AllFail(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	openaiErr := &models.APIError{Status: 500, Type: "server_error", Code: "internal_error", Message: "openai down"}
	azureErr := &models.APIError{Status: 502, Type: "server_error", Code: "bad_gateway", Message: "azure down"}
	providerErrors := map[string]error{
		"openai": openaiErr,
		"azure":  azureErr,
	}
	recorder := newCallRecorder(failoverCallHelper(providerErrors))

	result, err := f.Execute(context.Background(), "gpt-4", recorder.Func())
	if result != nil {
		t.Errorf("expected nil result, got %+v", result)
	}
	if err == nil {
		t.Fatal("expected error when all providers fail, got nil")
	}
	if len(recorder.calls) != 2 {
		t.Errorf("expected 2 calls (both providers tried), got %d: %v", len(recorder.calls), recorder.calls)
	}
}

// 11. TestFailover_Execute_MaxAttempts — stops at max_attempts even if more targets available
func TestFailover_Execute_MaxAttempts(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2", "p3", "p4")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 2, // Only 2 attempts even though 4 providers
	}, router, nil, nil)

	serverErr := &models.APIError{Status: 500, Type: "server_error", Code: "internal_error", Message: "fail"}
	providerErrors := map[string]error{
		"p1": serverErr,
		"p2": serverErr,
		"p3": serverErr,
		"p4": nil,
	}
	recorder := newCallRecorder(failoverCallHelper(providerErrors))

	_, err := f.Execute(context.Background(), "gpt-4", recorder.Func())
	if err == nil {
		t.Fatal("expected error due to max_attempts exhaustion, got nil")
	}
	if len(recorder.calls) != 2 {
		t.Errorf("expected exactly 2 calls (max_attempts=2), got %d: %v", len(recorder.calls), recorder.calls)
	}
}

// 12. TestFailover_Execute_NonFailoverableError — 400 error not retried
func TestFailover_Execute_NonFailoverableError(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	badRequestErr := &models.APIError{Status: 400, Type: "invalid_request_error", Code: "bad_request", Message: "invalid"}
	providerErrors := map[string]error{
		"openai": badRequestErr,
		"azure":  nil,
	}
	recorder := newCallRecorder(failoverCallHelper(providerErrors))

	_, err := f.Execute(context.Background(), "gpt-4", recorder.Func())
	if err == nil {
		t.Fatal("expected error for non-failoverable 400, got nil")
	}
	// Should have only called the first provider; 400 is not retried.
	if len(recorder.calls) != 1 {
		t.Errorf("expected 1 call (400 not retried), got %d: %v", len(recorder.calls), recorder.calls)
	}
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != 400 {
		t.Errorf("expected APIError with status 400, got: %v", err)
	}
}

// 13. TestFailover_Execute_FallbackUsedFlag — verify FallbackUsed is true when failover occurs
func TestFailover_Execute_FallbackUsedFlag(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	providerErrors := map[string]error{
		"openai": &models.APIError{Status: 503, Type: "server_error", Code: "unavailable", Message: "unavailable"},
		"azure":  nil,
	}

	result, err := f.Execute(context.Background(), "gpt-4", failoverCallHelper(providerErrors))
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if !result.FallbackUsed {
		t.Error("FallbackUsed = false, want true when failover occurred")
	}
}

// 14. TestFailover_Execute_MetadataRecorded — verify OriginalProvider and Attempts
func TestFailover_Execute_MetadataRecorded(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	providerErrors := map[string]error{
		"openai": &models.APIError{Status: 500, Type: "server_error", Code: "internal_error", Message: "fail"},
		"azure":  nil,
	}
	recorder := newCallRecorder(failoverCallHelper(providerErrors))

	result, err := f.Execute(context.Background(), "gpt-4", recorder.Func())
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}

	// OriginalProvider is the first provider tried.
	if result.OriginalProvider != recorder.calls[0] {
		t.Errorf("OriginalProvider = %q, want %q (first tried)", result.OriginalProvider, recorder.calls[0])
	}
	// ProviderID is the one that succeeded (the second one).
	if result.ProviderID != "azure" {
		t.Errorf("ProviderID = %q, want \"azure\"", result.ProviderID)
	}
	if result.Attempts != 2 {
		t.Errorf("Attempts = %d, want 2", result.Attempts)
	}
	if result.StrategyName != "round-robin" {
		t.Errorf("StrategyName = %q, want \"round-robin\"", result.StrategyName)
	}
}

// ---------------------------------------------------------------------------
// Failover IsEnabled Tests
// ---------------------------------------------------------------------------

// 15. TestFailover_IsEnabled_True — enabled with router returns true
func TestFailover_IsEnabled_True(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
	}, router, nil, nil)

	if !f.IsEnabled() {
		t.Error("IsEnabled() = false, want true")
	}
}

// 16. TestFailover_IsEnabled_Nil — nil failover returns false
func TestFailover_IsEnabled_Nil(t *testing.T) {
	var f *Failover
	if f.IsEnabled() {
		t.Error("nil Failover IsEnabled() = true, want false")
	}
}

// 17. TestFailover_IsEnabled_Disabled — disabled config returns false
func TestFailover_IsEnabled_Disabled(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	f := NewFailover(config.FailoverConfig{
		Enabled:     false,
		MaxAttempts: 3,
	}, router, nil, nil)

	if f.IsEnabled() {
		t.Error("IsEnabled() with Enabled=false = true, want false")
	}
}

// ---------------------------------------------------------------------------
// Router SelectExcluding Tests
// ---------------------------------------------------------------------------

// 18. TestRouter_SelectExcluding_Basic — excludes specified providers
func TestRouter_SelectExcluding_Basic(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
				{Provider: "anthropic", Weight: 1},
			},
		},
	}
	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	exclude := map[string]bool{"openai": true}
	for i := 0; i < 20; i++ {
		result, err := r.SelectExcluding("gpt-4", exclude)
		if err != nil {
			t.Fatalf("SelectExcluding error: %v", err)
		}
		if result.Target.ProviderID == "openai" {
			t.Errorf("iteration %d: got excluded provider \"openai\"", i)
		}
	}
}

// 19. TestRouter_SelectExcluding_AllExcluded — all excluded returns error
func TestRouter_SelectExcluding_AllExcluded(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"gpt-4": {
				{Provider: "openai", Weight: 1},
				{Provider: "azure", Weight: 1},
			},
		},
	}
	r, err := NewRouter(cfg, validProviderIDs(), validModelProviders())
	if err != nil {
		t.Fatalf("NewRouter error: %v", err)
	}

	exclude := map[string]bool{"openai": true, "azure": true}
	_, err = r.SelectExcluding("gpt-4", exclude)
	if err == nil {
		t.Fatal("expected error when all providers excluded, got nil")
	}
}

// ---------------------------------------------------------------------------
// Retryer Tests
// ---------------------------------------------------------------------------

// fastRetryConfig returns a RetryConfig with very short delays for fast tests.
func fastRetryConfig() config.RetryConfig {
	return config.RetryConfig{
		Enabled:       true,
		MaxRetries:    3,
		InitialDelay:  1 * time.Millisecond,
		MaxDelay:      10 * time.Millisecond,
		Multiplier:    2.0,
		OnStatusCodes: []int{429, 500, 502, 503, 504},
		OnTimeout:     true,
	}
}

// 1. TestRetryer_IsEnabled_True — enabled config returns true
func TestRetryer_IsEnabled_True(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	if !r.IsEnabled() {
		t.Error("IsEnabled() = false, want true")
	}
}

// 2. TestRetryer_IsEnabled_Nil — nil retryer returns false
func TestRetryer_IsEnabled_Nil(t *testing.T) {
	var r *Retryer
	if r.IsEnabled() {
		t.Error("nil Retryer IsEnabled() = true, want false")
	}
}

// 3. TestRetryer_IsEnabled_Disabled — disabled config returns false
func TestRetryer_IsEnabled_Disabled(t *testing.T) {
	cfg := fastRetryConfig()
	cfg.Enabled = false
	r := NewRetryer(cfg)
	if r.IsEnabled() {
		t.Error("IsEnabled() with Enabled=false = true, want false")
	}
}

// 4. TestRetryer_ShouldRetry_500 — 500 returns true
func TestRetryer_ShouldRetry_500(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	err := &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
	if !r.ShouldRetry(err) {
		t.Error("ShouldRetry(500) = false, want true")
	}
}

// 5. TestRetryer_ShouldRetry_429 — 429 returns true
func TestRetryer_ShouldRetry_429(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	err := &models.APIError{Status: 429, Code: "rate_limit", Message: "too many requests"}
	if !r.ShouldRetry(err) {
		t.Error("ShouldRetry(429) = false, want true")
	}
}

// 6. TestRetryer_ShouldRetry_400 — 400 returns false
func TestRetryer_ShouldRetry_400(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	err := &models.APIError{Status: 400, Code: "bad_request", Message: "invalid request"}
	if r.ShouldRetry(err) {
		t.Error("ShouldRetry(400) = true, want false")
	}
}

// 7. TestRetryer_ShouldRetry_Timeout — DeadlineExceeded returns true
func TestRetryer_ShouldRetry_Timeout(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	if !r.ShouldRetry(context.DeadlineExceeded) {
		t.Error("ShouldRetry(DeadlineExceeded) = false, want true")
	}
}

// 8. TestRetryer_ShouldRetry_TimeoutDisabled — OnTimeout=false, DeadlineExceeded returns false
func TestRetryer_ShouldRetry_TimeoutDisabled(t *testing.T) {
	cfg := fastRetryConfig()
	cfg.OnTimeout = false
	r := NewRetryer(cfg)
	if r.ShouldRetry(context.DeadlineExceeded) {
		t.Error("ShouldRetry(DeadlineExceeded) with OnTimeout=false = true, want false")
	}
}

// 9. TestRetryer_Execute_FirstSuccess — fn succeeds on first call, retries=0
func TestRetryer_Execute_FirstSuccess(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	calls := 0
	retries, err := r.Execute(context.Background(), func() error {
		calls++
		return nil
	})
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if retries != 0 {
		t.Errorf("retries = %d, want 0", retries)
	}
	if calls != 1 {
		t.Errorf("calls = %d, want 1", calls)
	}
}

// 10. TestRetryer_Execute_SuccessAfterRetry — fn fails once with 500 then succeeds, retries=1
func TestRetryer_Execute_SuccessAfterRetry(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	calls := 0
	retries, err := r.Execute(context.Background(), func() error {
		calls++
		if calls == 1 {
			return &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if retries != 1 {
		t.Errorf("retries = %d, want 1", retries)
	}
	if calls != 2 {
		t.Errorf("calls = %d, want 2", calls)
	}
}

// 11. TestRetryer_Execute_MaxRetries — fn always fails, stops at max_retries
func TestRetryer_Execute_MaxRetries(t *testing.T) {
	cfg := fastRetryConfig()
	cfg.MaxRetries = 3
	r := NewRetryer(cfg)
	calls := 0
	retries, err := r.Execute(context.Background(), func() error {
		calls++
		return &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
	})
	if err == nil {
		t.Fatal("Execute returned nil error, want error")
	}
	// 1 initial + 3 retries = 4 total calls
	if calls != 4 {
		t.Errorf("calls = %d, want 4", calls)
	}
	if retries != 3 {
		t.Errorf("retries = %d, want 3 (max_retries)", retries)
	}
}

// 12. TestRetryer_Execute_NonRetryableError — 400 error stops immediately
func TestRetryer_Execute_NonRetryableError(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	calls := 0
	retries, err := r.Execute(context.Background(), func() error {
		calls++
		return &models.APIError{Status: 400, Code: "bad_request", Message: "invalid"}
	})
	if err == nil {
		t.Fatal("Execute returned nil error, want error")
	}
	if calls != 1 {
		t.Errorf("calls = %d, want 1 (should not retry 400)", calls)
	}
	if retries != 0 {
		t.Errorf("retries = %d, want 0", retries)
	}
}

// 13. TestRetryer_Execute_Disabled — when disabled, calls fn once
func TestRetryer_Execute_Disabled(t *testing.T) {
	cfg := fastRetryConfig()
	cfg.Enabled = false
	r := NewRetryer(cfg)
	calls := 0
	retries, err := r.Execute(context.Background(), func() error {
		calls++
		return &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
	})
	if err == nil {
		t.Fatal("Execute returned nil error, want error")
	}
	if calls != 1 {
		t.Errorf("calls = %d, want 1 (disabled retryer should call once)", calls)
	}
	if retries != 0 {
		t.Errorf("retries = %d, want 0 (disabled retryer)", retries)
	}
}

// 14. TestRetryer_Execute_ContextCancellation — cancelled context stops retry loop
func TestRetryer_Execute_ContextCancellation(t *testing.T) {
	r := NewRetryer(fastRetryConfig())
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	retries, err := r.Execute(ctx, func() error {
		calls++
		if calls == 1 {
			// Cancel after first call so the backoff select picks up ctx.Done()
			cancel()
		}
		return &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
	})
	_ = retries
	if err == nil {
		t.Fatal("Execute returned nil error, want error")
	}
	// Should have called fn once (initial), then ctx cancelled during backoff.
	if calls != 1 {
		t.Errorf("calls = %d, want 1 (context should cancel during backoff)", calls)
	}
}

// 15. TestRetryer_Backoff_Increases — verify backoff increases with attempts
func TestRetryer_Backoff_Increases(t *testing.T) {
	cfg := fastRetryConfig()
	cfg.InitialDelay = 100 * time.Millisecond
	cfg.MaxDelay = 10 * time.Second
	cfg.Multiplier = 2.0
	r := NewRetryer(cfg)

	// Compute the upper bound of backoff for each attempt.
	// backoff(attempt) returns random in [0, initialDelay * multiplier^attempt)
	// The upper bound should increase with attempt.
	prevMax := time.Duration(0)
	for attempt := 0; attempt < 5; attempt++ {
		maxDelay := time.Duration(float64(cfg.InitialDelay) * math.Pow(cfg.Multiplier, float64(attempt)))
		if maxDelay <= prevMax {
			t.Errorf("attempt %d: max backoff %v did not increase from previous %v", attempt, maxDelay, prevMax)
		}
		prevMax = maxDelay

		// Verify the actual backoff is within [0, maxDelay)
		for i := 0; i < 50; i++ {
			d := r.backoff(attempt)
			if d < 0 || d >= maxDelay {
				t.Errorf("attempt %d: backoff %v not in [0, %v)", attempt, d, maxDelay)
			}
		}
	}
}

// 16. TestRetryer_Backoff_CappedAtMaxDelay — verify delay doesn't exceed max_delay
func TestRetryer_Backoff_CappedAtMaxDelay(t *testing.T) {
	cfg := fastRetryConfig()
	cfg.InitialDelay = 1 * time.Millisecond
	cfg.MaxDelay = 5 * time.Millisecond
	cfg.Multiplier = 10.0 // Aggressive multiplier to exceed max quickly
	r := NewRetryer(cfg)

	for attempt := 0; attempt < 20; attempt++ {
		for i := 0; i < 50; i++ {
			d := r.backoff(attempt)
			if d >= cfg.MaxDelay {
				t.Errorf("attempt %d, sample %d: backoff %v >= max_delay %v", attempt, i, d, cfg.MaxDelay)
			}
			if d < 0 {
				t.Errorf("attempt %d, sample %d: backoff %v < 0", attempt, i, d)
			}
		}
	}
}


// 17. TestFailover_Execute_WithRetry — failover with retry: first provider fails
// 500 twice (retry kicks in), then succeeds on a different provider.
func TestFailover_Execute_WithRetry(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "openai", "azure")
	retryer := NewRetryer(config.RetryConfig{
		Enabled:       true,
		MaxRetries:    1,
		InitialDelay:  1 * time.Millisecond,
		MaxDelay:      10 * time.Millisecond,
		Multiplier:    2.0,
		OnStatusCodes: []int{500},
		OnTimeout:     false,
	})
	f := NewFailover(config.FailoverConfig{
		Enabled:       true,
		MaxAttempts:   3,
		OnStatusCodes: []int{500},
	}, router, retryer, nil)

	calls := 0
	var firstProvider string
	result, err := f.Execute(context.Background(), "gpt-4", func(ctx context.Context, providerID string, modelOverride string) error {
		calls++
		if firstProvider == "" {
			firstProvider = providerID
		}
		// All calls to the first provider fail with 500.
		// Calls to any other provider succeed.
		if providerID == firstProvider {
			return &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
		}
		return nil
	})

	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result == nil {
		t.Fatal("Execute returned nil result")
	}

	// The first provider should have been called 2 times (initial + 1 retry).
	// Then failover to second provider which succeeds on first try = 1 call.
	// Total calls = 3.
	if calls != 3 {
		t.Errorf("total calls = %d, want 3 (2 for first provider with retry + 1 for second)", calls)
	}

	// The result should show failover was used.
	if !result.FallbackUsed {
		t.Error("FallbackUsed = false, want true")
	}
	if result.Attempts != 2 {
		t.Errorf("Attempts = %d, want 2 (two failover attempts)", result.Attempts)
	}
	if result.ProviderID == firstProvider {
		t.Errorf("final ProviderID = %q, should be different from first provider %q", result.ProviderID, firstProvider)
	}
}

// ---------------------------------------------------------------------------
// CircuitBreaker Tests
// ---------------------------------------------------------------------------

func newTestCBConfig() config.CircuitBreakerConfig {
	return config.CircuitBreakerConfig{
		Enabled:          true,
		FailureThreshold: 3,
		SuccessThreshold: 2,
		Cooldown:         10 * time.Millisecond,
		OnStatusCodes:    []int{429, 500, 502, 503, 504},
	}
}

func serverErr() error {
	return &models.APIError{Status: 500, Code: "internal_error", Message: "server error"}
}

func clientErr() error {
	return &models.APIError{Status: 400, Code: "bad_request", Message: "bad request"}
}

// 1. TestCircuitBreaker_InitialState — starts Closed
func TestCircuitBreaker_InitialState(t *testing.T) {
	cb := NewCircuitBreaker("provider-1", newTestCBConfig(), nil)
	if cb.State() != StateClosed {
		t.Fatalf("initial state = %v, want %v", cb.State(), StateClosed)
	}
}

// 2. TestCircuitBreaker_AllowClosed — Allow() returns true when closed
func TestCircuitBreaker_AllowClosed(t *testing.T) {
	cb := NewCircuitBreaker("provider-1", newTestCBConfig(), nil)
	if !cb.Allow() {
		t.Fatal("Allow() = false when circuit is closed, want true")
	}
}

// 3. TestCircuitBreaker_OpensAfterThreshold — opens after N consecutive failures
func TestCircuitBreaker_OpensAfterThreshold(t *testing.T) {
	cfg := newTestCBConfig() // FailureThreshold = 3
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Record failures below threshold — should stay closed.
	for i := 0; i < cfg.FailureThreshold-1; i++ {
		cb.RecordFailure(serverErr())
		if cb.State() != StateClosed {
			t.Fatalf("state after %d failures = %v, want %v", i+1, cb.State(), StateClosed)
		}
	}

	// One more failure should trip the breaker open.
	cb.RecordFailure(serverErr())
	if cb.State() != StateOpen {
		t.Fatalf("state after %d failures = %v, want %v", cfg.FailureThreshold, cb.State(), StateOpen)
	}
}

// 4. TestCircuitBreaker_AllowOpen — Allow() returns false when open
func TestCircuitBreaker_AllowOpen(t *testing.T) {
	cfg := newTestCBConfig()
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Trip the breaker.
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(serverErr())
	}
	if cb.State() != StateOpen {
		t.Fatalf("state = %v, want %v", cb.State(), StateOpen)
	}
	if cb.Allow() {
		t.Fatal("Allow() = true when circuit is open, want false")
	}
}

// 5. TestCircuitBreaker_HalfOpenAfterCooldown — transitions to half-open after cooldown
func TestCircuitBreaker_HalfOpenAfterCooldown(t *testing.T) {
	cfg := newTestCBConfig() // Cooldown = 10ms
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Trip the breaker.
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(serverErr())
	}
	if cb.State() != StateOpen {
		t.Fatalf("state = %v, want %v", cb.State(), StateOpen)
	}

	// Wait for cooldown to elapse.
	time.Sleep(cfg.Cooldown + 5*time.Millisecond)

	// Allow() should transition to half-open and return true.
	if !cb.Allow() {
		t.Fatal("Allow() = false after cooldown, want true")
	}
	if cb.State() != StateHalfOpen {
		t.Fatalf("state after cooldown = %v, want %v", cb.State(), StateHalfOpen)
	}
}

// 6. TestCircuitBreaker_ClosesAfterSuccessThreshold — in half-open, N successes close it
func TestCircuitBreaker_ClosesAfterSuccessThreshold(t *testing.T) {
	cfg := newTestCBConfig() // SuccessThreshold = 2
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Trip the breaker.
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(serverErr())
	}

	// Wait for cooldown, then call Allow() to move to half-open.
	time.Sleep(cfg.Cooldown + 5*time.Millisecond)
	cb.Allow()
	if cb.State() != StateHalfOpen {
		t.Fatalf("state = %v, want %v", cb.State(), StateHalfOpen)
	}

	// Record successes below threshold — should stay half-open.
	for i := 0; i < cfg.SuccessThreshold-1; i++ {
		cb.RecordSuccess()
		if cb.State() != StateHalfOpen {
			t.Fatalf("state after %d successes = %v, want %v", i+1, cb.State(), StateHalfOpen)
		}
	}

	// One more success should close the circuit.
	cb.RecordSuccess()
	if cb.State() != StateClosed {
		t.Fatalf("state after %d successes = %v, want %v", cfg.SuccessThreshold, cb.State(), StateClosed)
	}
}

// 7. TestCircuitBreaker_ReopensOnHalfOpenFailure — in half-open, any failure re-opens
func TestCircuitBreaker_ReopensOnHalfOpenFailure(t *testing.T) {
	cfg := newTestCBConfig()
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Trip the breaker.
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(serverErr())
	}

	// Wait for cooldown, transition to half-open.
	time.Sleep(cfg.Cooldown + 5*time.Millisecond)
	cb.Allow()
	if cb.State() != StateHalfOpen {
		t.Fatalf("state = %v, want %v", cb.State(), StateHalfOpen)
	}

	// A single failure should re-open.
	cb.RecordFailure(serverErr())
	if cb.State() != StateOpen {
		t.Fatalf("state after half-open failure = %v, want %v", cb.State(), StateOpen)
	}
}

// 8. TestCircuitBreaker_SuccessResetsClosed — success in closed resets failure counter
func TestCircuitBreaker_SuccessResetsClosed(t *testing.T) {
	cfg := newTestCBConfig() // FailureThreshold = 3
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Accumulate failures just below threshold.
	for i := 0; i < cfg.FailureThreshold-1; i++ {
		cb.RecordFailure(serverErr())
	}

	// A success should reset the failure counter.
	cb.RecordSuccess()
	if cb.State() != StateClosed {
		t.Fatalf("state = %v, want %v", cb.State(), StateClosed)
	}

	// Now we should need FailureThreshold failures again to trip.
	for i := 0; i < cfg.FailureThreshold-1; i++ {
		cb.RecordFailure(serverErr())
	}
	if cb.State() != StateClosed {
		t.Fatalf("state after %d failures (post-reset) = %v, want %v",
			cfg.FailureThreshold-1, cb.State(), StateClosed)
	}

	// One more failure should now trip it (total = FailureThreshold).
	cb.RecordFailure(serverErr())
	if cb.State() != StateOpen {
		t.Fatalf("state after %d failures = %v, want %v",
			cfg.FailureThreshold, cb.State(), StateOpen)
	}
}

// 9. TestCircuitBreaker_ClientErrorDoesNotCount — 400 APIError does not increment failures
func TestCircuitBreaker_ClientErrorDoesNotCount(t *testing.T) {
	cfg := newTestCBConfig() // FailureThreshold = 3
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Record many 400 errors — should never trip the breaker.
	for i := 0; i < cfg.FailureThreshold*3; i++ {
		cb.RecordFailure(clientErr())
	}
	if cb.State() != StateClosed {
		t.Fatalf("state = %v after client errors, want %v", cb.State(), StateClosed)
	}
	if !cb.Allow() {
		t.Fatal("Allow() = false after client errors, want true")
	}
}

// 10. TestCircuitBreaker_NonAPIErrorCounts — non-API error counts as failure
func TestCircuitBreaker_NonAPIErrorCounts(t *testing.T) {
	cfg := newTestCBConfig() // FailureThreshold = 3
	cb := NewCircuitBreaker("provider-1", cfg, nil)

	// Use a plain error (simulates network/timeout errors).
	networkErr := errors.New("connection refused")
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(networkErr)
	}
	if cb.State() != StateOpen {
		t.Fatalf("state = %v after %d non-API errors, want %v",
			cb.State(), cfg.FailureThreshold, StateOpen)
	}
}

// 11. TestCircuitBreaker_StateChangeCallback — onStateChange called correctly
func TestCircuitBreaker_StateChangeCallback(t *testing.T) {
	cfg := newTestCBConfig()

	type stateChange struct {
		providerID string
		healthy    bool
	}
	var mu sync.Mutex
	var changes []stateChange

	onChange := func(pid string, healthy bool) {
		mu.Lock()
		defer mu.Unlock()
		changes = append(changes, stateChange{providerID: pid, healthy: healthy})
	}

	cb := NewCircuitBreaker("provider-1", cfg, onChange)

	// Trip the breaker: should fire (provider-1, false).
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(serverErr())
	}

	// Wait for cooldown, transition to half-open, then close it.
	time.Sleep(cfg.Cooldown + 5*time.Millisecond)
	cb.Allow() // half-open
	for i := 0; i < cfg.SuccessThreshold; i++ {
		cb.RecordSuccess()
	}
	// Should fire (provider-1, true).

	mu.Lock()
	defer mu.Unlock()
	if len(changes) != 2 {
		t.Fatalf("got %d state changes, want 2; changes: %+v", len(changes), changes)
	}
	if changes[0].providerID != "provider-1" || changes[0].healthy != false {
		t.Errorf("change[0] = %+v, want {provider-1, false}", changes[0])
	}
	if changes[1].providerID != "provider-1" || changes[1].healthy != true {
		t.Errorf("change[1] = %+v, want {provider-1, true}", changes[1])
	}
}

// ---------------------------------------------------------------------------
// CircuitBreakerRegistry Tests
// ---------------------------------------------------------------------------

// 12. TestCBRegistry_GetCreatesNew — Get() creates breaker on first call
func TestCBRegistry_GetCreatesNew(t *testing.T) {
	cfg := newTestCBConfig()
	registry := NewCircuitBreakerRegistry(cfg, nil)

	cb := registry.Get("provider-x")
	if cb == nil {
		t.Fatal("Get() returned nil for new provider")
	}
	if cb.providerID != "provider-x" {
		t.Errorf("providerID = %q, want %q", cb.providerID, "provider-x")
	}
	if cb.State() != StateClosed {
		t.Errorf("new breaker state = %v, want %v", cb.State(), StateClosed)
	}
}

// 13. TestCBRegistry_GetReturnsSame — Get() returns same breaker for same provider
func TestCBRegistry_GetReturnsSame(t *testing.T) {
	cfg := newTestCBConfig()
	registry := NewCircuitBreakerRegistry(cfg, nil)

	cb1 := registry.Get("provider-y")
	cb2 := registry.Get("provider-y")
	if cb1 != cb2 {
		t.Fatal("Get() returned different breakers for the same provider")
	}

	// Different provider should yield a different breaker.
	cb3 := registry.Get("provider-z")
	if cb1 == cb3 {
		t.Fatal("Get() returned the same breaker for different providers")
	}
}

// 14. TestCBRegistry_IsEnabled — enabled/disabled/nil returns correct value
func TestCBRegistry_IsEnabled(t *testing.T) {
	// Enabled registry.
	enabledCfg := newTestCBConfig()
	enabledCfg.Enabled = true
	enabledReg := NewCircuitBreakerRegistry(enabledCfg, nil)
	if !enabledReg.IsEnabled() {
		t.Error("IsEnabled() = false for enabled registry, want true")
	}

	// Disabled registry.
	disabledCfg := newTestCBConfig()
	disabledCfg.Enabled = false
	disabledReg := NewCircuitBreakerRegistry(disabledCfg, nil)
	if disabledReg.IsEnabled() {
		t.Error("IsEnabled() = true for disabled registry, want false")
	}

	// Nil registry.
	var nilReg *CircuitBreakerRegistry
	if nilReg.IsEnabled() {
		t.Error("IsEnabled() = true for nil registry, want false")
	}
}

// 15. TestCBRegistry_ConcurrentGet — concurrent Get calls are safe
func TestCBRegistry_ConcurrentGet(t *testing.T) {
	cfg := newTestCBConfig()
	registry := NewCircuitBreakerRegistry(cfg, nil)

	const goroutines = 100
	providers := []string{"alpha", "beta", "gamma", "delta"}

	var wg sync.WaitGroup
	results := make([]*CircuitBreaker, goroutines)

	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			pid := providers[idx%len(providers)]
			results[idx] = registry.Get(pid)
		}(i)
	}
	wg.Wait()

	// All goroutines that used the same provider should get the same breaker.
	seen := make(map[string]*CircuitBreaker)
	for i, cb := range results {
		pid := providers[i%len(providers)]
		if prev, ok := seen[pid]; ok {
			if cb != prev {
				t.Errorf("goroutine %d got different breaker for %q", i, pid)
			}
		} else {
			seen[pid] = cb
		}
	}

	// Should have exactly len(providers) unique breakers.
	if len(seen) != len(providers) {
		t.Errorf("created %d unique breakers, want %d", len(seen), len(providers))
	}
}

// ---------------------------------------------------------------------------
// ModelFallbacks Tests
// ---------------------------------------------------------------------------

// 1. TestModelFallbacks_GetChain — returns configured fallback chain
func TestModelFallbacks_GetChain(t *testing.T) {
	mf := NewModelFallbacks(map[string][]string{
		"gpt-4o":         {"claude-sonnet-4-20250514", "gemini-2.0-pro"},
		"claude-sonnet-4-20250514": {"gpt-4o"},
	})

	chain := mf.GetChain("gpt-4o")
	if len(chain) != 2 {
		t.Fatalf("GetChain(gpt-4o) length = %d, want 2", len(chain))
	}
	if chain[0] != "claude-sonnet-4-20250514" || chain[1] != "gemini-2.0-pro" {
		t.Errorf("GetChain(gpt-4o) = %v, want [claude-sonnet-4-20250514 gemini-2.0-pro]", chain)
	}

	chain2 := mf.GetChain("claude-sonnet-4-20250514")
	if len(chain2) != 1 || chain2[0] != "gpt-4o" {
		t.Errorf("GetChain(claude-sonnet-4-20250514) = %v, want [gpt-4o]", chain2)
	}
}

// 2. TestModelFallbacks_GetChain_NoFallbacks — model with no fallbacks returns nil
func TestModelFallbacks_GetChain_NoFallbacks(t *testing.T) {
	mf := NewModelFallbacks(map[string][]string{
		"gpt-4o": {"claude-sonnet-4-20250514"},
	})

	chain := mf.GetChain("unknown-model")
	if chain != nil {
		t.Errorf("GetChain(unknown-model) = %v, want nil", chain)
	}
}

// 3. TestModelFallbacks_GetChain_Nil — nil ModelFallbacks returns nil
func TestModelFallbacks_GetChain_Nil(t *testing.T) {
	var mf *ModelFallbacks
	chain := mf.GetChain("gpt-4o")
	if chain != nil {
		t.Errorf("nil ModelFallbacks GetChain = %v, want nil", chain)
	}
}

// 4. TestModelFallbacks_HasFallbacks_True — model with fallbacks returns true
func TestModelFallbacks_HasFallbacks_True(t *testing.T) {
	mf := NewModelFallbacks(map[string][]string{
		"gpt-4o": {"claude-sonnet-4-20250514"},
	})

	if !mf.HasFallbacks("gpt-4o") {
		t.Error("HasFallbacks(gpt-4o) = false, want true")
	}
}

// 5. TestModelFallbacks_HasFallbacks_False — model without fallbacks returns false
func TestModelFallbacks_HasFallbacks_False(t *testing.T) {
	mf := NewModelFallbacks(map[string][]string{
		"gpt-4o": {"claude-sonnet-4-20250514"},
	})

	if mf.HasFallbacks("unknown-model") {
		t.Error("HasFallbacks(unknown-model) = true, want false")
	}
}

// 6. TestModelFallbacks_HasFallbacks_Nil — nil ModelFallbacks returns false
func TestModelFallbacks_HasFallbacks_Nil(t *testing.T) {
	var mf *ModelFallbacks
	if mf.HasFallbacks("gpt-4o") {
		t.Error("nil ModelFallbacks HasFallbacks = true, want false")
	}
}

// 7. TestModelFallbacks_HasFallbacks_EmptyChain — model with empty chain returns false
func TestModelFallbacks_HasFallbacks_EmptyChain(t *testing.T) {
	mf := NewModelFallbacks(map[string][]string{
		"gpt-4o": {},
	})

	if mf.HasFallbacks("gpt-4o") {
		t.Error("HasFallbacks(gpt-4o) with empty chain = true, want false")
	}
}

// 8. TestModelFallbacks_NilChains — NewModelFallbacks with nil initializes empty map
func TestModelFallbacks_NilChains(t *testing.T) {
	mf := NewModelFallbacks(nil)
	if mf.HasFallbacks("anything") {
		t.Error("HasFallbacks on nil-initialized fallbacks = true, want false")
	}
	chain := mf.GetChain("anything")
	if chain != nil {
		t.Errorf("GetChain on nil-initialized fallbacks = %v, want nil", chain)
	}
}

// 9. TestModelFallbacks_OrderPreserved — fallback chain preserves order
func TestModelFallbacks_OrderPreserved(t *testing.T) {
	expected := []string{"model-a", "model-b", "model-c", "model-d", "model-e"}
	mf := NewModelFallbacks(map[string][]string{
		"primary": expected,
	})

	chain := mf.GetChain("primary")
	if len(chain) != len(expected) {
		t.Fatalf("chain length = %d, want %d", len(chain), len(expected))
	}
	for i, m := range chain {
		if m != expected[i] {
			t.Errorf("chain[%d] = %q, want %q", i, m, expected[i])
		}
	}
}

// ---------------------------------------------------------------------------
// ConditionalRouter Tests
// ---------------------------------------------------------------------------

// testRC creates a minimal RequestContext for conditional routing tests.
func testRC(model, user string, stream bool, metadata map[string]string) *models.RequestContext {
	rc := &models.RequestContext{
		Model:    model,
		UserID:   user,
		IsStream: stream,
		Metadata: metadata,
	}
	if rc.Metadata == nil {
		rc.Metadata = make(map[string]string)
	}
	return rc
}

// 1. TestConditional_EqOperator — $eq matches exact string
func TestConditional_EqOperator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "match-gpt4",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// Match.
	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action == nil {
		t.Fatal("expected match, got nil")
	}
	if action.Provider != "openai" {
		t.Errorf("Provider = %q, want \"openai\"", action.Provider)
	}

	// No match.
	action = cr.Evaluate(testRC("claude-sonnet", "", false, nil))
	if action != nil {
		t.Errorf("expected no match, got %+v", action)
	}
}

// 2. TestConditional_NeOperator — $ne matches when not equal
func TestConditional_NeOperator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "not-gpt4",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$ne",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{Provider: "anthropic"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// Should not match gpt-4o.
	if action := cr.Evaluate(testRC("gpt-4o", "", false, nil)); action != nil {
		t.Errorf("expected no match for gpt-4o, got %+v", action)
	}

	// Should match claude.
	action := cr.Evaluate(testRC("claude-sonnet", "", false, nil))
	if action == nil {
		t.Fatal("expected match for claude-sonnet, got nil")
	}
	if action.Provider != "anthropic" {
		t.Errorf("Provider = %q, want \"anthropic\"", action.Provider)
	}
}

// 3. TestConditional_InOperator — $in matches set membership
func TestConditional_InOperator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "gpt-models",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$in",
				Value: []interface{}{"gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"},
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	for _, m := range []string{"gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"} {
		if action := cr.Evaluate(testRC(m, "", false, nil)); action == nil {
			t.Errorf("expected match for %q", m)
		}
	}

	if action := cr.Evaluate(testRC("claude-sonnet", "", false, nil)); action != nil {
		t.Errorf("expected no match for claude-sonnet, got %+v", action)
	}
}

// 4. TestConditional_NinOperator — $nin matches when not in set
func TestConditional_NinOperator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "non-gpt-models",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$nin",
				Value: []interface{}{"gpt-4o", "gpt-4-turbo"},
			},
			Action: config.RouteActionConfig{Provider: "anthropic"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	if action := cr.Evaluate(testRC("gpt-4o", "", false, nil)); action != nil {
		t.Errorf("expected no match for gpt-4o")
	}

	if action := cr.Evaluate(testRC("claude-sonnet", "", false, nil)); action == nil {
		t.Fatal("expected match for claude-sonnet, got nil")
	}
}

// 5. TestConditional_RegexOperator — $regex matches pattern
func TestConditional_RegexOperator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "gpt-prefix",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$regex",
				Value: "^gpt-",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	for _, m := range []string{"gpt-4o", "gpt-3.5-turbo", "gpt-4-turbo"} {
		if action := cr.Evaluate(testRC(m, "", false, nil)); action == nil {
			t.Errorf("expected match for %q", m)
		}
	}

	if action := cr.Evaluate(testRC("claude-sonnet", "", false, nil)); action != nil {
		t.Errorf("expected no match for claude-sonnet")
	}
}

// 6. TestConditional_ExistsOperator — $exists checks field presence
func TestConditional_ExistsOperator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "has-tier",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "metadata.tier",
				Op:    "$exists",
				Value: true,
			},
			Action: config.RouteActionConfig{Provider: "dedicated"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// With tier metadata.
	action := cr.Evaluate(testRC("gpt-4o", "", false, map[string]string{"tier": "enterprise"}))
	if action == nil {
		t.Fatal("expected match when metadata.tier exists")
	}

	// Without tier metadata.
	action = cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action != nil {
		t.Error("expected no match when metadata.tier doesn't exist")
	}
}

// 7. TestConditional_BoolField — $eq on boolean field (stream)
func TestConditional_BoolField(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "non-streaming",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "stream",
				Op:    "$eq",
				Value: false,
			},
			Action: config.RouteActionConfig{Provider: "batch-provider"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// Non-streaming → match.
	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action == nil {
		t.Fatal("expected match for stream=false")
	}

	// Streaming → no match.
	action = cr.Evaluate(testRC("gpt-4o", "", true, nil))
	if action != nil {
		t.Error("expected no match for stream=true")
	}
}

// 8. TestConditional_MetadataField — metadata.* field lookup
func TestConditional_MetadataField(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "enterprise-tier",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "metadata.tier",
				Op:    "$eq",
				Value: "enterprise",
			},
			Action: config.RouteActionConfig{Provider: "dedicated"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	action := cr.Evaluate(testRC("gpt-4o", "", false, map[string]string{"tier": "enterprise"}))
	if action == nil {
		t.Fatal("expected match for tier=enterprise")
	}

	action = cr.Evaluate(testRC("gpt-4o", "", false, map[string]string{"tier": "free"}))
	if action != nil {
		t.Error("expected no match for tier=free")
	}
}

// 9. TestConditional_AndCombinator — $and requires all conditions true
func TestConditional_AndCombinator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "vip-gpt4",
			Priority: 10,
			Condition: config.ConditionConfig{
				And: []config.ConditionConfig{
					{Field: "user", Op: "$eq", Value: "user-vip"},
					{Field: "model", Op: "$eq", Value: "gpt-4o"},
				},
			},
			Action: config.RouteActionConfig{Provider: "vip-provider"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// Both match.
	action := cr.Evaluate(testRC("gpt-4o", "user-vip", false, nil))
	if action == nil {
		t.Fatal("expected match when both conditions true")
	}

	// Only user matches.
	action = cr.Evaluate(testRC("claude-sonnet", "user-vip", false, nil))
	if action != nil {
		t.Error("expected no match when only user matches")
	}

	// Only model matches.
	action = cr.Evaluate(testRC("gpt-4o", "user-regular", false, nil))
	if action != nil {
		t.Error("expected no match when only model matches")
	}
}

// 10. TestConditional_OrCombinator — $or requires at least one true
func TestConditional_OrCombinator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "gpt-or-claude",
			Priority: 10,
			Condition: config.ConditionConfig{
				Or: []config.ConditionConfig{
					{Field: "model", Op: "$eq", Value: "gpt-4o"},
					{Field: "model", Op: "$eq", Value: "claude-sonnet"},
				},
			},
			Action: config.RouteActionConfig{Provider: "premium"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// First matches.
	if action := cr.Evaluate(testRC("gpt-4o", "", false, nil)); action == nil {
		t.Error("expected match for gpt-4o")
	}

	// Second matches.
	if action := cr.Evaluate(testRC("claude-sonnet", "", false, nil)); action == nil {
		t.Error("expected match for claude-sonnet")
	}

	// Neither matches.
	if action := cr.Evaluate(testRC("gemini-pro", "", false, nil)); action != nil {
		t.Error("expected no match for gemini-pro")
	}
}

// 11. TestConditional_NotCombinator — $not inverts condition
func TestConditional_NotCombinator(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "not-streaming",
			Priority: 10,
			Condition: config.ConditionConfig{
				Not: &config.ConditionConfig{
					Field: "stream",
					Op:    "$eq",
					Value: true,
				},
			},
			Action: config.RouteActionConfig{Provider: "batch"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// stream=false → not(true)=true → match.
	if action := cr.Evaluate(testRC("gpt-4o", "", false, nil)); action == nil {
		t.Error("expected match for stream=false")
	}

	// stream=true → not(true)=false → no match.
	if action := cr.Evaluate(testRC("gpt-4o", "", true, nil)); action != nil {
		t.Error("expected no match for stream=true")
	}
}

// 12. TestConditional_PriorityOrder — lower priority number wins
func TestConditional_PriorityOrder(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "low-priority",
			Priority: 100,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$regex",
				Value: ".*",
			},
			Action: config.RouteActionConfig{Provider: "default-provider"},
		},
		{
			Name:     "high-priority",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{Provider: "vip-provider"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// gpt-4o matches both, but high-priority (10) should win.
	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action == nil {
		t.Fatal("expected match")
	}
	if action.Provider != "vip-provider" {
		t.Errorf("Provider = %q, want \"vip-provider\" (higher priority)", action.Provider)
	}

	// claude-sonnet matches only the catch-all.
	action = cr.Evaluate(testRC("claude-sonnet", "", false, nil))
	if action == nil {
		t.Fatal("expected match for catch-all")
	}
	if action.Provider != "default-provider" {
		t.Errorf("Provider = %q, want \"default-provider\"", action.Provider)
	}
}

// 13. TestConditional_NoMatch — no routes match returns nil
func TestConditional_NoMatch(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "only-gpt4",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	action := cr.Evaluate(testRC("claude-sonnet", "", false, nil))
	if action != nil {
		t.Errorf("expected nil, got %+v", action)
	}
}

// 14. TestConditional_EmptyRoutes — empty routes returns nil router
func TestConditional_EmptyRoutes(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}
	if cr != nil {
		t.Error("expected nil router for empty routes")
	}
}

// 15. TestConditional_NilRouter — nil router evaluate returns nil
func TestConditional_NilRouter(t *testing.T) {
	var cr *ConditionalRouter
	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action != nil {
		t.Errorf("nil router Evaluate = %+v, want nil", action)
	}
}

// 16. TestConditional_ModelOverride — action with model_override
func TestConditional_ModelOverride(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "override-model",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{
				Provider:      "azure",
				ModelOverride: "gpt-4o-azure",
			},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action == nil {
		t.Fatal("expected match")
	}
	if action.ModelOverride != "gpt-4o-azure" {
		t.Errorf("ModelOverride = %q, want \"gpt-4o-azure\"", action.ModelOverride)
	}
}

// 17. TestConditional_InvalidOperator — invalid op returns error
func TestConditional_InvalidOperator(t *testing.T) {
	_, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "bad-op",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$badop",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err == nil {
		t.Fatal("expected error for invalid operator")
	}
}

// 18. TestConditional_InvalidRegex — invalid regex pattern returns error
func TestConditional_InvalidRegex(t *testing.T) {
	_, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "bad-regex",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$regex",
				Value: "[invalid",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err == nil {
		t.Fatal("expected error for invalid regex")
	}
}

// 19. TestConditional_MissingProvider — missing action.provider returns error
func TestConditional_MissingProvider(t *testing.T) {
	_, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "no-provider",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{}, // no provider
		},
	})
	if err == nil {
		t.Fatal("expected error for missing provider")
	}
}

// 20. TestConditional_NestedCombinators — $and containing $or
func TestConditional_NestedCombinators(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "complex-rule",
			Priority: 10,
			Condition: config.ConditionConfig{
				And: []config.ConditionConfig{
					{
						Or: []config.ConditionConfig{
							{Field: "model", Op: "$eq", Value: "gpt-4o"},
							{Field: "model", Op: "$eq", Value: "gpt-4-turbo"},
						},
					},
					{Field: "metadata.tier", Op: "$eq", Value: "enterprise"},
				},
			},
			Action: config.RouteActionConfig{Provider: "vip"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// gpt-4o + enterprise → match.
	action := cr.Evaluate(testRC("gpt-4o", "", false, map[string]string{"tier": "enterprise"}))
	if action == nil {
		t.Fatal("expected match for gpt-4o + enterprise")
	}

	// gpt-4-turbo + enterprise → match.
	action = cr.Evaluate(testRC("gpt-4-turbo", "", false, map[string]string{"tier": "enterprise"}))
	if action == nil {
		t.Fatal("expected match for gpt-4-turbo + enterprise")
	}

	// gpt-4o + free → no match (tier doesn't match).
	action = cr.Evaluate(testRC("gpt-4o", "", false, map[string]string{"tier": "free"}))
	if action != nil {
		t.Error("expected no match for gpt-4o + free")
	}

	// claude + enterprise → no match (model doesn't match).
	action = cr.Evaluate(testRC("claude-sonnet", "", false, map[string]string{"tier": "enterprise"}))
	if action != nil {
		t.Error("expected no match for claude + enterprise")
	}
}

// 21. TestConditional_UserField — routing by user ID
func TestConditional_UserField(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "vip-user",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "user",
				Op:    "$eq",
				Value: "user-vip-001",
			},
			Action: config.RouteActionConfig{Provider: "dedicated"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	if action := cr.Evaluate(testRC("gpt-4o", "user-vip-001", false, nil)); action == nil {
		t.Error("expected match for vip user")
	}

	if action := cr.Evaluate(testRC("gpt-4o", "user-regular", false, nil)); action != nil {
		t.Error("expected no match for regular user")
	}
}

// 22. TestConditional_RouteCount — RouteCount returns correct number
func TestConditional_RouteCount(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{Name: "r1", Priority: 10, Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "a"}, Action: config.RouteActionConfig{Provider: "p1"}},
		{Name: "r2", Priority: 20, Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "b"}, Action: config.RouteActionConfig{Provider: "p2"}},
		{Name: "r3", Priority: 30, Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "c"}, Action: config.RouteActionConfig{Provider: "p3"}},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}
	if cr.RouteCount() != 3 {
		t.Errorf("RouteCount() = %d, want 3", cr.RouteCount())
	}
}

// 23. TestConditional_ActionName — action includes rule name for observability
func TestConditional_ActionName(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "enterprise-rule",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action == nil {
		t.Fatal("expected match")
	}
	if action.Name != "enterprise-rule" {
		t.Errorf("Name = %q, want \"enterprise-rule\"", action.Name)
	}
}

// 24. TestConditional_UnknownField — unknown field resolves to nil
func TestConditional_UnknownField(t *testing.T) {
	cr, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "unknown-field",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "nonexistent_field",
				Op:    "$eq",
				Value: "something",
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err != nil {
		t.Fatalf("NewConditionalRouter error: %v", err)
	}

	// Unknown field → nil, $eq against "something" → false.
	action := cr.Evaluate(testRC("gpt-4o", "", false, nil))
	if action != nil {
		t.Error("expected no match for unknown field")
	}
}

// 25. TestConditional_MixedLeafAndCombinator — returns error
func TestConditional_MixedLeafAndCombinator(t *testing.T) {
	_, err := NewConditionalRouter([]config.ConditionalRouteConfig{
		{
			Name:     "mixed",
			Priority: 10,
			Condition: config.ConditionConfig{
				Field: "model",
				Op:    "$eq",
				Value: "gpt-4o",
				And: []config.ConditionConfig{
					{Field: "user", Op: "$eq", Value: "test"},
				},
			},
			Action: config.RouteActionConfig{Provider: "openai"},
		},
	})
	if err == nil {
		t.Fatal("expected error when mixing leaf and combinator")
	}
}

// ---------------------------------------------------------------------------
// HealthMonitor Tests
// ---------------------------------------------------------------------------

// 1. TestHealthMonitor_RecordSuccess — increments request count
func TestHealthMonitor_RecordSuccess(t *testing.T) {
	hm := NewHealthMonitor(nil, nil)
	hm.RecordSuccess("openai")
	hm.RecordSuccess("openai")
	hm.RecordSuccess("openai")

	h := hm.GetHealth("openai")
	if h.RequestCount != 3 {
		t.Errorf("RequestCount = %d, want 3", h.RequestCount)
	}
	if h.ErrorCount != 0 {
		t.Errorf("ErrorCount = %d, want 0", h.ErrorCount)
	}
	if h.SuccessRate != 1.0 {
		t.Errorf("SuccessRate = %f, want 1.0", h.SuccessRate)
	}
	if h.LastSuccessTime == nil {
		t.Error("LastSuccessTime is nil, want non-nil")
	}
}

// 2. TestHealthMonitor_RecordError — increments error count and stores error
func TestHealthMonitor_RecordError(t *testing.T) {
	hm := NewHealthMonitor(nil, nil)
	hm.RecordSuccess("openai")
	hm.RecordError("openai", fmt.Errorf("timeout"))

	h := hm.GetHealth("openai")
	if h.RequestCount != 2 {
		t.Errorf("RequestCount = %d, want 2", h.RequestCount)
	}
	if h.ErrorCount != 1 {
		t.Errorf("ErrorCount = %d, want 1", h.ErrorCount)
	}
	if h.SuccessRate != 0.5 {
		t.Errorf("SuccessRate = %f, want 0.5", h.SuccessRate)
	}
	if h.LastError != "timeout" {
		t.Errorf("LastError = %q, want \"timeout\"", h.LastError)
	}
	if h.LastErrorTime == nil {
		t.Error("LastErrorTime is nil, want non-nil")
	}
}

// 3. TestHealthMonitor_SuccessRate — calculates correctly
func TestHealthMonitor_SuccessRate(t *testing.T) {
	hm := NewHealthMonitor(nil, nil)
	for i := 0; i < 7; i++ {
		hm.RecordSuccess("p1")
	}
	for i := 0; i < 3; i++ {
		hm.RecordError("p1", fmt.Errorf("err"))
	}

	h := hm.GetHealth("p1")
	if h.RequestCount != 10 {
		t.Errorf("RequestCount = %d, want 10", h.RequestCount)
	}
	expectedRate := 0.7
	if h.SuccessRate < expectedRate-0.001 || h.SuccessRate > expectedRate+0.001 {
		t.Errorf("SuccessRate = %f, want %f", h.SuccessRate, expectedRate)
	}
}

// 4. TestHealthMonitor_GetAllHealth — returns all tracked providers
func TestHealthMonitor_GetAllHealth(t *testing.T) {
	hm := NewHealthMonitor(nil, nil)
	hm.RecordSuccess("openai")
	hm.RecordSuccess("anthropic")
	hm.RecordError("gemini", fmt.Errorf("err"))

	all := hm.GetAllHealth()
	if len(all) != 3 {
		t.Fatalf("GetAllHealth returned %d providers, want 3", len(all))
	}

	ids := make(map[string]bool)
	for _, h := range all {
		ids[h.ProviderID] = true
	}
	for _, expected := range []string{"openai", "anthropic", "gemini"} {
		if !ids[expected] {
			t.Errorf("missing provider %q in GetAllHealth", expected)
		}
	}
}

// 5. TestHealthMonitor_UnknownProvider — returns defaults
func TestHealthMonitor_UnknownProvider(t *testing.T) {
	hm := NewHealthMonitor(nil, nil)
	h := hm.GetHealth("unknown")
	if h.RequestCount != 0 {
		t.Errorf("RequestCount = %d, want 0", h.RequestCount)
	}
	if h.SuccessRate != 0 {
		t.Errorf("SuccessRate = %f, want 0", h.SuccessRate)
	}
	if h.CircuitState != "n/a" {
		t.Errorf("CircuitState = %q, want \"n/a\"", h.CircuitState)
	}
	if !h.Healthy {
		t.Error("Healthy = false, want true")
	}
}

// 6. TestHealthMonitor_NilSafe — nil monitor doesn't panic
func TestHealthMonitor_NilSafe(t *testing.T) {
	var hm *HealthMonitor
	hm.RecordSuccess("openai") // should not panic
	hm.RecordError("openai", fmt.Errorf("err"))
	h := hm.GetHealth("openai")
	if h.ProviderID != "openai" {
		t.Errorf("ProviderID = %q, want \"openai\"", h.ProviderID)
	}
	all := hm.GetAllHealth()
	if all != nil {
		t.Errorf("nil monitor GetAllHealth = %v, want nil", all)
	}
}

// 7. TestHealthMonitor_WithLatencyTracker — integrates with latency data
func TestHealthMonitor_WithLatencyTracker(t *testing.T) {
	tracker := NewLatencyTracker(0.3)
	tracker.Record("openai", 200*time.Millisecond)
	tracker.Record("openai", 300*time.Millisecond)

	hm := NewHealthMonitor(tracker, nil)
	hm.RecordSuccess("openai")

	h := hm.GetHealth("openai")
	if h.LatencyEWMAMs == 0 {
		t.Error("LatencyEWMAMs = 0, expected non-zero with latency data")
	}
}

// 8. TestHealthMonitor_WithCircuitBreaker — integrates with circuit breaker state
func TestHealthMonitor_WithCircuitBreaker(t *testing.T) {
	cfg := newTestCBConfig() // FailureThreshold = 3
	cbReg := NewCircuitBreakerRegistry(cfg, nil)

	hm := NewHealthMonitor(nil, cbReg)
	hm.RecordSuccess("openai")

	// Initially closed.
	h := hm.GetHealth("openai")
	if h.CircuitState != "closed" {
		t.Errorf("CircuitState = %q, want \"closed\"", h.CircuitState)
	}
	if !h.Healthy {
		t.Error("Healthy = false, want true (closed circuit)")
	}

	// Trip the circuit breaker.
	cb := cbReg.Get("openai")
	for i := 0; i < cfg.FailureThreshold; i++ {
		cb.RecordFailure(serverErr())
	}

	h = hm.GetHealth("openai")
	if h.CircuitState != "open" {
		t.Errorf("CircuitState = %q, want \"open\"", h.CircuitState)
	}
	if h.Healthy {
		t.Error("Healthy = true, want false (open circuit)")
	}
}

// 9. TestHealthMonitor_ConcurrentAccess — safe under concurrent reads/writes
func TestHealthMonitor_ConcurrentAccess(t *testing.T) {
	hm := NewHealthMonitor(nil, nil)

	var wg sync.WaitGroup
	providers := []string{"p1", "p2", "p3"}

	// Writers.
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			pid := providers[id%len(providers)]
			for j := 0; j < 100; j++ {
				if j%3 == 0 {
					hm.RecordError(pid, fmt.Errorf("err"))
				} else {
					hm.RecordSuccess(pid)
				}
			}
		}(i)
	}

	// Readers.
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 50; j++ {
				_ = hm.GetAllHealth()
				_ = hm.GetHealth("p1")
			}
		}()
	}

	wg.Wait()

	// Verify no data corruption.
	for _, pid := range providers {
		h := hm.GetHealth(pid)
		if h.RequestCount < 0 || h.ErrorCount < 0 {
			t.Errorf("provider %s: negative counts (requests=%d, errors=%d)", pid, h.RequestCount, h.ErrorCount)
		}
		if h.ErrorCount > h.RequestCount {
			t.Errorf("provider %s: errors (%d) > requests (%d)", pid, h.ErrorCount, h.RequestCount)
		}
	}
}

// ---------------------------------------------------------------------------
// Mirror Tests (Feature 2.11)
// ---------------------------------------------------------------------------

func TestMirror_ShouldMirror_ExactMatch(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "anthropic", TargetModel: "claude-sonnet", SampleRate: 1.0},
		},
	}
	m := NewMirror(cfg, nil)

	rule, ok := m.ShouldMirror("gpt-4o")
	if !ok {
		t.Fatal("expected match for gpt-4o")
	}
	if rule.TargetProvider != "anthropic" {
		t.Errorf("target = %q, want 'anthropic'", rule.TargetProvider)
	}
	if rule.TargetModel != "claude-sonnet" {
		t.Errorf("target_model = %q, want 'claude-sonnet'", rule.TargetModel)
	}
}

func TestMirror_ShouldMirror_NoMatch(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "anthropic", SampleRate: 1.0},
		},
	}
	m := NewMirror(cfg, nil)

	_, ok := m.ShouldMirror("gpt-4o-mini")
	if ok {
		t.Error("should not match gpt-4o-mini")
	}
}

func TestMirror_ShouldMirror_Wildcard(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "*", TargetProvider: "staging", SampleRate: 1.0},
		},
	}
	m := NewMirror(cfg, nil)

	_, ok := m.ShouldMirror("any-model-xyz")
	if !ok {
		t.Error("wildcard should match any model")
	}
}

func TestMirror_ShouldMirror_SampleRateZero(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "anthropic", SampleRate: 0.0},
		},
	}
	m := NewMirror(cfg, nil)

	_, ok := m.ShouldMirror("gpt-4o")
	if ok {
		t.Error("sample rate 0 should never mirror")
	}
}

func TestMirror_ShouldMirror_FirstMatchWins(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "first", SampleRate: 1.0},
			{SourceModel: "gpt-4o", TargetProvider: "second", SampleRate: 1.0},
		},
	}
	m := NewMirror(cfg, nil)

	rule, ok := m.ShouldMirror("gpt-4o")
	if !ok {
		t.Fatal("expected match")
	}
	if rule.TargetProvider != "first" {
		t.Errorf("target = %q, want 'first' (first match wins)", rule.TargetProvider)
	}
}

func TestMirror_NilSafe(t *testing.T) {
	var m *Mirror
	_, ok := m.ShouldMirror("gpt-4o")
	if ok {
		t.Error("nil mirror should not match")
	}
	// Should not panic.
	m.ExecuteAsync(nil, "", "", nil)
}

func TestMirror_ExecuteAsync_MissingProvider(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "nonexistent", SampleRate: 1.0},
		},
	}
	lookup := func(id string) (MirrorProvider, bool) {
		return nil, false // provider not found
	}
	m := NewMirror(cfg, lookup)

	req := &models.ChatCompletionRequest{Model: "gpt-4o"}
	// Should not panic even when provider not found.
	m.ExecuteAsync(req, "openai", "gpt-4o", nil)
}

func TestMirror_ExecuteAsync_CallsProvider(t *testing.T) {
	called := make(chan bool, 1)
	mockProvider := &mockMirrorProvider{
		fn: func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
			called <- true
			return &models.ChatCompletionResponse{ID: "mirror-resp"}, nil
		},
	}

	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "target", TargetModel: "target-model", SampleRate: 1.0},
		},
	}
	lookup := func(id string) (MirrorProvider, bool) {
		if id == "target" {
			return mockProvider, true
		}
		return nil, false
	}
	m := NewMirror(cfg, lookup)

	req := &models.ChatCompletionRequest{Model: "gpt-4o"}
	m.ExecuteAsync(req, "openai", "gpt-4o", nil)

	select {
	case <-called:
		// Success — provider was called.
	case <-time.After(2 * time.Second):
		t.Fatal("mirror provider was not called within timeout")
	}
}

func TestMirror_SampleRateClamped(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: true,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "anthropic", SampleRate: 5.0}, // clamped to 1.0
		},
	}
	m := NewMirror(cfg, nil)

	if m.rules[0].SampleRate != 1.0 {
		t.Errorf("sample rate = %f, want 1.0 (clamped)", m.rules[0].SampleRate)
	}
}

func TestMirror_DisabledConfig(t *testing.T) {
	cfg := config.MirrorConfig{
		Enabled: false,
		Rules: []config.MirrorRule{
			{SourceModel: "gpt-4o", TargetProvider: "anthropic", SampleRate: 1.0},
		},
	}
	m := NewMirror(cfg, nil)

	if m != nil {
		t.Error("disabled config should return nil mirror")
	}
}

// mockMirrorProvider implements MirrorProvider for testing.
type mockMirrorProvider struct {
	fn func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error)
}

func (p *mockMirrorProvider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return p.fn(ctx, req)
}
