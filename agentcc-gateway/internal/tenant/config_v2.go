package tenant

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
)

// This file holds the org-config contract v2 pieces that are more than plain
// wire fields: push-time metadata, the provider-preference values that come in
// more than one JSON shape, and accessors that apply the defaults declared in
// api_contracts/gateway/agentcc-admin.schema.json. Read v2 settings through
// these accessors, never through the raw pointers, so every reader agrees on
// the defaults. TestAccessorDefaults pins them to the schema.

// ConfigMeta is push-time metadata: which control-plane config version this is.
type ConfigMeta struct {
	Version  int    `json:"version"`
	ConfigID string `json:"config_id,omitempty"`
	PushedAt string `json:"pushed_at,omitempty"`
}

// PolicyVersion is "v<meta.version>", or "" when the config carries no version.
func (c *OrgConfig) PolicyVersion() string {
	if c == nil || c.Meta == nil || c.Meta.Version <= 0 {
		return ""
	}
	return "v" + strconv.Itoa(c.Meta.Version)
}

// Rollout returns the staged rollout, or nil when none is configured.
func (c *OrgConfig) Rollout() *RolloutConfig {
	if c == nil || c.Routing == nil {
		return nil
	}
	return c.Routing.Rollout
}

// ProviderSort is ProviderPrefs.sort: a bare key ("price") or {by, partition}.
type ProviderSort struct {
	By        string `json:"by"`
	Partition string `json:"partition,omitempty"`
}

// UnmarshalJSON accepts both the bare-key and the object form. The generated
// contract types this field as interface{}, so the object form is decoded
// strictly here.
func (s *ProviderSort) UnmarshalJSON(data []byte) error {
	if bytes.HasPrefix(bytes.TrimSpace(data), []byte(`"`)) {
		*s = ProviderSort{}
		return json.Unmarshal(data, &s.By)
	}
	type plain ProviderSort
	var out plain
	if err := decodeStrict(data, &out); err != nil {
		return fmt.Errorf("provider sort: %w", err)
	}
	*s = ProviderSort(out)
	return nil
}

// MarshalJSON writes the bare-key form when there is no partition.
func (s ProviderSort) MarshalJSON() ([]byte, error) {
	if s.Partition == "" {
		return json.Marshal(s.By)
	}
	type plain ProviderSort
	return json.Marshal(plain(s))
}

// PercentileBound is a latency or throughput preference: one number, or
// thresholds per percentile. Exactly one of Value and the percentiles is set.
type PercentileBound struct {
	Value *float64
	P50   *float64
	P75   *float64
	P90   *float64
	P99   *float64
}

type percentileThresholds struct {
	P50 *float64 `json:"p50,omitempty"`
	P75 *float64 `json:"p75,omitempty"`
	P90 *float64 `json:"p90,omitempty"`
	P99 *float64 `json:"p99,omitempty"`
}

// UnmarshalJSON accepts a number or a {p50, p75, p90, p99} object.
func (b *PercentileBound) UnmarshalJSON(data []byte) error {
	*b = PercentileBound{}
	if bytes.HasPrefix(bytes.TrimSpace(data), []byte(`{`)) {
		var t percentileThresholds
		if err := decodeStrict(data, &t); err != nil {
			return fmt.Errorf("percentile bound: %w", err)
		}
		b.P50, b.P75, b.P90, b.P99 = t.P50, t.P75, t.P90, t.P99
		return nil
	}
	var v float64
	if err := json.Unmarshal(data, &v); err != nil {
		return fmt.Errorf("percentile bound: %w", err)
	}
	b.Value = &v
	return nil
}

// MarshalJSON writes the shape the bound was read from.
func (b PercentileBound) MarshalJSON() ([]byte, error) {
	if b.Value != nil {
		return json.Marshal(*b.Value)
	}
	return json.Marshal(percentileThresholds{P50: b.P50, P75: b.P75, P90: b.P90, P99: b.P99})
}

