package credentials

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// REST-02: an expired access token during a retryable provider outage is an
// availability error, not a new-consent demand, for either credential scope.
func TestWalkOAuthTransientRefreshDoesNotDemandConsent(t *testing.T) {
	for _, shared := range []bool{false, true} {
		name := "personal"
		if shared {
			name = "shared"
		}
		t.Run(name, func(t *testing.T) {
			d := newFakeDjango(t)
			clock := newTestClock()
			r, _ := testResolver(t, d, nil, "", clock)
			key := keyA1
			if shared {
				key = SharedKey("org-a", "issues")
			}
			d.refuse(key, "unavailable", "")
			resolve := func() *mcp.Denial {
				if shared {
					_, denial := r.resolveShared(context.Background(), "org-a", "issues")
					return denial
				}
				_, _, denial := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
				return denial
			}
			denial := resolve()
			if denial == nil || denial.Code != mcp.CodePolicyUnavailable {
				t.Fatalf("provider outage: got %v, want policy_unavailable without a sign-in link", denial)
			}
			d.give(key, "walk-test-recovered-not-real", clock.now().Add(time.Hour), 2)
			if denial = resolve(); denial != nil {
				t.Fatalf("recovery required new consent: %v", denial)
			}
		})
	}
}
