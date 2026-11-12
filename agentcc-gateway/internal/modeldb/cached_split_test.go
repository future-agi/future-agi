package modeldb

import "testing"

// A cached count only ever lowers a bill. Checked over every priced chat
// entry of the bundled table, at the table's own rate and at both dated rows.
func TestCachedSplitNeverRaisesABill(t *testing.T) {
	db := New(BundledModels, nil)

	type usage struct{ prompt, cached, completion int }
	usages := []usage{
		{0, 0, 0},
		{1, 1, 0},
		{1000, 0, 500},
		{1000, 1, 500},
		{1000, 500, 500},
		{1000, 999, 500},
		{1000, 1000, 500},
		{1000, 1001, 500},
		{1_000_000, 750_000, 100_000},
		{10339, 10318, 60},
		{2006, 1920, 300},
	}

	for _, m := range db.ListByMode(ModeChat) {
		pricings := []struct {
			name string
			p    PricingInfo
		}{
			{"table", m.Pricing},
			{"2026", m.PricingAt(t26)},
			{"2027", m.PricingAt(t27)},
		}
		for _, pr := range pricings {
			p := pr.p
			if p.InputPerToken <= 0 {
				continue
			}
			for _, u := range usages {
				got := p.Calculate(u.prompt, u.completion, CostOptions{CachedTokens: u.cached})
				old := p.Calculate(u.prompt, u.completion, CostOptions{})
				where := func() string {
					return m.ID + " at " + pr.name
				}
				if got > old {
					t.Errorf("rose: %s prompt=%d cached=%d completion=%d: %g > %g", where(), u.prompt, u.cached, u.completion, got, old)
				}
				if got < 0 {
					t.Errorf("negative: %s prompt=%d cached=%d completion=%d: %g", where(), u.prompt, u.cached, u.completion, got)
				}
				lowerRate := p.CachedInputPerToken > 0 && p.CachedInputPerToken < p.InputPerToken
				if u.cached > 0 && u.cached <= u.prompt && lowerRate {
					if got >= old {
						t.Errorf("not lower: %s prompt=%d cached=%d completion=%d: %g >= %g", where(), u.prompt, u.cached, u.completion, got, old)
					}
				} else if got != old {
					t.Errorf("changed: %s prompt=%d cached=%d completion=%d: %g != %g", where(), u.prompt, u.cached, u.completion, got, old)
				}
			}
		}
	}
}
