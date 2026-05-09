package budget

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"sync"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// RedisBudgetBackend is an optional interface for Redis-backed budget counters.
// When set, the plugin uses Redis as the primary store and falls back to
// local in-memory counters when Redis is unavailable.
type RedisBudgetBackend interface {
	RecordSpend(org, level, key, period, model string, cost float64) (float64, bool)
	GetSpend(org, level, key, period string) (float64, map[string]float64, bool)
	Available() bool
}

// Plugin enforces hierarchical spend budgets as a pipeline plugin.
// Priority 90: runs after auth (100) and RBAC (95).
type Plugin struct {
	tracker      *budgetpkg.Tracker
	pricing      *PricingLookup
	enabled      bool
	tenantStore  *tenant.Store
	redisBudget  RedisBudgetBackend // nil = local-only
	orgCounters  sync.Map           // orgID -> *orgHierarchyCounters
}

// SetRedisBudget attaches a Redis-backed budget store for multi-replica support.
func (p *Plugin) SetRedisBudget(rb RedisBudgetBackend) {
	p.redisBudget = rb
}

// PricingLookup provides model pricing for independent cost calculation.
type PricingLookup struct {
	pricing map[string]modelPricing
}

type modelPricing struct {
	inputPerMTok  float64
	outputPerMTok float64
}

// NewPricingLookup creates a pricing lookup from custom pricing config.
func NewPricingLookup(custom map[string]config.CustomPricing) *PricingLookup {
	p := &PricingLookup{pricing: defaultPricing()}
	for model, cp := range custom {
		p.pricing[model] = modelPricing{
			inputPerMTok:  cp.InputPerMTok,
			outputPerMTok: cp.OutputPerMTok,
		}
	}
	return p
}

func (p *PricingLookup) calculateCost(model string, promptTokens, completionTokens int) (float64, bool) {
	mp, ok := p.pricing[model]
	if !ok {
		return 0, false
	}
	pt := promptTokens
	if pt < 0 {
		pt = 0
	}
	ct := completionTokens
	if ct < 0 {
		ct = 0
	}
	cost := float64(pt)*mp.inputPerMTok/1_000_000 + float64(ct)*mp.outputPerMTok/1_000_000
	return cost, true
}

// New creates a new budget plugin.
func New(tracker *budgetpkg.Tracker, pricing *PricingLookup, enabled bool, tenantStore *tenant.Store) *Plugin {
	return &Plugin{
		tracker:     tracker,
		pricing:     pricing,
		enabled:     enabled,
		tenantStore: tenantStore,
	}
}

func (p *Plugin) Name() string  { return "budget" }
func (p *Plugin) Priority() int { return 40 } // After auth (20) + RBAC (30), before guardrails.

// orgBudgetCounter tracks spend within a period for a single budget level.
type orgBudgetCounter struct {
	mu          sync.Mutex
	totalSpend  float64
	modelSpend  map[string]float64
	periodStart time.Time
}

// orgHierarchyCounters holds per-level counters for a single org.
type orgHierarchyCounters struct {
	mu    sync.Mutex // protects map creation only
	org   *orgBudgetCounter
	teams map[string]*orgBudgetCounter
	users map[string]*orgBudgetCounter
	keys  map[string]*orgBudgetCounter
	tags  map[string]*orgBudgetCounter
}

func newOrgHierarchyCounters() *orgHierarchyCounters {
	return &orgHierarchyCounters{
		teams: make(map[string]*orgBudgetCounter),
		users: make(map[string]*orgBudgetCounter),
		keys:  make(map[string]*orgBudgetCounter),
		tags:  make(map[string]*orgBudgetCounter),
	}
}

func (h *orgHierarchyCounters) getOrCreateCounter(level, key string) *orgBudgetCounter {
	h.mu.Lock()
	defer h.mu.Unlock()

	switch level {
	case "org":
		if h.org == nil {
			h.org = &orgBudgetCounter{modelSpend: make(map[string]float64)}
		}
		return h.org
	case "team":
		if _, ok := h.teams[key]; !ok {
			h.teams[key] = &orgBudgetCounter{modelSpend: make(map[string]float64)}
		}
		return h.teams[key]
	case "user":
		if _, ok := h.users[key]; !ok {
			h.users[key] = &orgBudgetCounter{modelSpend: make(map[string]float64)}
		}
		return h.users[key]
	case "key":
		if _, ok := h.keys[key]; !ok {
			h.keys[key] = &orgBudgetCounter{modelSpend: make(map[string]float64)}
		}
		return h.keys[key]
	case "tag":
		if _, ok := h.tags[key]; !ok {
			h.tags[key] = &orgBudgetCounter{modelSpend: make(map[string]float64)}
		}
		return h.tags[key]
	}
	return nil
}

