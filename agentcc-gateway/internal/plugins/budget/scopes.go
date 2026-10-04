package budget

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"sort"
)

// HierarchyScopes is shared by ordinary spend recording and video reservations.
// Zero limits retain soft-budget accounting without imposing a hard ceiling.
func HierarchyScopes(b *tenant.BudgetsConfig, rc *models.RequestContext) []video.BudgetReservation {
	if b == nil || !b.Enabled {
		return nil
	}
	period := b.DefaultPeriod
	if period == "" {
		period = "monthly"
	}
	scopes := []video.BudgetReservation{}
	if b.OrgLimit > 0 {
		p := b.OrgPeriod
		if p == "" {
			p = period
		}
		limit := b.OrgLimit
		if !b.HardLimit {
			limit = 0
		}
		scopes = append(scopes, video.BudgetReservation{Level: "org", Period: p, Limit: limit})
	}
	team, user, key, model := extractIdentity(rc)
	add := func(level, key string, lc *tenant.BudgetLevelConfig) {
		if key == "" || lc == nil {
			return
		}
		p := lc.Period
		if p == "" {
			p = period
		}
		limit, ml := lc.Limit, lc.PerModel[model]
		if lc.Hard != nil && !*lc.Hard {
			limit = 0
			ml = 0
		}
		scopes = append(scopes, video.BudgetReservation{Level: level, Key: key, Period: p, Limit: limit, ModelLimit: ml})
	}
	add("team", team, b.Teams[team])
	add("user", user, b.Users[user])
	add("key", key, b.Keys[key])
	tags := extractTags(rc)
	keys := make([]string, 0, len(tags))
	for k, v := range tags {
		keys = append(keys, k+":"+v)
	}
	sort.Strings(keys)
	for _, key := range keys {
		add("tag", key, b.Tags[key])
	}
	return scopes
}
