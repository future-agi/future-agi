package spendguard

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The spend guard guards the org's managed credit: a platform-funded call is
// neither refused nor counted, even while the org's managed spend is paused.
func TestSpendGuardSkipsAPlatformFundedCall(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	rdb.Set(bg, prefix+"pause:org-pf:managed_spend", "1", 0)
	g, _ := guardWith(store, config.SpendGuardConfig{}, nil)
	p := New(g, fixedEstimate(4_000))
	rc := managedRC("org-pf", "key_a", "pf-1", 10)
	rc.Metadata["key_billing"] = "platform_funded"
	if res := p.ProcessRequest(bg, rc); res.Error != nil {
		t.Fatalf("a platform-funded call was refused: %v", res.Error)
	}
	if n := rdb.Exists(bg, orgMinute(prefix, "org-pf", t0)).Val(); n != 0 {
		t.Fatal("a platform-funded call was counted")
	}
}
