package models

import (
	"net/http"
	"testing"
)

func TestDecisionUpstreamUnmapped4xxPreservesStatusAndSafeMessage(t *testing.T) {
	req, err := ParseDecisionRequest([]byte(`{"model":"jev-latest","state":"private-state-CANARY","questions":{"q":{"type":"noul"}}}`))
	if err != nil {
		t.Fatal(err)
	}
	for _, status := range []int{405, 409, 410, 415, 418, 451} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			message := req.UpstreamErrorMessage(status, []byte(`{"error":{"message":"Unsupported request"}}`), "synthetic-key-CANARY", "Vendor rejected the request.")
			if message != "Unsupported request" {
				t.Errorf("safe upstream message = %q", message)
			}
			apiErr := DecisionUpstreamError("Vendor", status, nil, message)
			if apiErr.Status != status || apiErr.Type != ErrTypeInvalidRequest || apiErr.Code != "upstream_rejected" || apiErr.Message != message || apiErr.UpstreamStatus != status {
				t.Errorf("upstream %d: error = %+v", status, apiErr)
			}
			fallback := req.UpstreamErrorMessage(status, []byte(`{"error":{"message":"private-state-CANARY"}}`), "synthetic-key-CANARY", "Vendor rejected the request.")
			if fallback != "Vendor rejected the request." {
				t.Errorf("unsafe message = %q", fallback)
			}
		})
	}
	for _, status := range []int{301, 500} {
		e := DecisionUpstreamError("Vendor", status, nil, "Vendor rejected the request.")
		if e.Status != 502 || e.Type != ErrTypeUpstream || e.Code != "upstream_error" {
			t.Errorf("upstream %d: error = %+v", status, e)
		}
	}
}
