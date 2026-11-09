// Package dimensional implements an opt-in flat-unit meter. It does not qualify
// catalogue prices: the caller must possess a versioned provider contract that
// defines non-overlapping units and enforceable request bounds. No bundled
// Gemini or Lyria listing currently supplies that contract.
package dimensional

import (
	"fmt"
	"math/big"
	"regexp"
	"sync/atomic"
)

// Unit names state the partition explicitly. Cached input is NOT also uncached
// input; reasoning is NOT also text output. A bare catalogue "image" quote is
// deliberately not a Unit. Resolution/image-token tariffs need another meter.
type Unit string

const (
	InputToken  Unit = "uncached_input_token"
	CacheRead   Unit = "cache_read_token"
	CacheWrite  Unit = "cache_write_token"
	OutputToken Unit = "text_output_token"
	Reasoning   Unit = "reasoning_token"
	InputImage  Unit = "input_image"
	OutputImage Unit = "output_image"
	Song        Unit = "song"
	Clip        Unit = "clip"
	Search      Unit = "search_query"
	Request     Unit = "request"
)

var units = [...]Unit{InputToken, CacheRead, CacheWrite, OutputToken, Reasoning, InputImage, OutputImage, Song, Clip, Search, Request}

// Identity binds a quote and later evidence to one exact model/endpoint. Moving
// aliases, variants and snapshots are not canonicalised or guessed.
type Identity struct {
	Model    string `json:"model"`
	Endpoint string `json:"endpoint"`
	Snapshot string `json:"snapshot"`
}

// Card is input to admission, copied privately by Admit. Rates are exact USD
// decimals per unit. Explicit zero differs from an omitted/unknown rate.
type Card struct {
	Identity Identity
	Contract string
	Rates    map[Unit]string
}

// BoundRequest is an adapter-certified envelope of the translated request that
// will actually be dispatched. Caps must be enforced by that adapter/provider;
// callers cannot turn an arbitrary user hint or a typical estimate into a cap.
// Every priced unit needs a cap, including explicit zero for inapplicable units.
type BoundRequest struct {
	Identity Identity
	Caps     map[Unit]int64
}

// Plan is immutable after admission, including its decimal rates and caps.
type Plan struct {
	identity   Identity
	contract   string
	lines      []line
	hold       int64
	dispatched atomic.Bool
}
type line struct {
	unit Unit
	rate string
	cap  int64
}

var decimal = regexp.MustCompile(`^(0|[1-9][0-9]*)(\.[0-9]+)?$`)

func amount(s string) (*big.Rat, error) {
	if len(s) > 64 || !decimal.MatchString(s) {
		return nil, fmt.Errorf("invalid nonnegative decimal amount")
	}
	r, ok := new(big.Rat).SetString(s)
	if !ok {
		return nil, fmt.Errorf("invalid decimal amount")
	}
	return r, nil
}

func known(u Unit) bool {
	for _, v := range units {
		if v == u {
			return true
		}
	}
	return false
}

// Admit rejects missing, negative or unknown quotes and bounds, including an
// all-zero paid card. The hold covers the full rounded upstream amount plus the
// 500 bps fee, with ceiling rounding. It is never a token-only estimate.
func Admit(card Card, request BoundRequest) (*Plan, error) {
	if card.Identity.Model == "" || card.Identity.Endpoint == "" || card.Identity.Snapshot == "" || card.Contract == "" || card.Identity != request.Identity {
		return nil, fmt.Errorf("missing or mismatched admitted identity/contract")
	}
	if len(card.Rates) == 0 {
		return nil, fmt.Errorf("missing dimensional prices")
	}
	for u := range card.Rates {
		if !known(u) {
			return nil, fmt.Errorf("unknown priced unit %q", u)
		}
	}
	for u, cap := range request.Caps {
		if !known(u) || cap < 0 {
			return nil, fmt.Errorf("invalid bounded unit %q", u)
		}
		if _, ok := card.Rates[u]; !ok {
			return nil, fmt.Errorf("unquoted bounded unit %q", u)
		}
	}
	p := &Plan{identity: card.Identity, contract: card.Contract}
	maxUSD := new(big.Rat)
	positive := false
	for _, u := range units {
		s, priced := card.Rates[u]
		if !priced {
			continue
		}
		r, err := amount(s)
		if err != nil {
			return nil, fmt.Errorf("unit %s: %w", u, err)
		}
		cap, bounded := request.Caps[u]
		if !bounded || cap < 0 || cap > 1_000_000_000 {
			return nil, fmt.Errorf("unit %s has no supported finite cap", u)
		}
		positive = positive || r.Sign() > 0
		maxUSD.Add(maxUSD, new(big.Rat).Mul(r, new(big.Rat).SetInt64(cap)))
		p.lines = append(p.lines, line{u, s, cap})
	}
	if !positive {
		return nil, fmt.Errorf("all-zero dimensional card is not a free-offer identity")
	}
	up, err := micros(maxUSD, true)
	if err != nil {
		return nil, err
	}
	p.hold, err = fee(up, true)
	if err != nil {
		return nil, err
	}
	if p.hold == 0 {
		return nil, fmt.Errorf("paid envelope has no positive bounded work")
	}
	return p, nil
}

func (p *Plan) HoldMicros() int64  { return p.hold }
func (p *Plan) Identity() Identity { return p.identity }

// Round the entire attempt once, not each dimensional line. Conservative
// estimates ceil; measured amounts round half-up. No float arithmetic.
func micros(usd *big.Rat, ceil bool) (int64, error) {
	return rounded(new(big.Rat).Mul(usd, big.NewRat(1_000_000, 1)), ceil)
}
func fee(up int64, ceil bool) (int64, error) {
	return rounded(new(big.Rat).Mul(new(big.Rat).SetInt64(up), big.NewRat(105, 100)), ceil)
}
func rounded(r *big.Rat, ceil bool) (int64, error) {
	q, rem := new(big.Int), new(big.Int)
	q.QuoRem(r.Num(), r.Denom(), rem)
	if (ceil && rem.Sign() > 0) || (!ceil && new(big.Int).Lsh(rem, 1).Cmp(r.Denom()) >= 0) {
		q.Add(q, big.NewInt(1))
	}
	if !q.IsInt64() || q.Sign() < 0 || q.Int64() > 1<<60 {
		return 0, fmt.Errorf("dimensional amount exceeds supported money range")
	}
	return q.Int64(), nil
}
