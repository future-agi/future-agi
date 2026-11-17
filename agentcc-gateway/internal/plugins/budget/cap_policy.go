package budget

import (
	"fmt"
	"math"
	"regexp"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const maxCapUSD = 1_000_000_000

var capIdentity = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$`)

// CapsEnabled is deliberately opt-in and is unrelated to legacy budget keys.
func CapsEnabled(cfg *tenant.OrgConfig) bool {
	return cfg.AdmissionCapsOn()
}

func capAgents(cfg *tenant.OrgConfig) *tenant.AgentsConfig {
	if cfg != nil && cfg.Routing != nil && cfg.Routing.Agents != nil {
		return cfg.Routing.Agents
	}
	return &tenant.AgentsConfig{}
}

func capLimit(v *float64) bool {
	return v != nil && !math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0 && *v <= maxCapUSD
}

// ValidateCapPolicy is also used at admission: directly loaded gateway config
// must never turn an invalid enabled policy into a fail-open execution path.
func ValidateCapPolicy(cfg *tenant.OrgConfig) error {
	if !CapsEnabled(cfg) {
		return nil
	}
	b := cfg.Budgets
	if !b.Enabled || cfg.BudgetsFailMode() != "closed" || cfg.BudgetsNoMaxTokens() != "clamp" {
		return fmt.Errorf("admission caps require enabled budgets, fail_mode closed and no_max_tokens clamp")
	}
	for _, levels := range []map[string]*tenant.AdmissionCapConfig{b.Agents, b.KeyIDs} {
		for id, c := range levels {
			if !capIdentity.MatchString(id) || c == nil || !capLimit(c.Limit) || c.CapPeriod() != "daily" || !c.CapHard() || c.CapAction() != "block" {
				return fmt.Errorf("invalid daily hard admission cap for %q", id)
			}
		}
	}
	a := capAgents(cfg)
	if err := validateCapAgents(a); err != nil {
		return err
	}
	if cfg.Routing == nil || cfg.Routing.Rollout == nil {
		return nil
	}
	candidate := capAgents(&tenant.OrgConfig{Routing: cfg.Routing.Rollout.Routing})
	if err := validateCapAgents(candidate); err != nil {
		return err
	}
	for key, agent := range a.KeyBindings {
		if candidate.KeyBindings[key] != agent {
			return fmt.Errorf("rollout cannot drop or change administrative key bindings")
		}
	}
	if a.MaxRunUSD != nil && (candidate.MaxRunUSD == nil || *candidate.MaxRunUSD > *a.MaxRunUSD) {
		return fmt.Errorf("rollout cannot drop or raise the configured run ceiling")
	}
	return nil
}

func validateCapAgents(a *tenant.AgentsConfig) error {
	if a.UnpricedUnderCap != "" && a.UnpricedUnderCap != "reject" {
		return fmt.Errorf("admission caps require unpriced_under_cap reject")
	}
	if a.MaxRunUSD != nil && !capLimit(a.MaxRunUSD) {
		return fmt.Errorf("invalid configured run ceiling")
	}
	for key, agent := range a.KeyBindings {
		if !capIdentity.MatchString(key) || !capIdentity.MatchString(agent) {
			return fmt.Errorf("invalid administrative key binding")
		}
	}
	return nil
}

// capMicros rounds a limit down, never granting a fractional micro of extra room.
func capMicros(v *float64) int64 {
	if v == nil {
		return -1
	}
	return int64(math.Floor(*v * 1_000_000))
}
