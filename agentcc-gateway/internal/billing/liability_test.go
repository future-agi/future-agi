package billing

import "testing"

func TestOwnedR7LiabilityTable(t *testing.T) {
	known := &Liability{UpstreamMicros: 10, BilledMicros: 11}
	zero := &Liability{}
	for _, tc := range []struct {
		name                           string
		dispatched, rejected, evidence bool
		reported                       *Liability
		want                           int64
		estimated, released            bool
	}{
		{"undispatched", false, false, false, nil, 0, false, true},
		{"provider-rejected-before-generation", true, true, false, nil, 0, false, true},
		{"provider-rejected-with-numeric-zero", true, true, true, zero, 0, false, false},
		{"reported", true, false, true, known, 11, false, false},
		{"billable-provider-error", true, true, true, known, 11, false, false},
		{"generation-without-total", true, true, true, nil, 41580, true, false},
		{"gateway-synthesized-error", true, false, false, nil, 41580, true, false},
		{"unresolved-dispatch", true, false, false, nil, 41580, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := ResolveLiability(tc.dispatched, tc.rejected, tc.evidence, tc.reported, 41580)
			if got.BilledMicros != tc.want || got.Estimated != tc.estimated || got.Released != tc.released {
				t.Fatalf("resolution=%+v want=%d estimated=%t released=%t", got, tc.want, tc.estimated, tc.released)
			}
		})
	}
}
