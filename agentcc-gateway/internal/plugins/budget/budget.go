package budget

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
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
	tracker     *budgetpkg.Tracker
	pricing     *PricingLookup
	enabled     bool
	tenantStore *tenant.Store
	redisBudget RedisBudgetBackend // nil = local-only
	orgCounters sync.Map           // orgID -> *orgHierarchyCounters
	now         func() time.Time   // the clock; time.Now outside tests

	unpricedWarned      sync.Map     // model -> struct{}: models already warned about
	unpricedWarnedCount atomic.Int32 // size of unpricedWarned, for the cap
}

// maxUnpricedWarnings bounds the warn-once set so a caller cycling through
// model names cannot grow it without limit.
const maxUnpricedWarnings = 1024

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
		now:         time.Now,
	}
}

func (p *Plugin) clock() time.Time {
	if p.now == nil {
		return time.Now().UTC()
	}
	return p.now().UTC()
}

func (p *Plugin) Name() string  { return "budget" }
func (p *Plugin) Priority() int { return 40 } // After auth (20), RBAC (30), the quota (33), guardrails (34) and the cache (35).

// orgBudgetCounter tracks spend within a period for a single budget level.
type orgBudgetCounter struct {
	mu          sync.Mutex
	totalSpend  float64
	modelSpend  map[string]float64
	periodStart time.Time

	// The level's throttle: the wall-clock minute and the requests
	// admitted in it past the limit. Guarded by orgHierarchyCounters.throttleMu.
	throttleWindow time.Time
	throttleCount  int
}

