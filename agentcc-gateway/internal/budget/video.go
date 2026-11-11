package budget

import (
	"sort"
	"time"
)

// GlobalBudgetBackend shares global counters between ordinary responses and
// durable video reservations/settlement. The no-backend tracker remains local.
type GlobalBudgetBackend interface {
	GetSpend(org, level, key, period string) (float64, map[string]float64, bool)
	RecordSpend(org, level, key, period, model string, cost float64) (float64, bool)
}

// SetGlobalBudgetBackend is startup-only, before requests or workers start.
func (t *Tracker) SetGlobalBudgetBackend(r GlobalBudgetBackend) { t.durable = r }

// combinedSpend requires the level's counter lock. Failed durable reads deny
// budget checks instead of silently hiding reserved/settled video spend.
func (t *Tracker) combinedSpend(lb *levelBudget, level, key string) (float64, map[string]float64, bool) {
	total := lb.counter.totalSpend
	models := make(map[string]float64, len(lb.counter.modelSpend))
	for m, v := range lb.counter.modelSpend {
		models[m] = v
	}
	if t.durable != nil {
		if level == "org" {
			key = ""
		}
		shared, perModel, ok := t.durable.GetSpend("", "global_"+level, key, lb.period)
		if !ok {
			return total, models, false
		}
		total += shared
		for m, v := range perModel {
			models[m] += v
		}
	}
	return total, models, true
}

type VideoScope struct {
	Level, Key, Period string
	Limit, ModelLimit  float64
}

// VideoScopes reserves global video exposure against the remaining ordinary
// spend ceiling. Durable CheckAndRecordSpend accounts for concurrent video
// reservations atomically; settlement uses the persisted scope, not a later
// request identity or a configuration lookup.
func (t *Tracker) VideoScopes(team, user, key, model string, tags map[string]string) []VideoScope {
	out := []VideoScope{}
	add := func(level, key string, lb *levelBudget) {
		if lb == nil {
			return
		}
		s := VideoScope{Level: level, Key: key, Period: lb.period}
		lb.counter.mu.Lock()
		lb.counter.maybeReset(lb.period, time.Now().UTC())
		if lb.hard {
			if lb.limit > 0 {
				s.Limit = lb.limit - lb.counter.totalSpend
				if s.Limit <= 0 {
					s.Limit = -1
				}
			}
			if n := lb.perModel[model]; n > 0 {
				s.ModelLimit = n - lb.counter.modelSpend[model]
				if s.ModelLimit <= 0 {
					s.ModelLimit = -1
				}
			}
		}
		lb.counter.mu.Unlock()
		out = append(out, s)
	}
	add("org", "", t.org)
	if team != "" {
		add("team", team, t.teams[team])
	}
	if user != "" {
		add("user", user, t.users[user])
	}
	if key != "" {
		add("key", key, t.keys[key])
	}
	names := make([]string, 0, len(tags))
	for k, v := range tags {
		names = append(names, k+":"+v)
	}
	sort.Strings(names)
	for _, name := range names {
		add("tag", name, t.tags[name])
	}
	return out
}
