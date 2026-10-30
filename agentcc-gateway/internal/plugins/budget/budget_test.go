package budget

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"testing"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func boolPtr(b bool) *bool { return &b }

func testTracker() *budgetpkg.Tracker {
	return budgetpkg.NewTracker(config.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Org: &config.BudgetLevelConfig{
			Limit: 10000.00,
			PerModel: map[string]float64{
				"gpt-4o": 3000.00,
			},
		},
		Teams: map[string]config.BudgetLevelConfig{
			"engineering": {
				Limit: 5000.00,
			},
			"marketing": {
				Limit: 1000.00,
				Hard:  boolPtr(false),
			},
		},
		Users: map[string]config.BudgetLevelConfig{
			"alice": {Limit: 500.00, Period: "daily"},
			"bob":   {Limit: 200.00},
		},
		Keys: map[string]config.BudgetLevelConfig{
			"ci-key": {Limit: 100.00, Period: "daily"},
		},
	})
}

func testPricing() *PricingLookup {
	return NewPricingLookup(nil)
}

func makeRC(model, provider string, metadata map[string]string) *models.RequestContext {
	rc := &models.RequestContext{
		Model:    model,
		Provider: provider,
		Metadata: make(map[string]string),
	}
	for k, v := range metadata {
		rc.Metadata[k] = v
	}
	return rc
}

func TestPlugin_Disabled(t *testing.T) {
	p := New(testTracker(), testPricing(), false, nil)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("disabled plugin should continue")
	}
}

func TestPlugin_NilTracker(t *testing.T) {
	p := New(nil, testPricing(), true, nil)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("nil tracker should continue")
	}
}

func TestPlugin_UnderBudget(t *testing.T) {
	p := New(testTracker(), testPricing(), true, nil)
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "alice",
		"key_team":       "engineering",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("under budget should pass: %s", result.Error.Message)
	}
	if rc.Metadata["budget_remaining"] == "" {
		t.Error("budget_remaining should be set")
	}
}

func TestPlugin_HardCapBlocks(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	// Exhaust alice's daily budget.
	tracker.RecordSpend("", "alice", "", "gpt-4o", nil, 500.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "alice",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("should be blocked by budget")
	}
	if result.Error.Status != 429 {
		t.Errorf("status = %d, want 429", result.Error.Status)
	}
	if result.Error.Code != "budget_exceeded" {
		t.Errorf("code = %q", result.Error.Code)
	}
	if rc.Metadata["budget_blocked_by"] == "" {
		t.Error("budget_blocked_by should be set")
	}
}

func TestPlugin_SoftCapWarns(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	// Exhaust marketing's soft budget.
	tracker.RecordSpend("marketing", "", "", "gpt-4o", nil, 1000.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_team": "marketing",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("soft cap should not block: %s", result.Error.Message)
	}
	if rc.Metadata["budget_warning"] != "true" {
		t.Error("should have budget warning")
	}
}

func TestPlugin_TeamBudgetBlocks(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	tracker.RecordSpend("engineering", "", "", "gpt-3.5-turbo", nil, 5000.00)

	rc := makeRC("gpt-3.5-turbo", "openai", map[string]string{
		"key_team": "engineering",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("should be blocked by team budget")
	}
}

func TestPlugin_KeyBudgetBlocks(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	tracker.RecordSpend("", "", "ci-key", "gpt-4o", nil, 100.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_name": "ci-key",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("should be blocked by key budget")
	}
}

func TestPlugin_OrgPerModelBudgetBlocks(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	tracker.RecordSpend("", "", "", "gpt-4o", nil, 3000.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("should be blocked by org per-model budget")
	}
}

func TestPlugin_ProcessResponse_RecordsCost(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "bob",
		"key_team":       "engineering",
	})
	rc.Response = &models.ChatCompletionResponse{
		Usage: &models.Usage{
			PromptTokens:     1000,
			CompletionTokens: 500,
		},
	}

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("ProcessResponse should continue")
	}

	// Cost should be tracked: 1000 * 2.50/1M + 500 * 10.00/1M = 0.0025 + 0.005 = 0.0075
	total, _ := tracker.GetSpend("team", "engineering")
	if total < 0.007 || total > 0.008 {
		t.Errorf("team spend = %f, want ~0.0075", total)
	}
}

