package server

import (
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"strconv"
	"testing"
)

// Money-pair addendum 9: a credit-funded native call with incomplete usage
// must consume its full admission estimate (or fence), not release the hold
// for a zero/partial charge. Complete and explicit-zero evidence stay exact.
func TestUsagePresenceNativeFullHold(t *testing.T) {
	for _, a := range upInt2Adapters[2:] {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, c := range upInt2Cases {
					usage, ok := c.native[a.name]
					if !ok {
						continue
					}
					t.Run(fmt.Sprintf("%s/%s/stream=%t/%s", a.name, upInt2SurfaceName(surface), stream, c.name), func(t *testing.T) {
						r, _ := presenceRun(t, a, surface, stream, true, usage)
						upInt2Served(t, "native", r)
						rec := r.records[0]
						var attempts []billing.AttemptSummary
						if err := json.Unmarshal([]byte(rec["attempts"]), &attempts); err != nil || len(attempts) != 1 || attempts[0].BilledMicros != r.spent {
							t.Fatalf("attempt debit does not reconcile: %v (%v)", attempts, err)
						}
						upstream, err := strconv.ParseInt(rec["upstream_micros"], 10, 64)
						if err != nil {
							t.Fatal(err)
						}
						markup, err := strconv.ParseInt(rec["markup_micros"], 10, 64)
						if err != nil || upstream+markup != r.spent || attempts[0].UpstreamMicros != upstream {
							t.Fatalf("request and attempt money do not reconcile: %v", rec)
						}
						hold, err := strconv.ParseInt(rec["hold_micros"], 10, 64)
						if err != nil || hold <= 0 {
							t.Fatalf("invalid admission: %v", rec)
						}
						switch c.name {
						case "complete":
							if r.spent != 74 || rec["usage_estimated"] != "0" {
								t.Fatalf("complete: %v", rec)
							}
						case "explicit-zero":
							if r.spent != 0 || rec["kind"] != "void" || rec["usage_estimated"] != "0" {
								t.Fatalf("zero: %v", rec)
							}
						default:
							// Messages requests cap output at 64, so the measured
							// 1000-input/500-output fixtures exceed that hold. The
							// floor must never cap the stronger token evidence.
							oracle, _ := presenceRun(t, upInt2Adapters[0], surface, stream, true, c.oracle)
							want := max(hold, oracle.spent)
							if r.spent != want || rec["kind"] != "spend" || rec["usage_estimated"] != "1" {
								t.Errorf("native incomplete metering settled %d, want %d (hold=%d, token evidence=%d), kind=%s estimated=%s", r.spent, want, hold, oracle.spent, rec["kind"], rec["usage_estimated"])
							}
						}
					})
				}
			}
		}
	}
}
