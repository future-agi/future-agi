package budget

import "time"

// The level, period and period start a budget refusal names, shared by the
// node tracker and the per-org budget plugin.

// RefusalPeriod is the period a refusal names: daily, weekly, monthly or
// total. Any other configured period runs as monthly (PeriodStart), so it is
// named monthly.
func RefusalPeriod(period string) string {
	switch period {
	case "daily", "weekly", "monthly", "total":
		return period
	}
	return "monthly"
}

// RefusalLevel is a level as a refusal names it: "org", else "<level>:<key>".
func RefusalLevel(level, key string) string {
	if level == "org" {
		return "org"
	}
	return level + ":" + key
}

// FormatPeriodStart is a period start as a refusal carries it: RFC 3339 UTC
// ("0001-01-01T00:00:00Z" for total).
func FormatPeriodStart(start time.Time) string {
	return start.UTC().Format(time.RFC3339)
}

func (r *CheckResult) setBlockedLevel(level, key, period string, now time.Time) {
	r.BlockedLevel = RefusalLevel(level, key)
	r.BlockedPeriod = RefusalPeriod(period)
	r.BlockedPeriodStart = PeriodStart(r.BlockedPeriod, now)
}

// distinctUsers drops empty and repeated user ids, keeping order.
func distinctUsers(users []string) []string {
	out := users[:0:0]
	for _, u := range users {
		if u == "" {
			continue
		}
		seen := false
		for _, o := range out {
			if o == u {
				seen = true
				break
			}
		}
		if !seen {
			out = append(out, u)
		}
	}
	return out
}

// DefaultPeriod is the tracker's default budget period (monthly when the
// config names none): the period cmd/agentcc/main.go reads its start-up
// spend summary for.
func (t *Tracker) DefaultPeriod() string {
	if t == nil {
		return ""
	}
	return t.defaultPeriod
}
