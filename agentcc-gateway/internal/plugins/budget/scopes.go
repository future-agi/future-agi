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
	team, user, key, model := videoIdentity(rc)
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

// videoScopes snapshots the same authenticated team/user/key/tag identity used
// by CheckBudget. Global scopes have their own keys, never tenant counters.
func (p *Plugin) videoScopes(rc *models.RequestContext) []video.BudgetReservation {
	team, user, key, model := videoIdentity(rc)
	scopes := p.tracker.VideoScopes(team, user, key, model, extractTags(rc))
	out := make([]video.BudgetReservation, 0, len(scopes))
	for _, s := range scopes {
		out = append(out, video.BudgetReservation{Global: true, Level: "global_" + s.Level, Key: s.Key, Period: s.Period, Limit: s.Limit, ModelLimit: s.ModelLimit})
	}
	return out
}

// videoIdentity is the identity video budgets reserve and settle against, as
// the video design defines it: the key's team, the key's owner as the one
// user, the key's name and the model. Ordinary spend is recorded for every
// user extractIdentity names; a video reservation keeps this narrower set.
func videoIdentity(rc *models.RequestContext) (team, user, key, model string) {
	id := extractIdentity(rc)
	return id.team, rc.Metadata["auth_key_owner"], id.keyName, id.model
}
