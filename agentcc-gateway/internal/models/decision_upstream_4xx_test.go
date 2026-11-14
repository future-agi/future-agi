package models

import (
	"net/http"
	"testing"
)

// A vendor 4xx the gateway does not map keeps its status as upstream_rejected
// with the vendor fallback message. The vendor's own text is not passed on: it
// could repeat the request, encoded in ways the safety check cannot see. Only
// 400 and 422 pass vendor text, as before.
func TestDecisionUpstreamUnmapped4xxPreservesStatusWithFallbackMessage(t *testing.T) {
	req, err := ParseDecisionRequest([]byte(`{"model":"jev-latest","state":"secret request CANARY text","questions":{"q":{"type":"noul"}}}`))
	if err != nil {
		t.Fatal(err)
	}
	const fallback = "Vendor rejected the request."
	for _, status := range []int{405, 409, 410, 415, 418, 451} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			for _, body := range []string{
				`{"error":{"message":"Unsupported request"}}`,
				`{"error":{"message":"bad field secret+request+CANARY+text"}}`,
			} {
				message := req.UpstreamErrorMessage(status, []byte(body), "synthetic-key-CANARY", fallback)
				if message != fallback {
					t.Errorf("%s: message = %q, want the fallback", body, message)
				}
				apiErr := DecisionUpstreamError("Vendor", status, nil, message)
				if apiErr.Status != status || apiErr.Type != ErrTypeInvalidRequest || apiErr.Code != "upstream_rejected" || apiErr.Message != fallback || apiErr.UpstreamStatus != status {
					t.Errorf("upstream %d: error = %+v", status, apiErr)
				}
			}
		})
	}
	// 400 and 422 still pass the vendor's text when it is safe.
	for _, status := range []int{400, 422} {
		if got := req.UpstreamErrorMessage(status, []byte(`{"error":{"message":"Unsupported request"}}`), "synthetic-key-CANARY", fallback); got != "Unsupported request" {
			t.Errorf("upstream %d: message = %q", status, got)
		}
	}
	for _, status := range []int{301, 500} {
		e := DecisionUpstreamError("Vendor", status, nil, fallback)
		if e.Status != 502 || e.Type != ErrTypeUpstream || e.Code != "upstream_error" {
			t.Errorf("upstream %d: error = %+v", status, e)
		}
	}
}