// orgHierarchyCounters holds per-level counters for a single org.
type orgHierarchyCounters struct {
	mu         sync.Mutex // protects map creation only
	throttleMu sync.Mutex // guards every counter's throttle window
	org        *orgBudgetCounter
	teams      map[string]*orgBudgetCounter
	users      map[string]*orgBudgetCounter
	keys       map[string]*orgBudgetCounter
	tags       map[string]*orgBudgetCounter
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
		id := extractIdentity(rc)

		result := p.tracker.CheckBudgetUsers(id.team, id.users, id.keyName, id.model, id.tags)

		if result.Remaining >= 0 {
			rc.Metadata["budget_remaining"] = fmt.Sprintf("%.2f", result.Remaining)
		}
		if len(result.Warnings) > 0 {
			rc.Metadata["budget_warning"] = "true"
		}

		if !result.Allowed {
			rc.Metadata["budget_blocked_by"] = result.BlockedBy
			stampRefusal(rc, result.BlockedLevel, result.BlockedPeriod, result.BlockedPeriodStart, tenant.BudgetActionBlock)
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

// stampRefusal writes a budget refusal's metadata, over any
// value the caller sent: the level, its period and period start (RFC 3339
// UTC) and the action that refused.
func stampRefusal(rc *models.RequestContext, level, period string, start time.Time, action string) {
	rc.Metadata["budget_exceeded_level"] = level
	rc.Metadata["budget_period"] = period
	rc.Metadata["budget_period_start"] = budgetpkg.FormatPeriodStart(start)
	rc.Metadata["budget_action"] = action
}

// levelThrottle is a throttle level past its limit, waiting for a slot.
type levelThrottle struct {
	counter *orgBudgetCounter
	level   string // as the refusal metadata names it
	period  string
	start   time.Time
	rpm     int
	message string
}

// orgLevelCheck is one level's budget check.
type orgLevelCheck struct {
	orgID, level, key, period string
	limit                     float64
	action                    string
	rpm                       int
	warn                      float64
	perModel                  map[string]float64
}

// checkOrgHierarchicalBudgets checks all per-org budget levels and returns an error if blocked.
func (p *Plugin) checkOrgHierarchicalBudgets(budgets *tenant.BudgetsConfig, orgID string, rc *models.RequestContext) *models.APIError {
	hc := p.getOrgHierarchy(orgID)
	id := extractIdentity(rc)
	now := p.clock()

	defaultPeriod := budgets.DefaultPeriod
	if defaultPeriod == "" {
		defaultPeriod = "monthly"
	}
	warnThreshold := budgets.WarnFraction()

	lowestRemaining := -1.0
	var throttled []levelThrottle

	// Check org-level budget (backward compat: use OrgLimit if set).
	if budgets.OrgLimit > 0 {
		if err := p.checkLevel(hc, rc, &lowestRemaining, &throttled, now, orgLevelCheck{
			orgID: orgID, level: "org", period: orgBudgetPeriod(budgets), limit: budgets.OrgLimit,
			action: budgets.OrgBudgetAction(), rpm: budgets.OrgThrottleRPM(), warn: warnThreshold,
		}); err != nil {
			return err
		}
	}

	level := func(name, key string, lc *tenant.BudgetLevelConfig) *models.APIError {
		if lc == nil || lc.Limit <= 0 {
			return nil
		}
		period := lc.Period
		if period == "" {
			period = defaultPeriod
		}
		return p.checkLevel(hc, rc, &lowestRemaining, &throttled, now, orgLevelCheck{
			orgID: orgID, level: name, key: key, period: period, limit: lc.Limit,
			action: lc.LevelAction(), rpm: lc.LevelThrottleRPM(),
			warn: lc.LevelWarnFraction(warnThreshold), perModel: lc.PerModel,
		})
	}

	// Check team budget.
	if id.team != "" && budgets.Teams != nil {
		if err := level("team", id.team, budgets.Teams[id.team]); err != nil {
			return err
		}
	}

	// Check user budgets: the request's user and the key's owner.
	if budgets.Users != nil {
		for _, user := range id.users {
			if err := level("user", user, budgets.Users[user]); err != nil {
				return err
			}
		}
	}

	// Check key budget.
	if id.keyName != "" && budgets.Keys != nil {
		if err := level("key", id.keyName, budgets.Keys[id.keyName]); err != nil {
			return err
		}
	}

	// Check tag budgets.
	if budgets.Tags != nil {
		for _, tagKey := range id.tagKeys() {
			if err := level("tag", tagKey, budgets.Tags[tagKey]); err != nil {
				return err
			}
		}
	}

	// Every level admitted the request: take the throttle slots it needs.
	if len(throttled) > 0 {
		if err := p.takeThrottleSlots(hc, rc, throttled, now); err != nil {
			return err
		}
	}

	if lowestRemaining >= 0 {
		rc.Metadata["org_budget_remaining"] = fmt.Sprintf("%.2f", lowestRemaining)
	}

	return nil
}

// checkLevel checks one budget level, including its per-model sub-budgets:
// past the limit a block level refuses now, a throttle level is listed for a
// throttle slot, a warn level only warns.
func (p *Plugin) checkLevel(hc *orgHierarchyCounters, rc *models.RequestContext, lowestRemaining *float64, throttled *[]levelThrottle, now time.Time, c orgLevelCheck) *models.APIError {
	bc := hc.getOrCreateCounter(c.level, c.key)
	spend, modelSpend := p.getCounterSpendWithRedis(bc, c.orgID, c.level, c.key, c.period, now)
	refusalLevel := budgetpkg.RefusalLevel(c.level, c.key)
	period := budgetpkg.RefusalPeriod(c.period)
	start := budgetpkg.PeriodStart(period, now)

	note := func(remaining float64) {
		if remaining < 0 {
			remaining = 0
		}
		if *lowestRemaining < 0 || remaining < *lowestRemaining {
			*lowestRemaining = remaining
		}
	}
	past := func(message string) *models.APIError {
		switch c.action {
		case tenant.BudgetActionBlock:
			rc.Metadata["org_budget_blocked"] = "true"
			stampRefusal(rc, refusalLevel, period, start, tenant.BudgetActionBlock)
			return &models.APIError{
				Status:  http.StatusTooManyRequests,
				Type:    models.ErrTypeRateLimit,
				Code:    "org_budget_exceeded",
				Message: message,
			}
		case tenant.BudgetActionThrottle:
			for _, t := range *throttled {
				if t.counter == bc {
					return nil
				}
			}
			*throttled = append(*throttled, levelThrottle{
				counter: bc, level: refusalLevel, period: period, start: start, rpm: c.rpm,
				message: fmt.Sprintf("%s; throttled to %d requests a minute", message, c.rpm),
			})
		}
		return nil
	}

	note(c.limit - spend)
	if c.level == "org" {
		rc.Metadata["org_budget_remaining"] = fmt.Sprintf("%.2f", math.Max(c.limit-spend, 0))
	}
	if spend >= c.limit*c.warn {
		rc.Metadata["org_budget_warning"] = "true"
	}
	if spend >= c.limit {
		msg := fmt.Sprintf("org budget exceeded: spent $%.2f of $%.2f %s limit", spend, c.limit, c.period)
		if c.level != "org" {
			msg = fmt.Sprintf("%s '%s' budget exceeded: spent $%.2f of $%.2f %s limit", c.level, c.key, spend, c.limit, c.period)
		}
		if err := past(msg); err != nil {
			return err
		}
	}

	// Check per-model limits.
	model := rc.Model
	if model != "" && c.perModel != nil {
		if modelLimit, ok := c.perModel[model]; ok && modelLimit > 0 {
			ms := modelSpend[model]
			note(modelLimit - ms)
			if ms >= modelLimit*c.warn {
				rc.Metadata["org_budget_warning"] = "true"
			}
			if ms >= modelLimit {
				msg := fmt.Sprintf("%s '%s' per-model budget exceeded: model '%s' spent $%.2f of $%.2f %s limit", c.level, c.key, model, ms, modelLimit, c.period)
				if err := past(msg); err != nil {
					return err
				}
			}
		}
	}

	return nil
}

// takeThrottleSlots admits the request through every throttle level it is
// past: each admits rpm requests per wall-clock minute. When
// one has no slot left the request gets 429 budget_throttled with Retry-After
// (the seconds to that level's next minute) and no level's slot is taken.
// Slots are counted per gateway replica.
func (p *Plugin) takeThrottleSlots(hc *orgHierarchyCounters, rc *models.RequestContext, throttled []levelThrottle, now time.Time) *models.APIError {
	window := now.Truncate(time.Minute)
	hc.throttleMu.Lock()
	defer hc.throttleMu.Unlock()
	for _, t := range throttled {
		used := t.counter.throttleCount
		if !t.counter.throttleWindow.Equal(window) {
			used = 0
		}
		if used >= t.rpm {
			retry := int(math.Ceil(window.Add(time.Minute).Sub(now).Seconds()))
			if retry < 1 {
				retry = 1
			}
			rc.Metadata["org_budget_throttled"] = "true"
			stampRefusal(rc, t.level, t.period, t.start, tenant.BudgetActionThrottle)
			return &models.APIError{
				Status:        http.StatusTooManyRequests,
				Type:          models.ErrTypeRateLimit,
				Code:          "budget_throttled",
				Message:       t.message,
				RetryAfterSec: retry,
			}
		}
	}
	for _, t := range throttled {
		if !t.counter.throttleWindow.Equal(window) {
			t.counter.throttleWindow, t.counter.throttleCount = window, 0
		}
		t.counter.throttleCount++
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
			id := extractIdentity(rc)

			p.tracker.RecordSpendUsers(id.team, id.users, id.keyName, id.model, id.tags, cost)

			result := p.tracker.CheckBudgetUsers(id.team, id.users, id.keyName, id.model, id.tags)
			if result.Remaining >= 0 {
				rc.Metadata["budget_remaining"] = fmt.Sprintf("%.2f", result.Remaining)
			}
			if len(result.Warnings) > 0 {
				rc.Metadata["budget_warning"] = "true"
			}
		}
	}

	// Per-org spend: every org's org level, whether or not its budgets are
	// on or set, so a budget set partway through a period is checked against
	// the period's spend so far; each level its budgets configure.
	if cost > 0 && p.tenantStore != nil {
		if orgID := rc.Metadata[tenant.MetadataKeyOrgID]; orgID != "" {
			var budgets *tenant.BudgetsConfig
			if orgCfg := p.tenantStore.Get(orgID); orgCfg != nil {
				budgets = orgCfg.Budgets
			}
			p.recordOrgHierarchicalSpend(budgets, orgID, rc, cost)
		}
	}

	return pipeline.ResultContinue()
}

// orgBudgetPeriod is the period the org level counts over: the org's
// org_period, else its default_period, else monthly (also for an org with no
// budgets section).
func orgBudgetPeriod(budgets *tenant.BudgetsConfig) string {
	if budgets != nil {
		if budgets.OrgPeriod != "" {
			return budgets.OrgPeriod
		}
		if budgets.DefaultPeriod != "" {
			return budgets.DefaultPeriod
		}
	}
	return "monthly"
}

// recordOrgHierarchicalSpend records cost against the org level (budgets
// may be nil, on or off: the org level is always counted) and, when the org
// has a budgets section, against each level it configures, on or off, so
// switching budgets on enforces the period's spend so far. Response
// metadata is written only for an org whose budgets are on.
func (p *Plugin) recordOrgHierarchicalSpend(budgets *tenant.BudgetsConfig, orgID string, rc *models.RequestContext, cost float64) {
	hc := p.getOrgHierarchy(orgID)
	now := p.clock()

	orgCounter := hc.getOrCreateCounter("org", "")
	p.recordToCounterWithRedis(orgCounter, orgID, "org", "", rc.Model, cost, orgBudgetPeriod(budgets), now)
	if budgets == nil {
		return
	}
	if budgets.Enabled && budgets.OrgLimit > 0 {
		orgCounter.mu.Lock()
		remaining := budgets.OrgLimit - orgCounter.totalSpend
		orgCounter.mu.Unlock()
		if remaining < 0 {
			remaining = 0
		}
		rc.Metadata["org_budget_remaining"] = fmt.Sprintf("%.2f", remaining)
	}

	id := extractIdentity(rc)
	defaultPeriod := budgets.DefaultPeriod
	if defaultPeriod == "" {
		defaultPeriod = "monthly"
	}

	record := func(level, key string, lc *tenant.BudgetLevelConfig) {
		if lc == nil {
			return
		}
		period := lc.Period
		if period == "" {
			period = defaultPeriod
		}
		p.recordToCounterWithRedis(hc.getOrCreateCounter(level, key), orgID, level, key, id.model, cost, period, now)
	}

	if id.team != "" && budgets.Teams != nil {
		record("team", id.team, budgets.Teams[id.team])
	}
	// The request's user and the key's owner, each charged once.
	if budgets.Users != nil {
		for _, user := range id.users {
			record("user", user, budgets.Users[user])
		}
	}
	if id.keyName != "" && budgets.Keys != nil {
		record("key", id.keyName, budgets.Keys[id.keyName])
	}
	if budgets.Tags != nil {
		for _, tagKey := range id.tagKeys() {
			record("tag", tagKey, budgets.Tags[tagKey])
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
		p.redisBudget.RecordSpend(orgID, level, redisKeyPart(key), period, model, cost)
	}
}

// getCounterSpendWithRedis tries Redis first, falls back to local counter.
func (p *Plugin) getCounterSpendWithRedis(bc *orgBudgetCounter, orgID, level, key, period string, now time.Time) (float64, map[string]float64) {
	if p.redisBudget != nil && p.redisBudget.Available() {
		total, modelSpend, ok := p.redisBudget.GetSpend(orgID, level, redisKeyPart(key), period)
		if ok {
			return total, modelSpend
		}
	}
	// Fall back to local counter.
	return p.getCounterSpend(bc, period, now)
}

// InvalidateOrg is called on every change to an org's config (main.go's
// onOrgConfigChange). A change of config, its removal included, does not
// change what the org spent this period, and the org level is counted for
// every org, so the counters are kept: each resets at its own period start,
// and a level's limit, action and period are read from the config on every
// request. There is one small set of counters per org that sends traffic.
func (p *Plugin) InvalidateOrg(string) {}

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
		if rc.Response.Usage.PromptTokens+rc.Response.Usage.CompletionTokens > 0 {
			p.warnUnpriced(rc, model)
		}
		return 0
	}
	return cost
}

