package billing

import (
	"bytes"
	"encoding/json"
	"os/exec"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The Python arithmetic takes the estimator's actual token counts, including
// tools/response-format overhead. Do not substitute model list prices or round
// individual components: Go rounds once, after the markup.
func TestWorstCaseScriptParity(t *testing.T) {
	type vector struct {
		Input           int              `json:"input_tokens"`
		Output          int              `json:"output_tokens"`
		Price           models.PriceCard `json:"price"`
		Markup          int              `json:"markup_bps"`
		UnpricedReserve *float64         `json:"unpriced_reserve_usd,omitempty"`
	}
	var vectors []vector
	var want []int64
	for _, price := range []models.PriceCard{
		{Prompt: 3e-6, Completion: 15e-6, Request: 0.0001},
		{Prompt: 0.00015, Completion: 0.0006},
		{Request: 0.0000005}, {}, {Completion: 1e12},
	} {
		for _, markup := range []int{-1, 0, 500, 1000} {
			for _, out := range []int{0, 1, 4096, 100000} {
				req := helloRequest(intp(out))
				req.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "synthetic_lookup", Description: "a tool adds input"}}}
				def := DefaultMaxOut(2000, 4096)
				vectors = append(vectors, vector{Input: EstimateInputTokens(req), Output: OutputCap(req, def), Price: price, Markup: markup})
				want = append(want, EstimateWorstCaseMicros(req, price, markup, def))
			}
		}
	}
	for _, reserve := range []float64{0, 0.1, 0.0000005, 0.0000004999996, 0.1234567} {
		vectors = append(vectors, vector{UnpricedReserve: &reserve})
		want = append(want, USDToMicros(reserve))
	}
	input, err := json.Marshal(vectors)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-I", "../../scripts/worst_case_hold.py", "--estimate-json")
	cmd.Stdin = bytes.NewReader(input)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("worst-case script failed: %v\n%s", err, out)
	}
	var got []int64
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != len(want) {
		t.Fatalf("got %d rows, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("vector %d %+v: Python %d != Go %d", i, vectors[i], got[i], want[i])
		}
	}
}