// ProcessRequest checks all applicable budgets before the provider call.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// Global budget check.
	if p.enabled && p.tracker != nil {
		team, user, keyName, model := extractIdentity(rc)
		tags := extractTags(rc)

		result := p.tracker.CheckBudget(team, user, keyName, model, tags)

		if result.Remaining >= 0 {
			rc.Metadata["budget_remaining"] = fmt.Sprintf("%.2f", result.Remaining)
		}
		if len(result.Warnings) > 0 {
			rc.Metadata["budget_warning"] = "true"
		}

		if !result.Allowed {
			rc.Metadata["budget_blocked_by"] = result.BlockedBy
			return pipeline.ResultError(&models.APIError{
				Status:  http.StatusTooManyRequests,
				Type:    models.ErrTypeRateLimit,
				Code:    "budget_exceeded",
				Message: result.BlockMsg,
			})
		}
	}

	// Per-org hierarchical budget check.
	if p.tenantStore != nil {
		orgID := rc.Metadata[tenant.MetadataKeyOrgID]
		if orgID != "" {
			orgCfg := p.tenantStore.Get(orgID)
			if orgCfg != nil && orgCfg.Budgets != nil && orgCfg.Budgets.Enabled {
				if err := p.checkOrgHierarchicalBudgets(orgCfg.Budgets, orgID, rc); err != nil {
					return pipeline.ResultError(err)
				}
			}
		}
	}

	return pipeline.ResultContinue()
}

// checkOrgHierarchicalBudgets checks all per-org budget levels and returns an error if blocked.
func (p *Plugin) checkOrgHierarchicalBudgets(budgets *tenant.BudgetsConfig, orgID string, rc *models.RequestContext) *models.APIError {
	hc := p.getOrgHierarchy(orgID)
	team, user, keyName, model := extractIdentity(rc)
	tags := extractTags(rc)
	now := time.Now().UTC()

	defaultPeriod := budgets.DefaultPeriod
	if defaultPeriod == "" {
		defaultPeriod = "monthly"
	}
	warnThreshold := budgets.WarnThreshold
	if warnThreshold <= 0 || warnThreshold >= 1 {
		warnThreshold = 0.8
	}

	lowestRemaining := -1.0

	// Check org-level budget (backward compat: use OrgLimit if set).
	if budgets.OrgLimit > 0 {
		orgPeriod := budgets.OrgPeriod
		if orgPeriod == "" {
			orgPeriod = defaultPeriod
		}
		bc := hc.getOrCreateCounter("org", "")
		spend, _ := p.getCounterSpendWithRedis(bc, orgID, "org", "", orgPeriod, now)
		remaining := budgets.OrgLimit - spend
		if remaining < 0 {
			remaining = 0
		}
		if lowestRemaining < 0 || remaining < lowestRemaining {
			lowestRemaining = remaining
		}
		rc.Metadata["org_budget_remaining"] = fmt.Sprintf("%.2f", remaining)
		if spend >= budgets.OrgLimit*warnThreshold {
			rc.Metadata["org_budget_warning"] = "true"
		}
		if spend >= budgets.OrgLimit && budgets.HardLimit {
			rc.Metadata["org_budget_blocked"] = "true"
			return &models.APIError{
				Status:  http.StatusTooManyRequests,
				Type:    models.ErrTypeRateLimit,
				Code:    "org_budget_exceeded",
				Message: fmt.Sprintf("org budget exceeded: spent $%.2f of $%.2f %s limit", spend, budgets.OrgLimit, orgPeriod),
			}
		}
	}

	// Check team budget.
	if team != "" && budgets.Teams != nil {
		if lc, ok := budgets.Teams[team]; ok && lc != nil && lc.Limit > 0 {
			if err := p.checkLevelBudget(hc, orgID, "team", team, model, lc, defaultPeriod, warnThreshold, now, rc, &lowestRemaining); err != nil {
				return err
			}
		}
	}

	// Check user budget.
	if user != "" && budgets.Users != nil {
		if lc, ok := budgets.Users[user]; ok && lc != nil && lc.Limit > 0 {
			if err := p.checkLevelBudget(hc, orgID, "user", user, model, lc, defaultPeriod, warnThreshold, now, rc, &lowestRemaining); err != nil {
				return err
			}
		}
	}

	// Check key budget.
	if keyName != "" && budgets.Keys != nil {
		if lc, ok := budgets.Keys[keyName]; ok && lc != nil && lc.Limit > 0 {
			if err := p.checkLevelBudget(hc, orgID, "key", keyName, model, lc, defaultPeriod, warnThreshold, now, rc, &lowestRemaining); err != nil {
				return err
			}
		}
	}

	// Check tag budgets.
	if budgets.Tags != nil {
		for k, v := range tags {
			tagKey := k + ":" + v
			if lc, ok := budgets.Tags[tagKey]; ok && lc != nil && lc.Limit > 0 {
				if err := p.checkLevelBudget(hc, orgID, "tag", tagKey, model, lc, defaultPeriod, warnThreshold, now, rc, &lowestRemaining); err != nil {
					return err
				}
			}
		}
	}

	if lowestRemaining >= 0 {
		rc.Metadata["org_budget_remaining"] = fmt.Sprintf("%.2f", lowestRemaining)
	}

	return nil
}