// warnUnpriced logs, once per model, that spend for the model cannot be
// metered. Returning a silent zero here is what let budgets pass unpriced
// traffic without any sign that they were not counting it. Budgets price from
// their own table, not the model database the cost plugin reads, so the hint
// names both keys: setting only one leaves the other gap open.
func (p *Plugin) warnUnpriced(rc *models.RequestContext, model string) {
	if !p.enabled || p.unpricedWarnedCount.Load() >= maxUnpricedWarnings {
		return
	}
	if _, seen := p.unpricedWarned.LoadOrStore(model, struct{}{}); seen {
		return
	}
	p.unpricedWarnedCount.Add(1)
	slog.Warn("budget: no pricing for model; its requests are not counted against any budget",
		"model", model,
		"provider", rc.Provider,
		"request_id", rc.RequestID,
		"hint", "set cost_tracking.custom_pricing.<model> (input_per_mtok / output_per_mtok, USD per million tokens); "+
			"the request log's cost is priced separately, from model_database.overrides.<model>.pricing (input_per_token / output_per_token, USD per token)",
	)
}

// identity is who a request's budgets are for.
type identity struct {
	team, keyName, model string
	// users: the request's user, then the key's owner when it
	// is another user: both users' budgets apply.
	users []string
	tags  map[string]string
}

