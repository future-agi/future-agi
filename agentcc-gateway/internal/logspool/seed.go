package logspool

import (
	"context"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// PeriodStart is the start (UTC) of the budget period t falls in, as the
// control plane's spend summary computes it (spend_summary.py _period_start):
// daily from midnight, weekly from Monday, monthly from the 1st, total from
// the beginning; anything else is monthly.
func PeriodStart(period string, t time.Time) time.Time {
	t = t.UTC()
	day := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
	switch period {
	case "daily":
		return day
	case "weekly":
		return day.AddDate(0, 0, -((int(day.Weekday()) + 6) % 7))
	case "total":
		return time.Time{}
	default:
		return time.Date(t.Year(), t.Month(), 1, 0, 0, 0, 0, time.UTC)
	}
}

// PeriodEnd is the end of the period that starts at start.
func PeriodEnd(period string, start time.Time) time.Time {
	switch period {
	case "daily":
		return start.AddDate(0, 0, 1)
	case "weekly":
		return start.AddDate(0, 0, 7)
	case "total":
		return time.Time{}
	default:
		return start.AddDate(0, 1, 0)
	}
}

// Spend is the spool's spend for one budget period, grouped as the control
// plane's spend summary groups (by org; within it by key name, user id and
// model). A spend line's KeyID holds the key's name (auth_key_name).
type Spend struct {
	Orgs map[string]*tenant.OrgSpendSummary
}

func (x *Spend) add(org, keyID, userID, model string, cost float64) {
	if org == "" || cost <= 0 {
		return
	}
	if x.Orgs == nil {
		x.Orgs = map[string]*tenant.OrgSpendSummary{}
	}
	o := x.Orgs[org]
	if o == nil {
		o = &tenant.OrgSpendSummary{PerKey: map[string]float64{}, PerUser: map[string]float64{}, PerModel: map[string]float64{}}
		x.Orgs[org] = o
	}
	o.TotalSpend += cost
	if keyID != "" {
		o.PerKey[keyID] += cost
	}
	if userID != "" {
		o.PerUser[userID] += cost
	}
	if model != "" {
		o.PerModel[model] += cost
	}
}

// Empty reports whether x holds no spend.
func (x *Spend) Empty() bool { return x == nil || len(x.Orgs) == 0 }

// Raw is the spool's spend as read at one moment: the spend lines of every
// spooled batch whose MAC verifies and that is not marked delivered, counted
// once per (org, request id), and the tombstones of dropped batches for the
// period that held that moment.
type Raw struct {
	Lines     []SpendLine
	Tombs     map[string]float64
	TombStart time.Time
}

// Snapshot reads the spool's spend. Taken before the spend summary is read, a
// batch delivered meanwhile is counted in both, never in neither.
func (s *Store) Snapshot(ctx context.Context, keys ...[]byte) (*Raw, error) {
	batches, err := s.All(ctx)
	if err != nil {
		return nil, err
	}
	start := PeriodStart(s.period, s.now())
	tombs, err := s.Tombstones(ctx, start)
	if err != nil {
		return nil, err
	}
	return rawOf(batches, tombs, start, keys...), nil
}

func rawOf(batches []Stored, tombs map[string]float64, tombStart time.Time, keys ...[]byte) *Raw {
	r := &Raw{Tombs: tombs, TombStart: tombStart}
	type seenKey struct{ org, id string }
	seen := map[seenKey]bool{}
	for _, st := range batches {
		if st.Corrupt || st.Delivered || st.Batch == nil || !st.Batch.Verify(keys...) {
			continue
		}
		for _, l := range st.Batch.Spend {
			k := seenKey{l.Org, l.RequestID}
			if seen[k] {
				continue
			}
			seen[k] = true
			r.Lines = append(r.Lines, l)
		}
	}
	return r
}

// Spend is X for the budget period that starts at periodStart: the lines of
// requests that started in it, and the tombstones when they are that period's.
func (r *Raw) Spend(period string, periodStart time.Time) *Spend {
	x := &Spend{}
	if r == nil {
		return x
	}
	for _, l := range r.Lines {
		if period != "total" && l.StartedAt.Before(periodStart) {
			continue
		}
		x.add(l.Org, l.KeyID, l.UserID, l.Model, l.Cost)
	}
	if period == "total" || r.TombStart.Equal(periodStart) {
		for field, cost := range r.Tombs {
			parts := strings.SplitN(field, fieldSep, 4)
			if len(parts) != 4 {
				continue
			}
			x.add(parts[0], parts[1], parts[2], parts[3], cost)
		}
	}
	return x
}

// Merge returns the spend summary S' = S + X, per org and within it per key,
// user and model. Orgs only X knows are added: the summary leaves out orgs
// with no recorded spend. S may be nil (the control plane could not be
// read): S' is then X alone, for the period that starts at periodStart.
func Merge(s *tenant.SpendSummary, x *Spend, period string, periodStart time.Time) *tenant.SpendSummary {
	out := &tenant.SpendSummary{Period: period, PeriodStart: periodStart.Format(time.RFC3339), Orgs: map[string]tenant.OrgSpendSummary{}}
	if s != nil {
		out.Period, out.PeriodStart = s.Period, s.PeriodStart
		for org, o := range s.Orgs {
			out.Orgs[org] = tenant.OrgSpendSummary{TotalSpend: o.TotalSpend, PerKey: clone(o.PerKey),
				PerUser: clone(o.PerUser), PerModel: clone(o.PerModel),
				PerTeam: clone(o.PerTeam), PerTag: clone(o.PerTag)}
		}
	}
	if x.Empty() {
		return out
	}
	for org, xo := range x.Orgs {
		o := out.Orgs[org]
		o.TotalSpend += xo.TotalSpend
		o.PerKey = addInto(o.PerKey, xo.PerKey)
		o.PerUser = addInto(o.PerUser, xo.PerUser)
		o.PerModel = addInto(o.PerModel, xo.PerModel)
		out.Orgs[org] = o
	}
	return out
}

func clone(m map[string]float64) map[string]float64 {
	out := make(map[string]float64, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func addInto(dst, src map[string]float64) map[string]float64 {
	if dst == nil {
		dst = make(map[string]float64, len(src))
	}
	for k, v := range src {
		dst[k] += v
	}
	return dst
}

// SummaryPeriodStart is the period start the control plane's summary names,
// else the one PeriodStart computes for now.
func SummaryPeriodStart(s *tenant.SpendSummary, period string, now time.Time) time.Time {
	if s != nil && s.PeriodStart != "" {
		if t, err := time.Parse(time.RFC3339, s.PeriodStart); err == nil {
			return t.UTC()
		}
		if t, err := time.Parse("2006-01-02T15:04:05.999999-07:00", s.PeriodStart); err == nil {
			return t.UTC()
		}
	}
	return PeriodStart(period, now)
}
