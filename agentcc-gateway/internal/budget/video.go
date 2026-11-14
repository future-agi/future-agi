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

// spend returns a level's spend for checks. Without a shared ledger it is the
// pod-local counter, as before the ledger existed. With one attached and
// readable, the ledger is authoritative: it holds every replica's ordinary
// spend and every durable video reservation and settlement. If the ledger read
// fails, the pod-local counter (which records every ordinary response on this
// replica) is used instead and unavailable reports the fallback, as tenant
// budgets do. The ledger is read before the level lock is taken, so a slow
// Redis round trip never blocks other requests on the same level.
func (t *Tracker) spend(lb *levelBudget, level, key string, now time.Time) (total float64, perModel map[string]float64, unavailable bool) {
	var shared float64
	var sharedModels map[string]float64
	ok := false
	if t.durable != nil {
		if level == "org" {
			key = ""
		}
		shared, sharedModels, ok = t.durable.GetSpend("", "global_"+level, key, lb.period)
	}
	if ok {
		perModel = make(map[string]float64, len(sharedModels))
		for m, v := range sharedModels {
			perModel[m] = v
		}
		return shared, perModel, false
	}
	lb.counter.mu.Lock()
	defer lb.counter.mu.Unlock()
	lb.counter.maybeReset(lb.period, now)
	perModel = make(map[string]float64, len(lb.counter.modelSpend))
	for m, v := range lb.counter.modelSpend {
		perModel[m] = v
	}
	return lb.counter.totalSpend, perModel, t.durable != nil
}

type VideoScope struct {
	Level, Key, Period string
	Limit, ModelLimit  float64
}

// VideoScopes reserves global video exposure against each level's configured
// ceiling. The shared ledger already holds ordinary spend from every replica,
// and durable CheckAndRecordSpend checks that total and concurrent video
// reservations atomically, so the pod-local counter is not subtracted again.
// Settlement uses the persisted scope, not a later request identity or a
// configuration lookup.
func (t *Tracker) VideoScopes(team, user, key, model string, tags map[string]string) []VideoScope {
	out := []VideoScope{}
	add := func(level, key string, lb *levelBudget) {
		if lb == nil {
			return
		}
		s := VideoScope{Level: level, Key: key, Period: lb.period}
		if lb.hard {
			if lb.limit > 0 {
				s.Limit = lb.limit
			}
			if n := lb.perModel[model]; n > 0 {
				s.ModelLimit = n
			}
		}
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
