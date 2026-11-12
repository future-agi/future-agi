package keyscope

import (
	"math"
	"strconv"
	"strings"
	"time"
)

// Budget periods a key budget may name. A period starts at 00:00 UTC: the
// day, the Monday of the week, the first of the month.
const (
	PeriodDaily   = "daily"
	PeriodWeekly  = "weekly"
	PeriodMonthly = "monthly"
)

// Budget is a key's spend limit per period, in microdollars. It counts what
// every model call the key makes costs (the cost plugin's "cost": billed for
// a managed call, the upstream cost for a call on the org's own provider
// key), and refuses the next call once the period's spend reaches the limit.
type Budget struct {
	LimitMicros int64
	Period      string
	valid       bool
}

// ParseBudget reads a key's budget entries. ok is false when the key carries
// neither (no budget). An entry the gateway cannot read (one without the
// other, a limit that is not a finite number of dollars at or above zero, or
// an unknown period) gives a budget that refuses every call: it fails
// closed.
func ParseBudget(metadata map[string]string) (b Budget, ok bool) {
	limit, hasLimit := metadata[MetaBudgetLimitUSD]
	period, hasPeriod := metadata[MetaBudgetPeriod]
	if !hasLimit && !hasPeriod {
		return Budget{}, false
	}
	usd, err := strconv.ParseFloat(strings.TrimSpace(limit), 64)
	if !hasLimit || !hasPeriod || err != nil || math.IsNaN(usd) || math.IsInf(usd, 0) || usd < 0 || usd > 1e9 {
		return Budget{Period: period}, true
	}
	switch period {
	case PeriodDaily, PeriodWeekly, PeriodMonthly:
	default:
		return Budget{Period: period}, true
	}
	return Budget{LimitMicros: int64(math.Round(usd * 1_000_000)), Period: period, valid: true}, true
}

// Valid reports whether the budget's entries were readable.
func (b Budget) Valid() bool { return b.valid }

// Exhausted reports whether a key that has spent spentMicros this period may
// make no more calls.
func (b Budget) Exhausted(spentMicros int64) bool {
	return !b.valid || spentMicros >= b.LimitMicros
}

// PeriodStart is the start of the period now falls in.
func (b Budget) PeriodStart(now time.Time) time.Time {
	return periodStart(b.Period, now)
}

// ResetsAt is when the period now falls in ends, and the spend starts again
// at zero.
func (b Budget) ResetsAt(now time.Time) time.Time {
	start := periodStart(b.Period, now)
	switch b.Period {
	case PeriodDaily:
		return start.AddDate(0, 0, 1)
	case PeriodWeekly:
		return start.AddDate(0, 0, 7)
	default:
		return start.AddDate(0, 1, 0)
	}
}

func periodStart(period string, now time.Time) time.Time {
	now = now.UTC()
	switch period {
	case PeriodDaily:
		return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	case PeriodWeekly:
		offset := (int(now.Weekday()) + 6) % 7 // days since Monday
		d := now.AddDate(0, 0, -offset)
		return time.Date(d.Year(), d.Month(), d.Day(), 0, 0, 0, 0, time.UTC)
	default:
		return time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	}
}
