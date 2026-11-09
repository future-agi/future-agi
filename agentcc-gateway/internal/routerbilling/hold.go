package routerbilling

import (
	"errors"
	"math/big"
)

var ErrBound = errors.New("router billing: missing, invalid or exceeded admission bound")

// Candidate is one immutable catalogue model/endpoint/tier card. Rates are USD
// decimals, not binary64. The caller supplies the entire catalogue universe,
// including every endpoint and tier, not just the router's empty endpoint list.
// A blank optional dimension means it is not charged. Unsupported dimensions
// must be rejected by the catalogue adapter, never dropped to construct a card.
type Candidate struct {
	ID, Prompt, Completion, CacheRead, CacheWrite, Reasoning string
	Request, Image, Search, Generation                       string
}

// Caps must describe the actual dispatched request. InputTokens is a proven
// upper bound, NOT the four-bytes-per-token estimate. Image/search/output counts
// must be enforced by the request adapter; this function cannot enforce them.
type Caps struct {
	InputTokens, MaxTokens, N, Images, Searches int
}

// Plan is an immutable per-attempt hold plan. Values are private so a caller
// cannot fabricate a plan by filling a struct with a smaller hold.
type Plan struct {
	listing, snapshot, maximizingCard string
	upstream, hold                    int64
	caps                              Caps
}

func (p Plan) Listing() string        { return p.listing }
func (p Plan) Snapshot() string       { return p.snapshot }
func (p Plan) MaximizingCard() string { return p.maximizingCard }
func (p Plan) HoldMicros() int64      { return p.hold }
func (p Plan) Caps() Caps             { return p.caps }

// FamilyMember does not make a model offerable: lifecycle, policy, durable
// ownership, and the upstream contract still have to pass admission.
func FamilyMember(id string) bool {
	switch id {
	case "openrouter/auto", "openrouter/auto-beta", "openrouter/pareto-code",
		"openrouter/bodybuilder", "typesafe/jev-router", "nvidia/switchyard":
		return true
	default:
		return false
	}
}

// Bound reserves the maximum two-stage-rounded card charge over the catalogue.
// A configured cap is a refusal threshold, NEVER a clamp that creates an
// underfunded hold. Dynamic parent/child operations (Fusion) are not candidates
// for this single-inclusive-charge contract.
func Bound(listing, snapshot string, expired bool, universe []Candidate, caps Caps, capMicros int64) (Plan, error) {
	if !FamilyMember(listing) || expired || snapshot == "" || len(universe) == 0 || capMicros <= 0 ||
		caps.InputTokens < 0 || caps.InputTokens > 10_000_000 || caps.MaxTokens <= 0 || caps.MaxTokens > 1_000_000 ||
		caps.N <= 0 || caps.N > 16 || caps.Images < 0 || caps.Images > 256 || caps.Searches < 0 || caps.Searches > 256 {
		return Plan{}, ErrBound
	}
	plan := Plan{listing: listing, snapshot: snapshot, caps: caps}
	for _, candidate := range universe {
		if candidate.ID == "" {
			return Plan{}, ErrBound
		}
		input, err := maxRate(candidate.Prompt, candidate.CacheRead, candidate.CacheWrite)
		if err != nil {
			return Plan{}, err
		}
		output, err := maxRate(candidate.Completion, candidate.Reasoning)
		if err != nil {
			return Plan{}, err
		}
		total := new(big.Rat).Mul(input, big.NewRat(int64(caps.InputTokens), 1))
		total.Add(total, new(big.Rat).Mul(output, big.NewRat(int64(caps.MaxTokens*caps.N), 1)))
		for _, line := range []struct {
			rate  string
			count int
		}{{candidate.Request, 1}, {candidate.Image, caps.Images}, {candidate.Search, caps.Searches}, {candidate.Generation, caps.N}} {
			rate, err := optionalRate(line.rate)
			if err != nil {
				return Plan{}, err
			}
			total.Add(total, new(big.Rat).Mul(rate, big.NewRat(int64(line.count), 1)))
		}
		upstream, billed, err := chargeRat(total)
		if err != nil || billed > capMicros {
			return Plan{}, ErrBound
		}
		if billed > plan.hold {
			plan.upstream, plan.hold, plan.maximizingCard = upstream, billed, candidate.ID
		}
	}
	if plan.hold <= 0 {
		return Plan{}, ErrBound // an all-zero universe cannot fund missing-cost liability
	}
	return plan, nil
}

func optionalRate(rate string) (*big.Rat, error) {
	if rate == "" {
		return new(big.Rat), nil
	}
	return decimal(rate)
}

func maxRate(required string, optional ...string) (*big.Rat, error) {
	value, err := decimal(required)
	if err != nil {
		return nil, err
	}
	for _, raw := range optional {
		rate, err := optionalRate(raw)
		if err != nil {
			return nil, err
		}
		if rate.Cmp(value) > 0 {
			value = rate
		}
	}
	return value, nil
}