// checkLevelBudget checks a single hierarchical budget level, including per-model sub-budgets.
func (p *Plugin) checkLevelBudget(hc *orgHierarchyCounters, orgID, level, key, model string, lc *tenant.BudgetLevelConfig, defaultPeriod string, warnThreshold float64, now time.Time, rc *models.RequestContext, lowestRemaining *float64) *models.APIError {
	period := lc.Period
	if period == "" {
		period = defaultPeriod
	}
	hard := true
	if lc.Hard != nil {
		hard = *lc.Hard
	}

	bc := hc.getOrCreateCounter(level, key)
	spend, modelSpend := p.getCounterSpendWithRedis(bc, orgID, level, key, period, now)

	remaining := lc.Limit - spend
	if remaining < 0 {
		remaining = 0
	}
	if *lowestRemaining < 0 || remaining < *lowestRemaining {
		*lowestRemaining = remaining
	}

	if spend >= lc.Limit*warnThreshold {
		rc.Metadata["org_budget_warning"] = "true"
	}

	if spend >= lc.Limit && hard {
		rc.Metadata["org_budget_blocked"] = "true"
		return &models.APIError{
			Status:  http.StatusTooManyRequests,
			Type:    models.ErrTypeRateLimit,
			Code:    "org_budget_exceeded",
			Message: fmt.Sprintf("%s '%s' budget exceeded: spent $%.2f of $%.2f %s limit", level, key, spend, lc.Limit, period),
		}
	}

	// Check per-model limits.
	if model != "" && lc.PerModel != nil {
		if modelLimit, ok := lc.PerModel[model]; ok && modelLimit > 0 {
			ms := modelSpend[model]
			mr := modelLimit - ms
			if mr < 0 {
				mr = 0
			}
			if *lowestRemaining < 0 || mr < *lowestRemaining {
				*lowestRemaining = mr
			}
			if ms >= modelLimit*warnThreshold {
				rc.Metadata["org_budget_warning"] = "true"
			}
			if ms >= modelLimit && hard {
				rc.Metadata["org_budget_blocked"] = "true"
				return &models.APIError{
					Status:  http.StatusTooManyRequests,
					Type:    models.ErrTypeRateLimit,
					Code:    "org_budget_exceeded",
					Message: fmt.Sprintf("%s '%s' per-model budget exceeded: model '%s' spent $%.2f of $%.2f %s limit", level, key, model, ms, modelLimit, period),
				}
			}
		}
	}

	return nil
}

// getCounterSpend returns current spend for a counter, resetting if period expired.
func (p *Plugin) getCounterSpend(bc *orgBudgetCounter, period string, now time.Time) (float64, map[string]float64) {
	bc.mu.Lock()
	defer bc.mu.Unlock()

	start := budgetpkg.PeriodStart(period, now)
	if start.After(bc.periodStart) {
		bc.totalSpend = 0
		bc.modelSpend = make(map[string]float64)
		bc.periodStart = start
	}

	ms := make(map[string]float64, len(bc.modelSpend))
	for m, s := range bc.modelSpend {
		ms[m] = s
	}
	return bc.totalSpend, ms
}

