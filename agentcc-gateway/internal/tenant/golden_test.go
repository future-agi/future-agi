package tenant

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

// TestGoldenOrgConfigV2 checks the org-config-v2 golden against the gateway:
// it decodes through the strict contract, survives the trip into the runtime
// config without losing a field, and every accessor returns the value it set.
func TestGoldenOrgConfigV2(t *testing.T) {
	body, err := os.ReadFile(filepath.Join(goldenDir, "org-config-v2.json"))
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	cfg, err := parseViaContract(body)
	if err != nil {
		t.Fatalf("golden rejected by the contract: %v", err)
	}

	t.Run("round trip keeps every field", func(t *testing.T) {
		var want any
		if err := json.Unmarshal(body, &want); err != nil {
			t.Fatalf("parse golden: %v", err)
		}
		got := asJSONValue(t, cfg)
		if diffs := jsonDiff("", want, got); len(diffs) > 0 {
			sort.Strings(diffs)
			t.Fatalf("tenant.OrgConfig does not mirror the golden:\n%v", diffs)
		}
	})

	t.Run("accessors read the golden values", func(t *testing.T) {
		checks := map[string][2]any{
			"PolicyVersion":            {cfg.PolicyVersion(), "v12"},
			"Rollout.Version":          {cfg.Rollout().Version, "v13"},
			"Rollout.Percent":          {cfg.Rollout().Percent, 10},
			"Rollout.MaxAttempts":      {*cfg.Rollout().Routing.MaxTotalAttempts, 3},
			"Rollout.ManagedAllowFree": {*cfg.Rollout().ManagedModels.AllowFree, false},
			"ManagedPolicy.AllowFree":  {cfg.ManagedPolicy().AllowFree, false},
			"ManagedPolicy.Topup":      {cfg.ManagedPolicy().TopupMinUSD, 50.0},
			"ManagedPolicy.Estimate":   {cfg.ManagedPolicy().DefaultMaxTokensEstimate, 2048},
			"ManagedPolicy.CallCap":    {*cfg.ManagedPolicy().MaxCallCostUSD, 0.5},
			"UpstreamKey.KeyHash":      {cfg.ManagedModels.UpstreamKeys["openrouter"].KeyHash, "kh_3b9e"},
			"Agents.DefaultModel":      {cfg.AgentsConfig().DefaultModel, "omega-default"},
			"Agents.AlwaysEmit":        {cfg.AgentsConfig().AlwaysEmitMetadata, false},
			"Agents.Unpriced":          {cfg.AgentsConfig().UnpricedUnderCap, "allow"},
			"DataPolicy.Collection":    {cfg.DataPolicy().DataCollection, "deny"},
			"Affinity.Enabled":         {cfg.AutoAffinity().Enabled, false},
			"Affinity.TTL":             {cfg.AutoAffinity().TTLSeconds, 600},
			"Outcomes.MinSamples":      {cfg.AutoOutcomes().MinSamples, 50},
			"Outcomes.Penalty":         {cfg.AutoOutcomes().PenaltyWeight, 0.5},
			"Deadline.Fraction":        {cfg.AutoDeadlineLatencyFraction(), 0.4},
			"Selector.Mode":            {cfg.AutoSelector().Mode, "enforce"},
			"Selector.MaxCandidates":   {cfg.AutoSelector().MaxCandidates, 5},
			"Idempotency.AcceptID":     {cfg.Idempotency().AcceptAttemptID, true},
			"Idempotency.TTL":          {cfg.Idempotency().TTLSeconds, 3600},
			"MaxTotalAttempts":         {cfg.MaxTotalAttempts(), 4},
			"RetryBudget.Ratio":        {cfg.RetryBudget().MaxRetryRatio, 0.1},
			"PromptCaching":            {cfg.PromptCachingMode(), "auto"},
			"FastestAllowsManaged":     {cfg.FastestAllowsManaged(), true},
			"ContentMode":              {cfg.ContentMode(), "none"},
			"Retention.AttemptDays":    {*cfg.Retention().AttemptDays, 90},
			"Retention.StoreBodies":    {cfg.Retention().StoreBodies, false},
			"ToolCalls.OnBlock":        {cfg.ToolCallGuard().OnBlock, "strip"},
			"ToolCalls.MaxArgsBytes":   {cfg.ToolCallGuard().MaxArgsBytes, 32768},
			"Budget.IncludesBYOK":      {cfg.Budgets.Keys["key_7f3a"].IncludesBYOK(), false},
			"RateLimit.MaxParallel":    {cfg.RateLimiting.PerKeyMaxParallel, 8},
			"Provider.ZDRAttested":     {cfg.Providers["openai"].ZDRAttested, true},
			"Preset.SortPartition":     {cfg.Routing.Presets["fast-act"].Provider.Sort.Partition, "model"},
			"Preset.MaxLatency":        {*cfg.Routing.Presets["fast-act"].Provider.PreferredMaxLatency.Value, 2.5},
			"Preset.MinThroughputP90":  {*cfg.Routing.Presets["fast-act"].Provider.PreferredMinThroughput.P90, 20.0},
			"Preset.CarefulSort":       {cfg.Routing.Presets["careful"].Provider.Sort.By, "throughput"},
		}
		for name, c := range checks {
			if !reflect.DeepEqual(c[0], c[1]) {
				t.Errorf("%s = %#v, want %#v", name, c[0], c[1])
			}
		}
		if got := cfg.Budgets.Keys["key_7f3a"].Thresholds(); !reflect.DeepEqual(got, []float64{0.8, 1}) {
			t.Errorf("alert thresholds = %v", got)
		}
	})
}

// jsonDiff lists the paths where two decoded JSON values differ.
func jsonDiff(path string, want, got any) []string {
	wm, wok := want.(map[string]any)
	gm, gok := got.(map[string]any)
	if wok && gok {
		var out []string
		for k, wv := range wm {
			out = append(out, jsonDiff(path+"."+k, wv, gm[k])...)
		}
		for k, gv := range gm {
			if _, ok := wm[k]; !ok {
				out = append(out, fmt.Sprintf("%s.%s: unexpected %v", path, k, gv))
			}
		}
		return out
	}
	if !reflect.DeepEqual(want, got) {
		return []string{fmt.Sprintf("%s: want %v, got %v", path, want, got)}
	}
	return nil
}
