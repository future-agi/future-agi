package routerbilling

import "testing"

func TestR602RouterGenerationTargetsBoundN(t *testing.T) {
	for _, id := range routers {
		t.Run(id, func(t *testing.T) {
			for n := 1; n <= 8; n++ {
				plan, err := Bound(id, "generation-snapshot", false, []Candidate{{ID: "synthetic/generation", Prompt: "0", Completion: "0", Generation: "0.08"}}, Caps{InputTokens: 1, MaxTokens: 1, N: n}, 1000000)
				if err != nil || plan.HoldMicros() != int64(n)*84000 {
					t.Fatalf("n=%d hold=%d err=%v", n, plan.HoldMicros(), err)
				}
			}
		})
	}
}
