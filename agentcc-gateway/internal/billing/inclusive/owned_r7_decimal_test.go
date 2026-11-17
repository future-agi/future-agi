package inclusive

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOwnedR7ExactWireDecimals(t *testing.T) {
	for _, raw := range []string{
		"0.00000949999999999999999",
		"0.00000949999999999999999e+000",
		"0.0000094" + strings.Repeat("9", 100),
		"949999999999999999e-" + strings.Repeat("0", 100) + "23",
	} {
		t.Run(raw, func(t *testing.T) {
			up, billed, err := Charge(raw)
			if err != nil || up != 9 || billed != 9 {
				t.Fatalf("charge=(%d,%d,%v) want exact (9,9)", up, billed, err)
			}
			var u models.Usage
			if err := json.Unmarshal([]byte(`{"cost":`+raw+`}`), &u); err != nil {
				t.Fatal(err)
			}
			got := Price(&u, 84000)
			if got.Estimated || got.UpstreamMicros != 9 || got.BilledMicros != 9 || got.ProviderTotal != raw {
				t.Fatalf("catalogue total=%+v", got)
			}
		})
	}
	for _, raw := range []string{"-0e999999999999999999999", "1e-999999999999999999999"} {
		if up, billed, err := Charge(raw); err != nil || up != 0 || billed != 0 {
			t.Fatalf("exact zero-range charge=(%d,%d,%v)", up, billed, err)
		}
	}
}
