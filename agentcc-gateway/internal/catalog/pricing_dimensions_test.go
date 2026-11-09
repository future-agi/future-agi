package catalog

import (
	"fmt"
	"testing"
)

func TestCompletePricingDimensions(t *testing.T) {
	for _, tc := range []struct {
		name, price string
		billable    bool
	}{
		{"prompt-only", `{"prompt":"0.000002"}`, false},
		{"completion-only", `{"completion":"0.000010"}`, false},
		{"prompt-and-fee", `{"prompt":"0.000002","request":"0.04"}`, false},
		{"completion-and-fee", `{"completion":"0.000010","request":"0.04"}`, false},
		{"explicit-zero-output", `{"prompt":"0.000002","completion":"0"}`, true},
		{"explicit-zero-input", `{"prompt":"0","completion":"0.000010"}`, true},
		{"fee-only", `{"request":"0.04"}`, true},
		{"explicit-fee-only", `{"request":"0.04","prompt":"0","completion":"0"}`, true},
		{"unsupported-image", `{"prompt":"0.000002","completion":"0.000010","image":"0.01"}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m, err := ParseOpenRouter([]byte(fmt.Sprintf(`{"data":[{"id":"acme/paid","pricing":%s}]}`, tc.price)))
			if err != nil {
				t.Fatal(err)
			}
			if got := m["acme/paid"].ManagedBillable(SourceOpenRouter); got != tc.billable {
				t.Errorf("listing billable=%t want=%t", got, tc.billable)
			}
			eps, err := ParseOpenRouterEndpoints("acme/paid", []byte(fmt.Sprintf(`{"data":{"id":"acme/paid","endpoints":[{"tag":"solo","pricing":%s}]}}`, tc.price)))
			if err != nil || len(eps) != 1 {
				t.Fatalf("endpoints=%d err=%v", len(eps), err)
			}
			if got := eps[0].ManagedBillable("acme/paid"); got != tc.billable {
				t.Errorf("endpoint billable=%t want=%t", got, tc.billable)
			}
		})
	}
}
