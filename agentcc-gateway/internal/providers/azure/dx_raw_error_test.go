package azure

import (
	"fmt"
	"testing"
)

// Raw HTTP refusal evidence is real, but the established public mapping stays
// 502, including raw 4xx. Do not confuse it with a synthetic gateway failure.
func TestDXRawHTTPErrorEvidence(t *testing.T) {
	for _, status := range []int{400, 401, 403, 404, 429, 500, 503} {
		for _, body := range []string{"synthetic refusal", "{", "{}"} {
			t.Run(fmt.Sprintf("%d/%s", status, body), func(t *testing.T) {
				e := parseProviderError(status, []byte(body))
				if e.Status != 502 || e.UpstreamStatus != status || e.ProviderStatus != status || e.Type != "upstream_error" || e.Code != fmt.Sprintf("provider_%d", status) || e.Message != fmt.Sprintf("provider error (HTTP %d): %s", status, body) {
					t.Fatalf("raw HTTP evidence/mapping: %+v", e)
				}
			})
		}
	}
}
