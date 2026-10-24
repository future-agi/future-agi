package tenant

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
)

var (
	adminSchemaPath = filepath.Join("..", "..", "..", "api_contracts", "gateway", "agentcc-admin.schema.json")
	goldenDir       = filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples")
	v1FixturePath   = filepath.Join("..", "..", "..", "futureagi", "agentcc", "tests", "fixtures", "gateway_org_config.full.json")
)

// parseViaContract decodes a pushed org config the way the admin PUT handler
// does (internal/server/admin_orgs.go): strictly into the generated contract,
// then into the runtime tenant config.
func parseViaContract(data []byte) (*OrgConfig, error) {
	var contract gatewayadmin.OrgConfig
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&contract); err != nil {
		return nil, err
	}
	body, err := json.Marshal(&contract)
	if err != nil {
		return nil, err
	}
	var out OrgConfig
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func mustParse(t *testing.T, data string) *OrgConfig {
	t.Helper()
	cfg, err := parseViaContract([]byte(data))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return cfg
}

func TestParseContractV2(t *testing.T) {
	cfg := mustParse(t, `{
		"meta": {"version": 7, "config_id": "cfg-1", "pushed_at": "2026-10-15T09:00:00Z"},
		"routing": {
			"agents": {"step_presets": {"verify": "careful"}, "price_ceiling": {"prompt_usd_per_mtok": 3}},
			"data_policy": {"data_collection": "deny", "allowed_regions": ["eu"]},
			"presets": {"careful": {"models": ["a", "b"], "provider": {"sort": "latency", "preferred_max_latency": {"p90": 4}}}},
			"rollout": {"version": "v8", "percent": 25, "sticky_by": "key", "routing": {"auto": {"preference": "latency"}}}
		},
		"managed_models": {
			"enabled": true,
			"upstream_keys": {"openrouter": {"api_key": "test-key", "key_hash": "kh_1"}},
			"spend_guard": {"max_usd_per_hour": 10}
		},
		"privacy": {"retention": {"request_log_days": 14}},
		"budgets": {"enabled": true, "teams": {"eng": {"limit": 5, "include_byok": false}}},
		"guardrails": {"tool_calls": {"enabled": true, "checks": ["pii-detection"]}}
	}`)

	if got := cfg.PolicyVersion(); got != "v7" {
		t.Fatalf("PolicyVersion = %q, want v7", got)
	}
	if cfg.Meta.ConfigID != "cfg-1" || cfg.Meta.PushedAt == "" {
		t.Fatalf("meta not mirrored: %#v", cfg.Meta)
	}
	r := cfg.Rollout()
	if r == nil || r.Version != "v8" || r.Percent != 25 || r.StickyBy != "key" {
		t.Fatalf("rollout = %#v", r)
	}
	if r.Routing == nil || r.Routing.Auto == nil || r.Routing.Auto.Preference != "latency" {
		t.Fatalf("rollout routing not mirrored: %#v", r.Routing)
	}
	if got := cfg.AgentsConfig(); got.StepPresets["verify"] != "careful" || *got.PriceCeiling.PromptUSDPerMTok != 3 {
		t.Fatalf("agents = %#v", got)
	}
	if got := cfg.DataPolicy(); got.DataCollection != "deny" || got.AllowedRegions[0] != "eu" {
		t.Fatalf("data policy = %#v", got)
	}
	preset := cfg.Routing.Presets["careful"]
	if preset.Provider.Sort.By != "latency" || *preset.Provider.PreferredMaxLatency.P90 != 4 {
		t.Fatalf("preset provider = %#v", preset.Provider)
	}
	m := cfg.ManagedPolicy()
	if key := cfg.ManagedModels.UpstreamKeys["openrouter"]; key == nil || key.KeyHash != "kh_1" {
		t.Fatalf("upstream keys not mirrored: %#v", cfg.ManagedModels.UpstreamKeys)
	}
	if !m.Enabled || *m.SpendGuard.MaxUSDPerHour != 10 || !m.AllowFree {
		t.Fatalf("managed policy = %#v", m)
	}
	if got := cfg.Retention(); *got.RequestLogDays != 14 || *got.AttemptDays != 14 {
		t.Fatalf("attempt_days must default to request_log_days: %#v", got)
	}
	if cfg.Budgets.Teams["eng"].IncludesBYOK() {
		t.Fatal("include_byok=false not honoured")
	}
	if got := cfg.ToolCallGuard(); !got.Enabled || got.Checks[0] != "pii-detection" || got.OnBlock != "error" {
		t.Fatalf("tool call guard = %#v", got)
	}

	t.Run("v1 config parses with v2 defaults", func(t *testing.T) {
		body, err := os.ReadFile(v1FixturePath)
		if err != nil {
			t.Fatalf("read v1 fixture: %v", err)
		}
		cfg, err := parseViaContract(body)
		if err != nil {
			t.Fatalf("v1 fixture rejected: %v", err)
		}
		if cfg.PolicyVersion() != "" || cfg.Rollout() != nil || cfg.MaxTotalAttempts() != 6 {
			t.Fatalf("v1 config grew v2 state: version=%q rollout=%v attempts=%d",
				cfg.PolicyVersion(), cfg.Rollout(), cfg.MaxTotalAttempts())
		}
	})

	t.Run("content mode is the most restrictive of content_mode and store_bodies", func(t *testing.T) {
		cases := []struct {
			privacy string
			want    string
		}{
			{`{}`, "full"},
			{`{"content_mode": "metadata"}`, "metadata"},
			{`{"retention": {"store_bodies": false}}`, "metadata"},
			{`{"content_mode": "full", "retention": {"store_bodies": false}}`, "metadata"},
			{`{"content_mode": "none", "retention": {"store_bodies": false}}`, "none"},
		}
		for _, tc := range cases {
			cfg := mustParse(t, `{"privacy": `+tc.privacy+`}`)
			if got := cfg.ContentMode(); got != tc.want {
				t.Errorf("privacy %s: ContentMode = %q, want %q", tc.privacy, got, tc.want)
			}
		}
	})

	t.Run("polymorphic provider values keep their shape", func(t *testing.T) {
		for _, raw := range []string{`"price"`, `{"by":"price","partition":"model"}`, `2.5`, `{"p50":1,"p99":3}`} {
			var v any
			if strings.HasPrefix(raw, `"`) || strings.Contains(raw, "by") {
				var s ProviderSort
				if err := json.Unmarshal([]byte(raw), &s); err != nil {
					t.Fatalf("decode sort %s: %v", raw, err)
				}
				v = s
			} else {
				var b PercentileBound
				if err := json.Unmarshal([]byte(raw), &b); err != nil {
					t.Fatalf("decode bound %s: %v", raw, err)
				}
				v = b
			}
			out, err := json.Marshal(v)
			if err != nil {
				t.Fatalf("encode %s: %v", raw, err)
			}
			if string(out) != raw {
				t.Errorf("round trip %s gave %s", raw, out)
			}
		}
	})
}