func TestPlugin_ProcessResponse_FromCostMetadata(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "alice",
		"cost":           "0.050000", // Pre-calculated by cost plugin
	})
	rc.Response = &models.ChatCompletionResponse{}

	p.ProcessResponse(context.Background(), rc)

	total, _ := tracker.GetSpend("user", "alice")
	if total < 0.049 || total > 0.051 {
		t.Errorf("user spend = %f, want ~0.05", total)
	}
}

func TestPlugin_ProcessResponse_NilResponse(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "alice",
	})
	// No response, no cost metadata.
	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("should continue with nil response")
	}
}

func TestPlugin_ProcessResponse_BudgetRemainingUpdated(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "alice",
		"cost":           "100.00",
	})
	rc.Response = &models.ChatCompletionResponse{}

	p.ProcessResponse(context.Background(), rc)

	if rc.Metadata["budget_remaining"] == "" {
		t.Error("budget_remaining should be set after ProcessResponse")
	}
}

func TestPlugin_NameAndPriority(t *testing.T) {
	p := New(testTracker(), testPricing(), true, nil)
	if p.Name() != "budget" {
		t.Errorf("name = %q", p.Name())
	}
	if p.Priority() != 40 {
		t.Errorf("priority = %d", p.Priority())
	}
}

func TestPlugin_ExtractTags(t *testing.T) {
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"tag:project":    "alpha",
		"tag:department": "research",
		"auth_key_owner": "alice",
	})
	tags := extractTags(rc)
	if tags["project"] != "alpha" {
		t.Errorf("project tag = %q", tags["project"])
	}
	if tags["department"] != "research" {
		t.Errorf("department tag = %q", tags["department"])
	}
	if _, ok := tags["auth_key_owner"]; ok {
		t.Error("non-tag metadata should not be in tags")
	}
}

func TestPlugin_RbacTeamFallback(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)

	// Use rbac_team instead of key_team.
	tracker.RecordSpend("engineering", "", "", "gpt-4o", nil, 5000.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"rbac_team": "engineering",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("should be blocked via rbac_team fallback")
	}
}

// --- Per-org hierarchical budget tests ---

func testTenantStore(orgID string, budgets *tenant.BudgetsConfig) *tenant.Store {
	store := tenant.NewStore()
	store.Set(orgID, &tenant.OrgConfig{
		Budgets: budgets,
	})
	return store
}

func TestPerOrg_OrgLevelBlocks(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		OrgLimit:      100.0,
		HardLimit:     true,
	})
	p := New(nil, testPricing(), false, store)

	// Record spend to exhaust org budget.
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"cost":       "100.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	// Next request should be blocked.
	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error == nil {
		t.Fatal("should be blocked by per-org budget")
	}
	if result.Error.Code != "org_budget_exceeded" {
		t.Errorf("code = %q, want org_budget_exceeded", result.Error.Code)
	}
}

func TestPerOrg_TeamLevelBlocks(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Teams: map[string]*tenant.BudgetLevelConfig{
			"engineering": {Limit: 50.0},
		},
	})
	p := New(nil, testPricing(), false, store)

	// Record team spend.
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":     "org-1",
		"key_team":       "engineering",
		"cost":           "50.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	// Next request for same team should be blocked.
	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"key_team":   "engineering",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error == nil {
		t.Fatal("should be blocked by per-org team budget")
	}
	if result.Error.Code != "org_budget_exceeded" {
		t.Errorf("code = %q", result.Error.Code)
	}
}

