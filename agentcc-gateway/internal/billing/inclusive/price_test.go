package inclusive

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestR601TwoStageRounding(t *testing.T) {
	for _, tc := range []struct {
		raw        string
		up, billed int64
	}{
		{"0", 0, 0}, {"0.000009499999999999999", 9, 9}, {"0.0000095", 10, 11}, {"9.5e-6", 10, 11}, {"0.0000005", 1, 1}, {"0.08", 80000, 84000},
	} {
		u, b, e := Charge(tc.raw)
		if e != nil || u != tc.up || b != tc.billed {
			t.Errorf("%s = %d,%d,%v", tc.raw, u, b, e)
		}
	}
	for _, raw := range []string{"", "null", "-1", "NaN", "Inf", "1/2", "1e999999", "1000000000000000"} {
		if _, _, err := Charge(raw); err == nil {
			t.Errorf("accepted invalid cost %q", raw)
		}
	}
}

func TestR601ExactListingBounds(t *testing.T) {
	all, _, err := catalog.BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	for id, m := range all {
		m.InclusiveEnabled = true
		q := m.InclusivePrice(catalog.SourceOpenRouter)
		if q == nil {
			continue
		}
		count++
		t.Run(id, func(t *testing.T) {
			n, out := 2, 64
			r := &models.ChatCompletionRequest{Model: id, N: &n, MaxTokens: &out}
			hold, err := Bound(r, q)
			if err != nil || hold <= 0 {
				t.Fatalf("hold=%d err=%v", hold, err)
			}
			if q.Generation == "0.08" && hold != 168000 || q.Generation == "0.04" && hold != 84000 {
				t.Fatalf("Lyria bound=%d", hold)
			}
			for _, raw := range []string{"0.0000095", "0", "-1", "missing", "100"} {
				var usage models.Usage
				if raw != "missing" {
					if err := json.Unmarshal([]byte(`{"cost":`+raw+`}`), &usage); err != nil {
						t.Fatal(err)
					}
				}
				got := Price(&usage, hold)
				want := int64(11)
				switch raw {
				case "0":
					want = 0
				case "-1", "missing":
					want = hold
				case "100":
					want = 105000000
				}
				if got.BilledMicros != want {
					t.Fatalf("%s: %+v want=%d", raw, got, want)
				}
				if raw == "100" && got.Anomaly == "" {
					t.Fatal("overhold silently absorbed")
				}
			}
			if q.Generation == "" {
				r.Messages = []models.Message{{Role: "user", Content: json.RawMessage(`[{"type":"image_url","image_url":{"url":"https://synthetic.invalid/a"}},{"type":"image_url","image_url":{"url":"https://synthetic.invalid/a"}}]`)}}
				withImages, e := Bound(r, q)
				if e != nil || withImages < hold {
					t.Fatalf("image occurrence bound: %d < %d (%v)", withImages, hold, e)
				}
			}
			n = 9
			if _, e := Bound(r, q); e == nil {
				t.Fatal("unbounded generation count")
			}
		})
	}
	if count != 34 {
		t.Fatalf("priced families=%d want 32 image+2 Lyria", count)
	}
}

func TestR601UnsupportedBounds(t *testing.T) {
	q := &models.InclusivePrice{Model: "image", Prompt: "0.0000095", Completion: "0", Image: "0", ContextLimit: 1}
	out := 1
	r := &models.ChatCompletionRequest{Model: "image", MaxTokens: &out}
	hold, err := Bound(r, q)
	if err != nil || hold != 11 {
		t.Fatalf("half-micro hold=%d err=%v", hold, err)
	}
	r.MaxTokens = nil
	if _, err := Bound(r, q); err == nil {
		t.Fatal("uncapped output admitted")
	}
}
