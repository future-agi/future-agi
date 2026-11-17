package inclusive

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Independent integer-micro goldens: two candidates, the listed context bound,
// 64 output tokens per candidate, zero image occurrences. Expired cards remain
// useful arithmetic fixtures; this test does not grant lifecycle eligibility.
func TestR601PerIDExactAdmissionMicros(t *testing.T) {
	want := map[string]int64{
		"google/gemini-4-argon": 4405363, "google/gemini-4-argon:batch": 2202682,
		"google/gemini-3.8-flash": 1817212, "google/gemini-3.8-flash:batch": 826006,
		"google/gemini-3.7-flash": 1652011, "google/gemini-3.7-flash:batch": 826006,
		"google/gemini-3.6-flash": 1652011, "google/gemini-3.6-flash:batch": 826006,
		"google/gemini-3.5-flash-lite": 660939, "google/gemini-3.5-flash-lite:batch": 330470,
		"google/gemini-3-pro-image": 552115,
		"google/gemini-3.5-flash":   3304224, "google/gemini-3.5-flash:batch": 1652112,
		"google/gemini-3.1-flash-lite": 550704, "google/gemini-3.1-flash-lite:batch": 275352,
		"~google/gemini-pro-latest": 4405363, "~google/gemini-flash-latest": 1652011,
		"google/lyria-3-pro-preview": 168000, "google/lyria-3-clip-preview": 84000,
		"google/gemini-3.1-flash-lite-preview":      550704,
		"google/gemini-3.1-pro-preview-customtools": 4405632,
		"google/gemini-3.1-pro-preview":             4405632, "google/gemini-3.1-pro-preview:batch": 2202816,
		"google/gemini-3-flash-preview": 1101408, "google/gemini-3-flash-preview:batch": 550704,
		"google/gemini-3-pro-image-preview": 276864, "google/gemini-2.5-flash-image": 20980,
		"google/gemini-2.5-flash-lite": 220254, "google/gemini-2.5-flash-lite:batch": 110127,
		"google/gemini-2.5-flash": 660939, "google/gemini-2.5-flash:batch": 330470,
		"google/gemini-2.5-pro": 2753856, "google/gemini-2.5-pro:batch": 1376928,
		"google/gemini-2.5-pro-preview": 2753856,
	}
	all, _, err := catalog.BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	for id, expected := range want {
		t.Run(id, func(t *testing.T) {
			m := all[id]
			if m == nil {
				t.Fatal("listing missing")
			}
			m.InclusiveEnabled = true
			n, out := 2, 64
			hold, err := Bound(&models.ChatCompletionRequest{Model: id, N: &n, MaxTokens: &out}, m.InclusivePrice(catalog.SourceOpenRouter))
			if err != nil || hold != expected {
				t.Fatalf("hold=%d want=%d error=%v", hold, expected, err)
			}
		})
	}
}