// requestUserID is the request's user: the agent context's
// user id (x-agentcc-user-id or metadata.agent.user_id) when sent, else the
// body's user (rc.UserID), else the key's owner.
func requestUserID(rc *models.RequestContext) string {
	if rc.Agent.UserID != "" {
		return rc.Agent.UserID
	}
	if rc.UserID != "" {
		return rc.UserID
	}
	return rc.Metadata["auth_key_owner"]
}

func extractIdentity(rc *models.RequestContext) identity {
	id := identity{
		team:    rc.Metadata["key_team"],
		keyName: rc.Metadata["auth_key_name"],
		model:   rc.Model,
		tags:    extractTags(rc),
	}
	if id.team == "" {
		id.team = rc.Metadata["rbac_team"]
	}
	if user := requestUserID(rc); user != "" {
		id.users = append(id.users, user)
	}
	if owner := rc.Metadata["auth_key_owner"]; owner != "" && (len(id.users) == 0 || owner != id.users[0]) {
		id.users = append(id.users, owner)
	}
	return id
}

// tagKeys are the request's tags as budget keys ("key:value"), sorted.
func (id identity) tagKeys() []string {
	if len(id.tags) == 0 {
		return nil
	}
	keys := make([]string, 0, len(id.tags))
	for k, v := range id.tags {
		keys = append(keys, k+":"+v)
	}
	sort.Strings(keys)
	return keys
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

// redisKeyPart is a level key as the Redis counters key it: as is up to 64
// bytes, else "h:" and the first 40 hex digits of its SHA-256,
// so a long user id cannot grow a Redis key without bound.
func redisKeyPart(key string) string {
	if len(key) <= 64 {
		return key
	}
	sum := sha256.Sum256([]byte(key))
	return "h:" + hex.EncodeToString(sum[:])[:40]
}

func defaultPricing() map[string]modelPricing {
	return map[string]modelPricing{
		"gpt-4o":            {inputPerMTok: 2.50, outputPerMTok: 10.00},
		"gpt-4o-mini":       {inputPerMTok: 0.15, outputPerMTok: 0.60},
		"gpt-4-turbo":       {inputPerMTok: 10.00, outputPerMTok: 30.00},
		"gpt-4":             {inputPerMTok: 30.00, outputPerMTok: 60.00},
		"gpt-3.5-turbo":     {inputPerMTok: 0.50, outputPerMTok: 1.50},
		"o1":                {inputPerMTok: 15.00, outputPerMTok: 60.00},
		"o1-mini":           {inputPerMTok: 3.00, outputPerMTok: 12.00},
		"o3-mini":           {inputPerMTok: 1.10, outputPerMTok: 4.40},
		"claude-3-opus":     {inputPerMTok: 15.00, outputPerMTok: 75.00},
		"claude-3-sonnet":   {inputPerMTok: 3.00, outputPerMTok: 15.00},
		"claude-3-haiku":    {inputPerMTok: 0.25, outputPerMTok: 1.25},
		"claude-3.5-sonnet": {inputPerMTok: 3.00, outputPerMTok: 15.00},
		"gemini-1.5-pro":    {inputPerMTok: 1.25, outputPerMTok: 5.00},
		"gemini-1.5-flash":  {inputPerMTok: 0.075, outputPerMTok: 0.30},
		"gemini-2.0-flash":  {inputPerMTok: 0.10, outputPerMTok: 0.40},
		"command-r-plus":    {inputPerMTok: 2.50, outputPerMTok: 10.00},
		"command-r":         {inputPerMTok: 0.15, outputPerMTok: 0.60},
	}
}
