// Package spendguard is the managed spend guard (plan G17): it refuses a
// managed request that would push its org's or its key's managed spend over
// a velocity cap, or a new org's spend over its daily cap, refuses every
// managed request while the org is paused, and raises an alert, and with
// auto_pause pauses the org, when the org's spend jumps.
//
// Guard is the check itself. It takes no request context, so callers
// outside the plugin pipeline (M18's managed tool calls) run it too; the
// pipeline plugin (plugin.go) calls the same three functions.
package spendguard

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Guard admits managed spend against the spend guard and corrects its
// windows when the spend settles or is voided.
type Guard struct {
	store     *redisstate.SpendGuardStore
	defaults  config.SpendGuardConfig
	orgConfig func(orgID string) *tenant.OrgConfig
	alert     Alerter
	now       func() time.Time
}

// NewGuard returns the guard over store, with the gateway-wide
// managed.spend_guard as every org's baseline.
func NewGuard(store *redisstate.SpendGuardStore, defaults config.SpendGuardConfig) *Guard {
	return &Guard{store: store, defaults: defaults, now: time.Now}
}

// SetOrgConfig sets how an org's stored config is read, for its own
// managed_models.spend_guard. Call it before the guard is used.
func (g *Guard) SetOrgConfig(fn func(orgID string) *tenant.OrgConfig) { g.orgConfig = fn }

// SetAlerter sets where anomaly alerts go. Call it before the guard is used.
func (g *Guard) SetAlerter(a Alerter) { g.alert = a }

// SetClock replaces the clock, for tests.
func (g *Guard) SetClock(now func() time.Time) { g.now = now }

// Admit checks one managed spend of estimateMicros (its worst-case cost) by
// orgID through keyID ("" for a caller without a key) under the caller's
// hold id, and returns the refusal to send, or nil when it was admitted and
// added to the windows. The checks run in order: the new-org daily cap, the
// EE budget pause, the auto-pause, then the key's and the org's minute and
// hour windows. Every admission must end in Settle or Void with the same
// hold id. Redis unavailable fails closed (503).
func (g *Guard) Admit(ctx context.Context, orgID, keyID string, estimateMicros int64, holdID string) *models.APIError {
	now := g.now()
	limits := g.settings(orgID).limits
	v, err := g.store.Admit(ctx, redisstate.SpendGuardAdmission{Org: orgID, KeyID: keyID, HoldID: holdID,
		EstimateMicros: estimateMicros, Limits: limits, Now: now})
	if err != nil {
		slog.Error("managed spend guard unavailable; refusing the managed request", "org_id", orgID, "hold_id", holdID, "error", err)
		return creditsplugin.ErrBillingUnavailable()
	}
	switch v.Outcome {
	case redisstate.SpendGuardAdmitted:
		return nil
	case redisstate.SpendGuardNewOrgCap:
		// Preserve the daily gate's precedence even if this request would also
		// exceed a velocity cap: disclose the limit that actually refused it.
		return errNewOrgDailyCap()
	case redisstate.SpendGuardOverCap:
		// Use the same effective limits as the atomic admission, not a second
		// config read. Waiting cannot make this estimate fit an empty window.
		if limits.MinuteMicros > 0 && estimateMicros > limits.MinuteMicros {
			return errRequestExceedsMinuteCap()
		}
		if limits.HourMicros > 0 && estimateMicros > limits.HourMicros {
			return errRequestExceedsHourCap()
		}
		return errVelocity(v, keyID, now)
	case redisstate.SpendGuardBudgetPaused:
		return errPaused(pausedByBudget)
	case redisstate.SpendGuardAutoPaused:
		return errPaused(pausedByAnomaly)
	}
	slog.Error("managed spend guard returned an unknown outcome; refusing the managed request", "org_id", orgID, "outcome", v.Outcome)
	return creditsplugin.ErrBillingUnavailable()
}

// Settle replaces an admitted spend's estimate with billedMicros in the
// windows, once per hold id, then checks the org's spend for an anomaly. A
// failure is logged: the estimate stays counted, the safe side.
func (g *Guard) Settle(ctx context.Context, orgID, holdID string, billedMicros int64) {
	now := g.now()
	sums, applied, err := g.store.Settle(ctx, orgID, holdID, billedMicros, now)
	if err != nil {
		slog.Warn("managed spend guard: settle not recorded; the estimate stays in the window",
			"org_id", orgID, "hold_id", holdID, "billed_micros", billedMicros, "error", err)
		return
	}
	if applied {
		g.checkAnomaly(ctx, orgID, sums, now)
	}
}

// Void takes an admitted spend's estimate out of the windows, once per hold
// id. A failure is logged: the estimate stays counted until its buckets
// leave the windows.
func (g *Guard) Void(ctx context.Context, orgID, holdID string) {
	if _, err := g.store.Void(ctx, orgID, holdID); err != nil {
		slog.Warn("managed spend guard: void not recorded; the estimate stays in the window",
			"org_id", orgID, "hold_id", holdID, "error", err)
	}
}

// Unpause clears the org's auto-pause and reports whether one was set
// (DELETE /-/orgs/{org_id}/credits/pause). The EE budget pause is EE's to
// lift.
func (g *Guard) Unpause(ctx context.Context, orgID string) (bool, error) {
	return g.store.Unpause(ctx, orgID)
}

// settings is what the guard enforces for one org.
type settings struct {
	limits             redisstate.SpendGuardLimits
	alertPerHourMicros int64
	autoPause          bool
}

// settings merges the gateway-wide spend guard with the org's own. The org's
// can only tighten the gateway's, which protects the shared upstream
// account: each cap and the alert threshold is the stricter non-zero value
// of the two, and auto_pause is on when either turns it on.
func (g *Guard) settings(orgID string) settings {
	d := g.defaults
	minute, _ := billing.SpendCapMicros(d.MaxUSDPerMinute)
	hour, _ := billing.SpendCapMicros(d.MaxUSDPerHour)
	daily, _ := billing.SpendCapMicros(d.NewOrgDailyCapUSD)
	s := settings{
		limits: redisstate.SpendGuardLimits{
			MinuteMicros:      minute,
			HourMicros:        hour,
			NewOrgDailyMicros: daily,
		},
		alertPerHourMicros: billing.USDToMicros(d.AlertUSDPerHour),
		autoPause:          d.AutoPause,
	}
	if g.orgConfig == nil {
		return s
	}
	cfg := g.orgConfig(orgID)
	if cfg == nil || cfg.ManagedModels == nil || cfg.ManagedModels.SpendGuard == nil {
		return s
	}
	org := cfg.ManagedModels.SpendGuard
	tighten(&s.limits.MinuteMicros, org.MaxUSDPerMinute)
	tighten(&s.limits.HourMicros, org.MaxUSDPerHour)
	tighten(&s.limits.NewOrgDailyMicros, org.NewOrgDailyCapUSD)
	tighten(&s.alertPerHourMicros, org.AlertUSDPerHour)
	s.autoPause = s.autoPause || org.AutoPause
	return s
}

// tighten lowers *micros (0 is unset) to the org's value when the org sets a
// stricter one.
func tighten(micros *int64, orgUSD *float64) {
	if orgUSD == nil {
		return
	}
	if v, _ := billing.SpendCapMicros(*orgUSD); v > 0 && (*micros == 0 || v < *micros) {
		*micros = v
	}
}