func TestContractV2UnknownKeyRejected(t *testing.T) {
	cases := map[string]string{
		"meta":                        `{"meta": {"version": 1, "source": "x"}}`,
		"managed_models":              `{"managed_models": {"enabled": true, "markup_percent": 0}}`,
		"managed upstream key":        `{"managed_models": {"upstream_keys": {"openrouter": {"api_key": "k", "key_hash": "h", "secret": "s"}}}}`,
		"spend guard":                 `{"managed_models": {"spend_guard": {"max_usd_per_day": 1}}}`,
		"routing.agents":              `{"routing": {"agents": {"bogus": true}}}`,
		"routing.data_policy":         `{"routing": {"data_policy": {"regions": ["eu"]}}}`,
		"nested rollout":              `{"routing": {"rollout": {"version": "v2", "percent": 5, "sticky_by": "session", "routing": {"rollout": {}}}}}`,
		"preset provider":             `{"routing": {"presets": {"p": {"provider": {"sorting": "price"}}}}}`,
		"preset provider sort object": `{"routing": {"presets": {"p": {"provider": {"sort": {"by": "price", "order": "asc"}}}}}}`,
		"percentile object":           `{"routing": {"presets": {"p": {"provider": {"preferred_max_latency": {"p95": 2}}}}}}`,
		"auto selector":               `{"routing": {"auto": {"selector": {"temperature": 0}}}}`,
		"auto step profile":           `{"routing": {"auto": {"step_profiles": {"plan": {"tier": "complex"}}}}}`,
		"privacy retention":           `{"privacy": {"retention": {"keep_forever": true}}}`,
		"guardrails tool_calls":       `{"guardrails": {"tool_calls": {"mode": "strict"}}}`,
		"budget level":                `{"budgets": {"keys": {"k": {"limit": 1, "byok": true}}}}`,
	}
	for name, payload := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := parseViaContract([]byte(payload)); err == nil {
				t.Fatalf("payload accepted: %s", payload)
			} else if !strings.Contains(err.Error(), "unknown field") {
				t.Fatalf("rejected for the wrong reason: %v", err)
			}
		})
	}
}

