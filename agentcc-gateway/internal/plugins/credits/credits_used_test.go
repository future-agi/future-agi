package credits

import (
	"context"
	"fmt"
	"testing"
)

type fakeRedisCredit struct{ balance, deducted int64 }

func (f *fakeRedisCredit) DeductMicros(_ string, amount int64) (int64, bool) {
	f.deducted += amount
	f.balance -= amount
	return f.balance, true
}
func (f *fakeRedisCredit) ForceDeductMicros(id string, amount int64) (int64, bool) {
	return f.DeductMicros(id, amount)
}
func (f *fakeRedisCredit) GetBalance(string) (int64, bool) { return f.balance, true }
func (f *fakeRedisCredit) Available() bool                 { return true }

// x-agentcc-credits-used reports the micro-dollars actually deducted: a cost
// below half a micro-dollar is deducted as 1, so the header says 0.000001,
// not 0.000000. Both the local and the Redis-backed paths.
func TestCreditsUsedReportsTheDeductedMicros(t *testing.T) {
	for _, tc := range []struct {
		cost string
		want string
	}{
		{"0.0000004", "0.000001"},
		{"0.0000014", "0.000001"},
		{"0.0000015", "0.000002"},
		{"0.005", "0.005000"},
	} {
		for _, backend := range []string{"local", "redis"} {
			t.Run(fmt.Sprintf("%s/%s", tc.cost, backend), func(t *testing.T) {
				ks := newKeyStore()
				managed := findKey(ks, "managed")
				before := managed.BalanceMicros()
				p := New(true, ks)
				fake := &fakeRedisCredit{balance: 10_000_000}
				if backend == "redis" {
					p.SetRedisCredit(fake)
				}
				rc := makeRC(managed.ID, "managed")
				defer rc.Release()
				rc.Metadata["cost"] = tc.cost
				p.ProcessResponse(context.Background(), rc)
				if got := rc.Metadata["credits_used"]; got != tc.want {
					t.Fatalf("credits_used = %q for cost %s; want %q", got, tc.cost, tc.want)
				}
				deducted := before - managed.BalanceMicros()
				if backend == "redis" {
					deducted = fake.deducted
				}
				if got := fmt.Sprintf("%.6f", float64(deducted)/1_000_000); got != tc.want {
					t.Fatalf("deducted %d micros, header says %s", deducted, tc.want)
				}
			})
		}
	}
}
