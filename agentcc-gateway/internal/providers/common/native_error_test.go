package common

import (
	"testing"
)

func TestNativeHTTPErrorPreservesOnlyRealEvidence(t *testing.T) {
	for _, tc := range []struct {
		name, body, typ, code, message string
		status, wantStatus             int
	}{
		{"typed", `{"error":{"type":"rate_limit_error","code":"quota","message":"slow down"}}`, "rate_limit_error", "quota", "slow down", 429, 429},
		{"google", `{"error":{"code":403,"status":"PERMISSION_DENIED","message":"bad credential"}}`, "PERMISSION_DENIED", "", "bad credential", 403, 403},
		{"untyped", `{"error":{"message":"unavailable"}}`, "", "", "unavailable", 500, 502},
		{"raw", "temporarily unavailable", "", "", "temporarily unavailable", 503, 502},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NativeHTTPError(tc.status, []byte(tc.body))
			if e.Status != tc.wantStatus || e.ProviderStatus != tc.status || e.UpstreamStatus != tc.status || e.Type != tc.typ || e.Code != tc.code || e.Message != tc.message {
				t.Fatalf("error = %+v", e)
			}
		})
	}
}