// schemaDefaults reads every `default` the admin schema declares, keyed
// "<Definition>.<field>".
func schemaDefaults(t *testing.T) map[string]any {
	t.Helper()
	body, err := os.ReadFile(adminSchemaPath)
	if err != nil {
		t.Fatalf("read schema: %v", err)
	}
	var schema struct {
		Defs map[string]struct {
			Properties map[string]map[string]any `json:"properties"`
		} `json:"$defs"`
	}
	if err := json.Unmarshal(body, &schema); err != nil {
		t.Fatalf("parse schema: %v", err)
	}
	out := map[string]any{}
	for def, d := range schema.Defs {
		for field, prop := range d.Properties {
			if v, ok := prop["default"]; ok {
				out[def+"."+field] = v
			}
		}
	}
	return out
}

// asJSONValue normalises a Go value to what encoding/json decodes it to.
func asJSONValue(t *testing.T, v any) any {
	t.Helper()
	body, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %#v: %v", v, err)
	}
	var out any
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return out
}

func TestAccessorDefaults(t *testing.T) {
	for name, cfg := range map[string]*OrgConfig{"nil config": nil, "empty config": {}} {
		t.Run(name, func(t *testing.T) {
			m := cfg.ManagedPolicy()
			a := cfg.AgentsConfig()
			d := cfg.DataPolicy()
			af := cfg.AutoAffinity()
			o := cfg.AutoOutcomes()
			s := cfg.AutoSelector()
			i := cfg.Idempotency()
			rb := cfg.RetryBudget()
			ret := cfg.Retention()
			tc := cfg.ToolCallGuard()
			var level *BudgetLevelConfig

			got := map[string]any{
				"ManagedModelsConfig.fallback_to_managed":         m.FallbackToManaged,
				"ManagedModelsConfig.allow_free":                  m.AllowFree,
				"ManagedModelsConfig.hedging":                     m.Hedging,
				"ManagedModelsConfig.allow_data_collection":       m.AllowDataCollection,
				"ManagedModelsConfig.max_call_cost_usd":           m.MaxCallCostUSD,
				"ManagedModelsConfig.default_max_tokens_estimate": m.DefaultMaxTokensEstimate,
				"ManagedSpendGuardConfig.auto_pause":              m.SpendGuard.AutoPause,
				"ManagedTopupConfig.min_usd":                      m.TopupMinUSD,
				"AgentsConfig.default_model":                      a.DefaultModel,
				"AgentsConfig.budget_scope_ttl_seconds":           a.BudgetScopeTTLSeconds,
				"AgentsConfig.always_emit_metadata":               a.AlwaysEmitMetadata,
				"AgentsConfig.unpriced_under_cap":                 a.UnpricedUnderCap,
				"AgentsConfig.framework_aliases":                  a.FrameworkAliases,
				"AgentsConfig.price_ceiling":                      a.PriceCeiling,
				"DataPolicyConfig.zdr":                            d.ZDR,
				"DataPolicyConfig.data_collection":                d.DataCollection,
				"AutoAffinityConfig.enabled":                      af.Enabled,
				"AutoAffinityConfig.ttl_seconds":                  af.TTLSeconds,
				"AutoOutcomeFeedbackConfig.enabled":               o.Enabled,
				"AutoOutcomeFeedbackConfig.window_seconds":        o.WindowSeconds,
				"AutoOutcomeFeedbackConfig.min_samples":           o.MinSamples,
				"AutoOutcomeFeedbackConfig.penalty_weight":        o.PenaltyWeight,
				"AutoRoutingConfig.deadline_latency_fraction":     cfg.AutoDeadlineLatencyFraction(),
				"AutoSelectorConfig.enabled":                      s.Enabled,
				"AutoSelectorConfig.mode":                         s.Mode,
				"AutoSelectorConfig.timeout_ms":                   s.TimeoutMs,
				"AutoSelectorConfig.max_cost_usd":                 s.MaxCostUSD,
				"AutoSelectorConfig.ambiguity_threshold":          s.AmbiguityThreshold,
				"AutoSelectorConfig.max_candidates":               s.MaxCandidates,
				"IdempotencyConfig.enabled":                       i.Enabled,
				"IdempotencyConfig.ttl_seconds":                   i.TTLSeconds,
				"IdempotencyConfig.accept_attempt_id":             i.AcceptAttemptID,
				"PromptCachingConfig.mode":                        cfg.PromptCachingMode(),
				"RoutingConfig.max_total_attempts":                cfg.MaxTotalAttempts(),
				"RolloutRoutingConfig.max_total_attempts":         cfg.MaxTotalAttempts(),
				"RetryBudgetConfig.max_retry_ratio":               rb.MaxRetryRatio,
				"RetryBudgetConfig.max_hedges_per_minute":         rb.MaxHedgesPerMinute,
				"FastestResponseConfig.allow_managed":             cfg.FastestAllowsManaged(),
				"BudgetLevelConfig.include_byok":                  level.IncludesBYOK(),
				"BudgetLevelConfig.alert_thresholds":              level.Thresholds(),
				"PrivacyConfig.content_mode":                      cfg.ContentMode(),
				"PrivacyRetentionConfig.request_log_days":         ret.RequestLogDays,
				"PrivacyRetentionConfig.attempt_days":             ret.AttemptDays,
				"PrivacyRetentionConfig.store_bodies":             ret.StoreBodies,
				"ToolCallGuardrailConfig.enabled":                 tc.Enabled,
				"ToolCallGuardrailConfig.on_block":                tc.OnBlock,
				"ToolCallGuardrailConfig.apply_to":                tc.ApplyTo,
				"ToolCallGuardrailConfig.max_args_bytes":          tc.MaxArgsBytes,
				"ToolCallGuardrailConfig.stop_run_on_block":       tc.StopRunOnBlock,
			}
			if cfg.PolicyVersion() != "" || cfg.Rollout() != nil {
				t.Fatalf("an unversioned config has version %q and rollout %v", cfg.PolicyVersion(), cfg.Rollout())
			}

			want := schemaDefaults(t)
			var keys []string
			for k := range want {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				g, ok := got[k]
				if !ok {
					t.Errorf("schema default %s has no accessor", k)
					continue
				}
				if gv := asJSONValue(t, g); !reflect.DeepEqual(gv, want[k]) {
					t.Errorf("%s: accessor gives %v, schema default is %v", k, gv, want[k])
				}
			}
			for k := range got {
				if _, ok := want[k]; !ok {
					t.Errorf("accessor %s maps to no schema default", k)
				}
			}
		})
	}
}
