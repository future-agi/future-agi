package catalog

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCAT2InclusiveOutputPermissionsFrozenWithListing(t *testing.T) {
	all, _, err := BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		id          string
		text, image bool
	}{
		{"google/gemini-3-pro-image", true, true},
		{"google/gemini-3.5-flash", true, false},
		{"google/lyria-3-pro-preview", false, false},
	} {
		t.Run(tc.id, func(t *testing.T) {
			m := all[tc.id]
			if m == nil || m.Inclusive == nil {
				t.Fatal("missing bundled inclusive listing")
			}
			q := *m.Inclusive
			if q.TextOutput != tc.text || q.ImageOutput != tc.image {
				t.Fatalf("card permissions: text=%t image=%t", q.TextOutput, q.ImageOutput)
			}
			card := models.PriceCard{Inclusive: &q}
			frozen := card.Clone()
			q.ImageOutput = !q.ImageOutput
			m.OutputModalities = []string{"audio"}
			if frozen.Inclusive.ImageOutput != tc.image || frozen.Inclusive.TextOutput != tc.text {
				t.Fatal("frozen card changed with the listing")
			}
			wire, err := json.Marshal(frozen.Inclusive)
			if err != nil {
				t.Fatal(err)
			}
			var restored models.InclusivePrice
			if err := json.Unmarshal(wire, &restored); err != nil || restored.ImageOutput != tc.image || restored.TextOutput != tc.text {
				t.Fatalf("output permissions lost on persistence: %+v %v", restored, err)
			}
		})
	}
}