// ProcessResponse records actual spend against all applicable budgets.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	cost := p.calculateCost(rc)

	// Global budget tracking.
	if p.enabled && p.tracker != nil {
		if cost > 0 {
			team, user, keyName, model := extractIdentity(rc)
			tags := extractTags(rc)

			p.tracker.RecordSpend(team, user, keyName, model, tags, cost)

			result := p.tracker.CheckBudget(team, user, keyName, model, tags)
			if result.Remaining >= 0 {
				rc.Metadata["budget_remaining"] = fmt.Sprintf("%.2f", result.Remaining)
			}
			if len(result.Warnings) > 0 {
				rc.Metadata["budget_warning"] = "true"
			}
		}
	}

	// Per-org hierarchical spend recording.
	if cost > 0 && p.tenantStore != nil {
		orgID := rc.Metadata[tenant.MetadataKeyOrgID]
		if orgID != "" {
			orgCfg := p.tenantStore.Get(orgID)
			if orgCfg != nil && orgCfg.Budgets != nil && orgCfg.Budgets.Enabled {
				p.recordOrgHierarchicalSpend(orgCfg.Budgets, orgID, rc, cost)
			}
		}
	}

	return pipeline.ResultContinue()
}

// recordOrgHierarchicalSpend records cost against all applicable per-org hierarchy levels.
func (p *Plugin) recordOrgHierarchicalSpend(budgets *tenant.BudgetsConfig, orgID string, rc *models.RequestContext, cost float64) {
	hc := p.getOrgHierarchy(orgID)
	team, user, keyName, model := extractIdentity(rc)
	tags := extractTags(rc)
	now := time.Now().UTC()

	defaultPeriod := budgets.DefaultPeriod
	if defaultPeriod == "" {
		defaultPeriod = "monthly"
	}

	// Record against org level.
	if budgets.OrgLimit > 0 {
		orgPeriod := budgets.OrgPeriod
		if orgPeriod == "" {
			orgPeriod = defaultPeriod
		}
		p.recordToCounterWithRedis(hc.getOrCreateCounter("org", ""), orgID, "org", "", model, cost, orgPeriod, now)
		bc := hc.getOrCreateCounter("org", "")
		bc.mu.Lock()
		remaining := budgets.OrgLimit - bc.totalSpend
		bc.mu.Unlock()
		if remaining < 0 {
			remaining = 0
		}
		rc.Metadata["org_budget_remaining"] = fmt.Sprintf("%.2f", remaining)
	}

	// Record against team level.
	if team != "" && budgets.Teams != nil {
		if lc, ok := budgets.Teams[team]; ok && lc != nil {
			period := lc.Period
			if period == "" {
				period = defaultPeriod
			}
			p.recordToCounterWithRedis(hc.getOrCreateCounter("team", team), orgID, "team", team, model, cost, period, now)
		}
	}

	// Record against user level.
	if user != "" && budgets.Users != nil {
		if lc, ok := budgets.Users[user]; ok && lc != nil {
			period := lc.Period
			if period == "" {
				period = defaultPeriod
			}
			p.recordToCounterWithRedis(hc.getOrCreateCounter("user", user), orgID, "user", user, model, cost, period, now)
		}
	}

	// Record against key level.
	if keyName != "" && budgets.Keys != nil {
		if lc, ok := budgets.Keys[keyName]; ok && lc != nil {
			period := lc.Period
			if period == "" {
				period = defaultPeriod
			}
			p.recordToCounterWithRedis(hc.getOrCreateCounter("key", keyName), orgID, "key", keyName, model, cost, period, now)
		}
	}

	// Record against tag levels.
	if budgets.Tags != nil {
		for k, v := range tags {
			tagKey := k + ":" + v
			if lc, ok := budgets.Tags[tagKey]; ok && lc != nil {
				period := lc.Period
				if period == "" {
					period = defaultPeriod
				}
				p.recordToCounterWithRedis(hc.getOrCreateCounter("tag", tagKey), orgID, "tag", tagKey, model, cost, period, now)
			}
		}
	}
}

// recordToCounter adds cost to a counter, resetting if period expired.
// When a Redis backend is available, it writes through to Redis as well.
func (p *Plugin) recordToCounter(bc *orgBudgetCounter, model string, cost float64, period string, now time.Time) {
	if bc == nil {
		return
	}
	bc.mu.Lock()
	defer bc.mu.Unlock()

	start := budgetpkg.PeriodStart(period, now)
	if start.After(bc.periodStart) {
		bc.totalSpend = 0
		bc.modelSpend = make(map[string]float64)
		bc.periodStart = start
	}
	bc.totalSpend += cost
	if model != "" {
		bc.modelSpend[model] += cost
	}
}

// recordToCounterWithRedis writes spend to both local and Redis.
func (p *Plugin) recordToCounterWithRedis(bc *orgBudgetCounter, orgID, level, key, model string, cost float64, period string, now time.Time) {
	// Always write to local counter.
	p.recordToCounter(bc, model, cost, period, now)

	// Write-through to Redis if available.
	if p.redisBudget != nil {
		p.redisBudget.RecordSpend(orgID, level, key, period, model, cost)
	}
}

