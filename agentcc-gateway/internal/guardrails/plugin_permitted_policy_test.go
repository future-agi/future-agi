package guardrails

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
)

// The streaming checker takes the request's policy only from a key that may
// override; otherwise it applies the key's and org's policy (none here).
func TestPermittedRequestPolicy(t *testing.T) {
	cases := []struct {
		header  string
		allowed bool
		want    policy.RequestPolicy
	}{
		{"disabled", false, policy.RequestPolicyNone},
		{"log-only", false, policy.RequestPolicyNone},
		{"strict", false, policy.RequestPolicyNone},
		{"", false, policy.RequestPolicyNone},
		{"disabled", true, policy.RequestPolicyDisabled},
		{"log-only", true, policy.RequestPolicyLogOnly},
		{"strict", true, policy.RequestPolicyStrict},
		{"not-a-policy", true, policy.RequestPolicyNone},
		{"", true, policy.RequestPolicyNone},
	}
	for _, tc := range cases {
		rc := overrideRequest(tc.header, tc.allowed)
		if got := PermittedRequestPolicy(rc.Metadata); got != tc.want {
			t.Errorf("PermittedRequestPolicy(header %q, allowed %v) = %v, want %v", tc.header, tc.allowed, got, tc.want)
		}
		rc.Release()
	}
}
