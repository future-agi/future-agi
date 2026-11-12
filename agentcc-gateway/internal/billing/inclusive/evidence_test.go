package inclusive

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestR601TerminalInvalidCostCannotBorrowEarlierTotal(t *testing.T) {
	var previous models.Usage
	if err := json.Unmarshal([]byte(`{"cost":0.0000095}`), &previous); err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{`{"cost":null}`, `{"cost":-1}`, `{}`} {
		var next models.Usage
		if err := json.Unmarshal([]byte(body), &next); err != nil {
			t.Fatal(err)
		}
		got := Price(models.MergeUsage(&previous, &next), 84000)
		want := int64(84000)
		if body == `{}` {
			want = 11
		}
		if got.BilledMicros != want {
			t.Errorf("%s charge=%+v want=%d", body, got, want)
		}
	}
}

func TestR601AttemptQuoteIsImmutable(t *testing.T) {
	q := &models.InclusivePrice{Model: "quoted", Prompt: "0.000001"}
	state := models.BillingState{ReservePrice: &models.PriceCard{Inclusive: q}, InclusiveHoldMicros: 105}
	a := models.NewAttemptPricing(&state)
	cloned := state.Clone()
	q.Prompt = "100"
	state.InclusiveHoldMicros = 1000000
	if a.ReservePrice.Inclusive.Prompt != "0.000001" || a.InclusiveHoldMicros != 105 || cloned.ReservePrice.Inclusive.Prompt != "0.000001" {
		t.Fatal("mutable quote escaped into attempt snapshot")
	}
}
