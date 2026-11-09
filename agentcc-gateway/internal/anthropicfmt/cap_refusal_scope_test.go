package anthropicfmt

import (
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	contract "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCapRefusalMetadataScope(t *testing.T) {
	usd := 0.000001
	identity := contract.AdmissionCapRefusalMetadata{CapScope: "agent", CapRef: "worker", CapKind: "daily", PolicyRevision: 7}
	for _, tc := range []struct {
		name     string
		metadata any
		cap      bool
	}{
		{"none", nil, false},
		{"credits", models.PaymentMeta{RequiredUSD: &usd, AvailableUSD: &usd}, false},
		{"untyped", map[string]any{"cap_scope": "agent", "cap_ref": "worker"}, false},
		{"admission", identity, true},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(tc.name+"/stream="+map[bool]string{false: "false", true: "true"}[stream], func(t *testing.T) {
				err := &models.APIError{Status: 402, Code: "managed_credits_insufficient", Message: "synthetic refusal", Metadata: tc.metadata}
				w := httptest.NewRecorder()
				WriteAPIError(w, err)
				data := w.Body.Bytes()
				if stream {
					frame := string(StreamErrorEvent(err))
					if !strings.HasPrefix(frame, "event: error\ndata: ") || !strings.HasSuffix(frame, "\n\n") {
						t.Fatalf("invalid SSE frame: %q", frame)
					}
					data = []byte(strings.TrimSuffix(strings.TrimPrefix(frame, "event: error\ndata: "), "\n\n"))
				}
				var body map[string]any
				if e := json.Unmarshal(data, &body); e != nil {
					t.Fatal(e)
				}
				want := map[string]any{"type": "api_error", "message": err.Message}
				if tc.cap {
					want["code"] = err.Code
					want["metadata"] = map[string]any{"cap_scope": "agent", "cap_ref": "worker", "cap_kind": "daily", "policy_revision": float64(7)}
				}
				if !reflect.DeepEqual(body, map[string]any{"type": "error", "error": want}) {
					t.Fatalf("wrong Messages shape: %s", data)
				}
			})
		}
	}
}
