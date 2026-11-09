// Package inclusive implements R601's one upstream total, one 5% fee contract.
// Component prices are admission bounds only, never additive settlement lines.
package inclusive

import (
	"fmt"
	"math/big"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func round(r *big.Rat) (int64, error) {
	q, rem := new(big.Int), new(big.Int)
	q.QuoRem(r.Num(), r.Denom(), rem)
	if rem.Lsh(rem, 1).Cmp(r.Denom()) >= 0 {
		q.Add(q, big.NewInt(1))
	}
	if !q.IsInt64() || q.Sign() < 0 || q.Int64() > 1<<60 {
		return 0, fmt.Errorf("cost exceeds supported money range")
	}
	return q.Int64(), nil
}

// Charge applies both half-up operations, in order. 9.5 upstream micros is
// 10 rounded upstream micros and 11 billed micros, NOT ceil(9.5*1.05)=10.
// Upstream rounding reads the wire digits directly, so every lexeme the
// decimal grammar admits is priced exactly (see decimal.go).
func Charge(usd string) (upstream, billed int64, err error) {
	w, err := lex(usd)
	if err != nil {
		return 0, 0, err
	}
	if upstream, err = w.micros(); err != nil {
		return 0, 0, err
	}
	billed, err = Markup(upstream)
	return
}

// ChargeRational shares the same two-stage rounding with owned token-card
// estimates, whose decimal rate/count products are already an exact rational.
// It never mutates usd. Provider totals should use Charge to validate their wire
// decimal before parsing, rather than converting through a binary float.
func ChargeRational(usd *big.Rat) (upstream, billed int64, err error) {
	if usd == nil || usd.Sign() < 0 {
		return 0, 0, fmt.Errorf("invalid nonnegative amount")
	}
	return charge(usd)
}

func charge(r *big.Rat) (upstream, billed int64, err error) {
	upstream, err = round(new(big.Rat).Mul(r, big.NewRat(1000000, 1)))
	if err != nil {
		return 0, 0, err
	}
	billed, err = Markup(upstream)
	return
}
func Markup(upstream int64) (int64, error) {
	if upstream < 0 {
		return 0, fmt.Errorf("negative upstream amount")
	}
	return round(new(big.Rat).Mul(new(big.Rat).SetInt64(upstream), big.NewRat(105, 100)))
}

// Price never falls back to token evidence. Missing/invalid inclusive cost is
// charged at the FULL hold, labelled estimated, pending reconciliation. So is
// a total a later event contested with an explicit invalid cost: the terminal
// report cannot borrow the earlier partial total. Valid over-hold totals
// remain actual charges: the existing ledger permits debt.
// The caller must persist the anomaly and quarantine BEFORE further admission.
func Price(u *models.Usage, hold int64) models.InclusiveAttempt {
	estimate, _ := round(new(big.Rat).Mul(new(big.Rat).SetInt64(hold), big.NewRat(100, 105)))
	r := models.InclusiveAttempt{HoldMicros: hold, UpstreamMicros: estimate, BilledMicros: hold, Estimated: true}
	if u == nil || u.CostContested() || (u.CostDecimal == "" && !u.HasReportedCost()) {
		return r
	}
	decimal := u.CostDecimal
	if decimal == "" {
		decimal = strconv.FormatFloat(*u.Cost, 'g', -1, 64) // native usage only
	}
	up, billed, err := Charge(decimal)
	if err != nil {
		return r
	}
	r.UpstreamMicros, r.BilledMicros, r.Estimated, r.ProviderTotal = up, billed, false, decimal
	if billed > hold {
		r.Anomaly = "actual charge exceeds admission hold; actual settled, listing quarantined"
	}
	return r
}