func TestPerOrg_UserLevelBlocks(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "daily",
		WarnThreshold: 0.8,
		Users: map[string]*tenant.BudgetLevelConfig{
			"alice": {Limit: 25.0, Period: "daily"},
		},
	})
	p := New(nil, testPricing(), false, store)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":     "org-1",
		"auth_key_owner": "alice",
		"cost":           "25.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":     "org-1",
		"auth_key_owner": "alice",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error == nil {
		t.Fatal("should be blocked by per-org user budget")
	}
}

func TestPerOrg_KeyLevelBlocks(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Keys: map[string]*tenant.BudgetLevelConfig{
			"ci-key": {Limit: 10.0, Period: "daily"},
		},
	})
	p := New(nil, testPricing(), false, store)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":    "org-1",
		"auth_key_name": "ci-key",
		"cost":          "10.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":    "org-1",
		"auth_key_name": "ci-key",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error == nil {
		t.Fatal("should be blocked by per-org key budget")
	}
}

func TestPerOrg_TagLevelBlocks(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Tags: map[string]*tenant.BudgetLevelConfig{
			"project:alpha": {Limit: 20.0},
		},
	})
	p := New(nil, testPricing(), false, store)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":   "org-1",
		"tag:project":  "alpha",
		"cost":         "20.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":  "org-1",
		"tag:project": "alpha",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error == nil {
		t.Fatal("should be blocked by per-org tag budget")
	}
}

func TestPerOrg_SoftCapWarns(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Teams: map[string]*tenant.BudgetLevelConfig{
			"marketing": {Limit: 100.0, Hard: boolPtr(false)},
		},
	})
	p := New(nil, testPricing(), false, store)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"key_team":   "marketing",
		"cost":       "100.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"key_team":   "marketing",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error != nil {
		t.Fatalf("soft cap should not block: %s", result.Error.Message)
	}
	if rc2.Metadata["org_budget_warning"] != "true" {
		t.Error("should have org_budget_warning")
	}
}

func TestPerOrg_PerModelBlocks(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Teams: map[string]*tenant.BudgetLevelConfig{
			"engineering": {
				Limit: 1000.0,
				PerModel: map[string]float64{
					"gpt-4o": 50.0,
				},
			},
		},
	})
	p := New(nil, testPricing(), false, store)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"key_team":   "engineering",
		"cost":       "50.00",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	rc2 := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"key_team":   "engineering",
	})
	result := p.ProcessRequest(context.Background(), rc2)
	if result.Error == nil {
		t.Fatal("should be blocked by per-org per-model budget")
	}
}

func TestPerOrg_UnknownTeamPasses(t *testing.T) {
	store := testTenantStore("org-1", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Teams: map[string]*tenant.BudgetLevelConfig{
			"engineering": {Limit: 50.0},
		},
	})
	p := New(nil, testPricing(), false, store)

	// Unknown team should not be budget-checked.
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id": "org-1",
		"key_team":   "sales",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("unknown team should not be blocked: %s", result.Error.Message)
	}
}

// ---------------------------------------------------------------------------
// Unpriced models (#3445): a budget that cannot price a request must say so
// ---------------------------------------------------------------------------

func rcWithUsage(model string, prompt, completion int) *models.RequestContext {
	rc := makeRC(model, "anthropic", nil)
	rc.Response = &models.ChatCompletionResponse{
		Usage: &models.Usage{PromptTokens: prompt, CompletionTokens: completion, TotalTokens: prompt + completion},
	}
	return rc
}