// decodeStrict decodes an object and rejects keys the target does not declare.
func decodeStrict(data []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

func boolOr(p *bool, def bool) bool {
	if p == nil {
		return def
	}
	return *p
}

func intOr(p *int, def int) int {
	if p == nil {
		return def
	}
	return *p
}

func floatOr(p *float64, def float64) float64 {
	if p == nil {
		return def
	}
	return *p
}

func stringOr(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

// ManagedPolicy is managed_models with its defaults applied.
type ManagedPolicy struct {
	Enabled                  bool
	AllowModels              []string
	DenyModels               []string
	AllowVendors             []string
	DenyVendors              []string
	FallbackToManaged        bool
	AllowFree                bool
	Hedging                  bool
	AllowDataCollection      bool
	MaxCallCostUSD           *float64 // nil: no per-call cap
	DefaultMaxTokensEstimate int
	SpendGuard               ManagedSpendGuardConfig
	TopupMinUSD              float64
}

// ManagedPolicy returns the org's managed-model policy with defaults applied.
func (c *OrgConfig) ManagedPolicy() ManagedPolicy {
	var m ManagedModelsConfig
	if c != nil && c.ManagedModels != nil {
		m = *c.ManagedModels
	}
	p := ManagedPolicy{
		Enabled:                  m.Enabled,
		AllowModels:              m.AllowModels,
		DenyModels:               m.DenyModels,
		AllowVendors:             m.AllowVendors,
		DenyVendors:              m.DenyVendors,
		FallbackToManaged:        m.FallbackToManaged,
		AllowFree:                boolOr(m.AllowFree, true),
		Hedging:                  m.Hedging,
		AllowDataCollection:      m.AllowDataCollection,
		MaxCallCostUSD:           m.MaxCallCostUSD,
		DefaultMaxTokensEstimate: intOr(m.DefaultMaxTokensEstimate, 4096),
		TopupMinUSD:              25,
	}
	if m.SpendGuard != nil {
		p.SpendGuard = *m.SpendGuard
	}
	if m.Topup != nil {
		p.TopupMinUSD = floatOr(m.Topup.MinUSD, 25)
	}
	return p
}

func (c *OrgConfig) routing() *RoutingConfig {
	if c == nil || c.Routing == nil {
		return &RoutingConfig{}
	}
	return c.Routing
}

func (c *OrgConfig) auto() *AutoRoutingConfig {
	if r := c.routing(); r.Auto != nil {
		return r.Auto
	}
	return &AutoRoutingConfig{}
}

// AgentsSettings is routing.agents with its defaults applied.
type AgentsSettings struct {
	DefaultModel          string
	BudgetScopeTTLSeconds int
	StepPresets           map[string]string
	AlwaysEmitMetadata    bool
	UnpricedUnderCap      string // "reject" or "allow"
	FrameworkAliases      bool
	PriceCeiling          *AgentsPriceCeiling // nil: no ceiling
}

// AgentsConfig returns the org's agent policy with defaults applied.
func (c *OrgConfig) AgentsConfig() AgentsSettings {
	var a AgentsConfig
	if r := c.routing(); r.Agents != nil {
		a = *r.Agents
	}
	return AgentsSettings{
		DefaultModel:          stringOr(a.DefaultModel, "auto"),
		BudgetScopeTTLSeconds: intOr(a.BudgetScopeTTLSeconds, 86400),
		StepPresets:           a.StepPresets,
		AlwaysEmitMetadata:    boolOr(a.AlwaysEmitMetadata, true),
		UnpricedUnderCap:      stringOr(a.UnpricedUnderCap, "reject"),
		FrameworkAliases:      boolOr(a.FrameworkAliases, true),
		PriceCeiling:          a.PriceCeiling,
	}
}

// DataPolicy returns routing.data_policy with defaults applied. It is the org
// policy only: managed requests also deny data collection unless
// ManagedPolicy().AllowDataCollection is set.
func (c *OrgConfig) DataPolicy() DataPolicyConfig {
	var d DataPolicyConfig
	if r := c.routing(); r.DataPolicy != nil {
		d = *r.DataPolicy
	}
	d.DataCollection = stringOr(d.DataCollection, "allow")
	return d
}

// AffinitySettings is routing.auto.affinity with defaults applied.
type AffinitySettings struct {
	Enabled    bool
	TTLSeconds int
}

// AutoAffinity returns the session/model affinity settings.
func (c *OrgConfig) AutoAffinity() AffinitySettings {
	var a AutoAffinityConfig
	if cfg := c.auto().Affinity; cfg != nil {
		a = *cfg
	}
	return AffinitySettings{Enabled: boolOr(a.Enabled, true), TTLSeconds: intOr(a.TTLSeconds, 1800)}
}

// OutcomeSettings is routing.auto.outcome_feedback with defaults applied.
type OutcomeSettings struct {
	Enabled       bool
	WindowSeconds int
	MinSamples    int
	PenaltyWeight float64
}

// AutoOutcomes returns the outcome-feedback settings.
func (c *OrgConfig) AutoOutcomes() OutcomeSettings {
	var o AutoOutcomeFeedbackConfig
	if cfg := c.auto().OutcomeFeedback; cfg != nil {
		o = *cfg
	}
	return OutcomeSettings{
		Enabled:       boolOr(o.Enabled, true),
		WindowSeconds: intOr(o.WindowSeconds, 86400),
		MinSamples:    intOr(o.MinSamples, 20),
		PenaltyWeight: floatOr(o.PenaltyWeight, 0.3),
	}
}

// AutoDeadlineLatencyFraction is the share of a deadline auto may spend on latency.
func (c *OrgConfig) AutoDeadlineLatencyFraction() float64 {
	return floatOr(c.auto().DeadlineLatencyFraction, 0.5)
}

// SelectorSettings is routing.auto.selector with defaults applied.
type SelectorSettings struct {
	Enabled            bool
	Mode               string // "observe" or "enforce"
	Model              string
	TimeoutMs          int
	MaxCostUSD         float64
	DailyBudgetUSD     *float64 // nil: no daily budget
	AmbiguityThreshold float64
	MaxCandidates      int
}

// AutoSelector returns the LLM-selector settings.
func (c *OrgConfig) AutoSelector() SelectorSettings {
	var s AutoSelectorConfig
	if cfg := c.auto().Selector; cfg != nil {
		s = *cfg
	}
	return SelectorSettings{
		Enabled:            s.Enabled,
		Mode:               stringOr(s.Mode, "observe"),
		Model:              s.Model,
		TimeoutMs:          intOr(s.TimeoutMs, 800),
		MaxCostUSD:         floatOr(s.MaxCostUSD, 0.002),
		DailyBudgetUSD:     s.DailyBudgetUSD,
		AmbiguityThreshold: floatOr(s.AmbiguityThreshold, 0.05),
		MaxCandidates:      intOr(s.MaxCandidates, 8),
	}
}

// IdempotencySettings is routing.idempotency with defaults applied.
type IdempotencySettings struct {
	Enabled         bool
	TTLSeconds      int
	AcceptAttemptID bool
}

// Idempotency returns the Idempotency-Key settings.
func (c *OrgConfig) Idempotency() IdempotencySettings {
	var i IdempotencyConfig
	if r := c.routing(); r.Idempotency != nil {
		i = *r.Idempotency
	}
	return IdempotencySettings{
		Enabled:         boolOr(i.Enabled, true),
		TTLSeconds:      intOr(i.TTLSeconds, 86400),
		AcceptAttemptID: i.AcceptAttemptID,
	}
}

// MaxTotalAttempts caps the attempts one request may make.
func (c *OrgConfig) MaxTotalAttempts() int {
	return intOr(c.routing().MaxTotalAttempts, 6)
}

// RetryBudgetSettings is routing.retry_budget with defaults applied.
type RetryBudgetSettings struct {
	MaxRetryRatio      float64
	MaxHedgesPerMinute int
}

// RetryBudget returns the org's retry and hedge budget.
func (c *OrgConfig) RetryBudget() RetryBudgetSettings {
	var b RetryBudgetConfig
	if r := c.routing(); r.RetryBudget != nil {
		b = *r.RetryBudget
	}
	return RetryBudgetSettings{
		MaxRetryRatio:      floatOr(b.MaxRetryRatio, 0.2),
		MaxHedgesPerMinute: intOr(b.MaxHedgesPerMinute, 60),
	}
}

// PromptCachingMode is "off" or "auto".
func (c *OrgConfig) PromptCachingMode() string {
	if r := c.routing(); r.PromptCaching != nil {
		return stringOr(r.PromptCaching.Mode, "off")
	}
	return "off"
}

// FastestAllowsManaged reports whether fastest routing may hedge onto managed providers.
func (c *OrgConfig) FastestAllowsManaged() bool {
	f := c.routing().Fastest
	return f != nil && f.AllowManaged
}

// contentModeRank orders content modes from least to most restrictive.
var contentModeRank = map[string]int{"full": 0, "metadata": 1, "none": 2}

// MostRestrictiveContentMode returns the most restrictive of the given modes,
// ignoring empty and unknown values; "full" when none is set.
func MostRestrictiveContentMode(modes ...string) string {
	best := "full"
	for _, m := range modes {
		if r, ok := contentModeRank[m]; ok && r > contentModeRank[best] {
			best = m
		}
	}
	return best
}

// ContentMode is the org's privacy.content_mode. store_bodies=false reads as
// at least "metadata".
func (c *OrgConfig) ContentMode() string {
	if c == nil || c.Privacy == nil {
		return "full"
	}
	mode := c.Privacy.ContentMode
	if !c.Retention().StoreBodies {
		return MostRestrictiveContentMode(mode, "metadata")
	}
	return MostRestrictiveContentMode(mode)
}

// RetentionSettings is privacy.retention with defaults applied. A nil TTL
// keeps rows indefinitely.
type RetentionSettings struct {
	RequestLogDays *int
	AttemptDays    *int
	StoreBodies    bool
}

// Retention returns the org's retention TTLs. attempt_days defaults to request_log_days.
func (c *OrgConfig) Retention() RetentionSettings {
	var r PrivacyRetentionConfig
	if c != nil && c.Privacy != nil && c.Privacy.Retention != nil {
		r = *c.Privacy.Retention
	}
	attempts := r.AttemptDays
	if attempts == nil {
		attempts = r.RequestLogDays
	}
	return RetentionSettings{RequestLogDays: r.RequestLogDays, AttemptDays: attempts, StoreBodies: boolOr(r.StoreBodies, true)}
}

// ToolCallGuardSettings is guardrails.tool_calls with defaults applied.
type ToolCallGuardSettings struct {
	Enabled        bool
	Checks         []string
	OnBlock        string // "error" or "strip"
	ApplyTo        string // "all", "managed" or "byok"
	MaxArgsBytes   int
	StopRunOnBlock bool
}

// ToolCallGuard returns the tool-call guardrail settings.
func (c *OrgConfig) ToolCallGuard() ToolCallGuardSettings {
	var t ToolCallGuardrailConfig
	if c != nil && c.Guardrails != nil && c.Guardrails.ToolCalls != nil {
		t = *c.Guardrails.ToolCalls
	}
	return ToolCallGuardSettings{
		Enabled:        t.Enabled,
		Checks:         t.Checks,
		OnBlock:        stringOr(t.OnBlock, "error"),
		ApplyTo:        stringOr(t.ApplyTo, "all"),
		MaxArgsBytes:   intOr(t.MaxArgsBytes, 65536),
		StopRunOnBlock: t.StopRunOnBlock,
	}
}

// DefaultAlertThresholds are the budget alert fractions used when a level sets none.
var DefaultAlertThresholds = []float64{0.5, 0.75, 1.0}

// IncludesBYOK reports whether the budget counts BYOK spend (default true).
func (b *BudgetLevelConfig) IncludesBYOK() bool {
	return b == nil || boolOr(b.IncludeBYOK, true)
}

// Thresholds returns the level's alert fractions, or DefaultAlertThresholds.
func (b *BudgetLevelConfig) Thresholds() []float64 {
	if b == nil || len(b.AlertThresholds) == 0 {
		return append([]float64(nil), DefaultAlertThresholds...)
	}
	return b.AlertThresholds
}
