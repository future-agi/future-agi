package main

import "testing"

func TestMockAuthSelectionIsOnlyTheReviewedBearerOrOAuth(t *testing.T) {
	const bearer = "bearer:e2e-mcp-upstream-bearer-not-a-secret"
	for value, want := range map[string]string{"": bearer, bearer: bearer, "oauth": "oauth"} {
		got, err := selectedMockAuth(value)
		if err != nil || got != want {
			t.Fatalf("reviewed mock auth selection failed: %v", err)
		}
	}
	for _, value := range []string{"none", "bearer:arbitrary", "OAuth", "oauth ", "static"} {
		if _, err := selectedMockAuth(value); err == nil {
			t.Error("unreviewed mock authentication mode was accepted")
		}
	}
}