func captureSlog(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

func TestProcessResponse_UnpricedModel_WarnsOncePerModel(t *testing.T) {
	buf := captureSlog(t)
	p := New(testTracker(), testPricing(), true, nil)

	for i := 0; i < 5; i++ {
		p.ProcessResponse(context.Background(), rcWithUsage("glm-5.3-flash", 1000, 500))
	}
	p.ProcessResponse(context.Background(), rcWithUsage("another-unpriced-model", 10, 10))
	p.ProcessResponse(context.Background(), rcWithUsage("gpt-4o", 1000, 500)) // priced: no warning

	out := buf.String()
	if n := strings.Count(out, "model=glm-5.3-flash"); n != 1 {
		t.Errorf("warned %d times for glm-5.3-flash, want exactly 1\n%s", n, out)
	}
	if n := strings.Count(out, "model=another-unpriced-model"); n != 1 {
		t.Errorf("warned %d times for another-unpriced-model, want exactly 1\n%s", n, out)
	}
	if strings.Contains(out, "model=gpt-4o") {
		t.Errorf("priced model must not warn\n%s", out)
	}
	if !strings.Contains(out, "cost_tracking.custom_pricing") {
		t.Errorf("warning should tell the operator how to fix it\n%s", out)
	}
}

func TestProcessResponse_UnpricedModel_NoWarningWhenNothingToPrice(t *testing.T) {
	buf := captureSlog(t)

	// Zero tokens: nothing was consumed, so nothing went unmetered.
	New(testTracker(), testPricing(), true, nil).
		ProcessResponse(context.Background(), rcWithUsage("glm-5.3-flash", 0, 0))
	// Budgets off: a missing price costs the operator nothing.
	New(testTracker(), testPricing(), false, nil).
		ProcessResponse(context.Background(), rcWithUsage("glm-5.3-flash", 1000, 500))

	if out := buf.String(); strings.Contains(out, "no pricing for model") {
		t.Fatalf("unexpected warning:\n%s", out)
	}
}

func TestProcessResponse_CustomPricedModel_NoWarningAndMetered(t *testing.T) {
	buf := captureSlog(t)
	tracker := testTracker()
	pricing := NewPricingLookup(map[string]config.CustomPricing{
		"glm-5.3-flash": {InputPerMTok: 1.00, OutputPerMTok: 2.00},
	})
	p := New(tracker, pricing, true, nil)

	p.ProcessResponse(context.Background(), rcWithUsage("glm-5.3-flash", 1000, 500))

	if out := buf.String(); strings.Contains(out, "no pricing for model") {
		t.Fatalf("a model with custom_pricing must not warn:\n%s", out)
	}
	// 1000*$1/1M + 500*$2/1M = $0.002
	if total, _ := tracker.GetSpend("org", ""); total < 0.0019 || total > 0.0021 {
		t.Fatalf("org spend = %v, want ~0.002", total)
	}
}

// The budget table and the model database are separate: an operator who sets
// only the budget's price still logs the request without a cost, so the hint
// has to name both keys.
func TestProcessResponse_UnpricedModel_HintNamesBothPricingKeys(t *testing.T) {
	buf := captureSlog(t)
	p := New(testTracker(), testPricing(), true, nil)

	p.ProcessResponse(context.Background(), rcWithUsage("glm-5.3-flash", 1000, 500))

	out := buf.String()
	for _, key := range []string{"cost_tracking.custom_pricing", "model_database.overrides"} {
		if !strings.Contains(out, key) {
			t.Errorf("hint should name %s\n%s", key, out)
		}
	}
}

// A call that cannot spend, such as a delete, is not refused: a key out of
// budget can still clean up what it pays to keep.
func TestPlugin_NoSpendCallIsNotRefused(t *testing.T) {
	tracker := testTracker()
	p := New(tracker, testPricing(), true, nil)
	tracker.RecordSpend("", "alice", "", "gpt-4o", nil, 500.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{"auth_key_owner": "alice"})
	if result := p.ProcessRequest(context.Background(), rc); result.Error == nil {
		t.Fatal("control: a call that can spend should be refused")
	}
	rc = makeRC("gpt-4o", "openai", map[string]string{"auth_key_owner": "alice"})
	rc.Flags.NoSpend = true
	if result := p.ProcessRequest(context.Background(), rc); result.Error != nil {
		t.Fatalf("a call that cannot spend was refused: %v", result.Error.Message)
	}
}