// getCounterSpendWithRedis tries Redis first, falls back to local counter.
func (p *Plugin) getCounterSpendWithRedis(bc *orgBudgetCounter, orgID, level, key, period string, now time.Time) (float64, map[string]float64) {
	if p.redisBudget != nil && p.redisBudget.Available() {
		total, modelSpend, ok := p.redisBudget.GetSpend(orgID, level, key, period)
		if ok {
			return total, modelSpend
		}
	}
	// Fall back to local counter.
	return p.getCounterSpend(bc, period, now)
}

// InvalidateOrg removes cached budget counters for an org. Called when
// the org's config changes (e.g., budget period or limits updated).
func (p *Plugin) InvalidateOrg(orgID string) {
	p.orgCounters.Delete(orgID)
}

func (p *Plugin) getOrgHierarchy(orgID string) *orgHierarchyCounters {
	if v, ok := p.orgCounters.Load(orgID); ok {
		return v.(*orgHierarchyCounters)
	}
	hc := newOrgHierarchyCounters()
	actual, _ := p.orgCounters.LoadOrStore(orgID, hc)
	return actual.(*orgHierarchyCounters)
}

func (p *Plugin) calculateCost(rc *models.RequestContext) float64 {
	// Try reading from metadata first (if cost plugin already ran).
	if costStr := rc.Metadata["cost"]; costStr != "" {
		if cost, err := strconv.ParseFloat(costStr, 64); err == nil {
			return cost
		}
	}

	// Calculate independently from usage data.
	if rc.Response == nil || rc.Response.Usage == nil {
		return 0
	}

	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}

	if p.pricing == nil {
		slog.Warn("budget: no pricing table available")
		return 0
	}

	cost, ok := p.pricing.calculateCost(model, rc.Response.Usage.PromptTokens, rc.Response.Usage.CompletionTokens)
	if !ok {
		// Try the other model name.
		if model == rc.ResolvedModel && rc.Model != "" {
			cost, ok = p.pricing.calculateCost(rc.Model, rc.Response.Usage.PromptTokens, rc.Response.Usage.CompletionTokens)
		}
	}
	if !ok {
		return 0
	}
	return cost
}

func extractIdentity(rc *models.RequestContext) (team, user, keyName, model string) {
	team = rc.Metadata["key_team"]
	if team == "" {
		team = rc.Metadata["rbac_team"]
	}
	user = rc.Metadata["auth_key_owner"]
	keyName = rc.Metadata["auth_key_name"]
	model = rc.Model
	return
}

func extractTags(rc *models.RequestContext) map[string]string {
	// Budget tags can come from key metadata prefixed with "tag:".
	tags := make(map[string]string)
	for k, v := range rc.Metadata {
		if len(k) > 4 && k[:4] == "tag:" {
			tags[k[4:]] = v
		}
	}
	return tags
}

func defaultPricing() map[string]modelPricing {
	return map[string]modelPricing{
		"gpt-4o":                 {inputPerMTok: 2.50, outputPerMTok: 10.00},
		"gpt-4o-mini":            {inputPerMTok: 0.15, outputPerMTok: 0.60},
		"gpt-4-turbo":            {inputPerMTok: 10.00, outputPerMTok: 30.00},
		"gpt-4":                  {inputPerMTok: 30.00, outputPerMTok: 60.00},
		"gpt-3.5-turbo":          {inputPerMTok: 0.50, outputPerMTok: 1.50},
		"o1":                     {inputPerMTok: 15.00, outputPerMTok: 60.00},
		"o1-mini":                {inputPerMTok: 3.00, outputPerMTok: 12.00},
		"o3-mini":                {inputPerMTok: 1.10, outputPerMTok: 4.40},
		"claude-3-opus":          {inputPerMTok: 15.00, outputPerMTok: 75.00},
		"claude-3-sonnet":        {inputPerMTok: 3.00, outputPerMTok: 15.00},
		"claude-3-haiku":         {inputPerMTok: 0.25, outputPerMTok: 1.25},
		"claude-3.5-sonnet":      {inputPerMTok: 3.00, outputPerMTok: 15.00},
		"gemini-1.5-pro":         {inputPerMTok: 1.25, outputPerMTok: 5.00},
		"gemini-1.5-flash":       {inputPerMTok: 0.075, outputPerMTok: 0.30},
		"gemini-2.0-flash":       {inputPerMTok: 0.10, outputPerMTok: 0.40},
		"command-r-plus":         {inputPerMTok: 2.50, outputPerMTok: 10.00},
		"command-r":              {inputPerMTok: 0.15, outputPerMTok: 0.60},
	}
}
