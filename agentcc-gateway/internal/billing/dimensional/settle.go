package dimensional

import (
	"fmt"
	"math/big"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// Evidence is supplied by a qualified adapter, not by a caller. Quantities are
// cumulative for one attempt and use the contract's non-overlapping partition.
// InclusiveTotal is optional; an explicit zero is different from missing.
type Evidence struct {
	Identity       Identity
	Quantities     map[Unit]int64
	InclusiveTotal *string
}

type Charge struct {
	UpstreamMicros int64
	BilledMicros   int64
	Estimated      bool
	Receipt        billing.DimensionalReceipt
}

// Price uses an inclusive total OR dimensions, never both. Missing evidence is
// charged at its admitted bound and labelled estimated, not invented actual
// usage. A missing identity estimates the whole envelope; a contradictory
// identity or an over-cap measurement is an error, not a cheaper price.
func (p *Plan) Price(e Evidence) (Charge, error) {
	var c Charge
	if e.Identity != (Identity{}) && e.Identity != p.identity {
		return c, fmt.Errorf("served identity is outside the admitted snapshot")
	}
	identityKnown := e.Identity == p.identity
	for u, n := range e.Quantities {
		found := false
		for _, l := range p.lines {
			if l.unit == u {
				found = true
				if n < 0 || n > l.cap {
					return c, fmt.Errorf("unit %s is outside its admitted cap", u)
				}
			}
		}
		if !found {
			return c, fmt.Errorf("unadmitted evidence unit %s", u)
		}
	}
	var total *big.Rat
	if e.InclusiveTotal != nil {
		var err error
		total, err = amount(*e.InclusiveTotal)
		if err != nil {
			return c, err
		}
	}
	c.Receipt = billing.DimensionalReceipt{Contract: p.contract, Model: p.identity.Model, Endpoint: p.identity.Endpoint, Snapshot: p.identity.Snapshot, State: "measured"}
	// Without identity, even a monetary total cannot establish that the quoted
	// contract served. Retain a conservative estimate, never claim it measured.
	covered := total != nil && identityKnown
	usd := new(big.Rat)
	for _, l := range p.lines {
		count, measured := e.Quantities[l.unit]
		measured = measured && identityKnown
		item := billing.DimensionalLine{Unit: string(l.unit), RateUSD: l.rate, Cap: l.cap, CoveredByTotal: covered}
		if measured {
			item.Quantity = &count
		}
		if !covered {
			if !measured {
				count = l.cap
				item.Estimated = true
				c.Estimated = true
			}
			rate, _ := amount(l.rate) // validated and privately copied at Admit
			usd.Add(usd, new(big.Rat).Mul(rate, new(big.Rat).SetInt64(count)))
		}
		c.Receipt.Lines = append(c.Receipt.Lines, item)
	}
	if covered {
		usd = total
		copyTotal := *e.InclusiveTotal
		c.Receipt.ProviderTotal = &copyTotal
		c.Receipt.State = "provider_total"
	} else if c.Estimated {
		c.Receipt.State = "estimated_bound"
	}
	var err error
	c.UpstreamMicros, err = micros(usd, c.Estimated)
	if err != nil {
		return Charge{}, err
	}
	c.BilledMicros, err = fee(c.UpstreamMicros, c.Estimated)
	if err != nil {
		return Charge{}, err
	}
	if c.BilledMicros > p.hold {
		return Charge{}, fmt.Errorf("measured charge exceeds admitted hold")
	}
	// Missing positive work must never become an accidental free generation.
	if c.Estimated && c.BilledMicros == 0 {
		return Charge{}, fmt.Errorf("missing evidence has no positive conservative charge")
	}
	return c, nil
}
