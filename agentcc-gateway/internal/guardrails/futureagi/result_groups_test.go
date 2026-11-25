package futureagi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Chat checks read every result group of a single-template response, as the
// per-template validation allows; only realtime checks require a single group.
func TestChatCheckReadsVerdictFromAnyResultGroup(t *testing.T) {
	for _, tc := range []struct {
		name, output string
		pass         bool
	}{{"passed", "Passed", true}, {"failed", "Failed", false}} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				json.NewEncoder(w).Encode(map[string]interface{}{"result": []interface{}{
					map[string]interface{}{"evaluations": []interface{}{}},
					map[string]interface{}{"evaluations": []interface{}{map[string]interface{}{"evalId": "tone", "output": tc.output}}},
				}})
			}))
			defer srv.Close()
			check := New("synthetic", map[string]interface{}{"base_url": srv.URL, "api_key": "synthetic", "secret_key": "synthetic", "eval_id": "tone"})
			res := check.Check(context.Background(), &guardrails.CheckInput{Request: &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic text"`)}}}})
			if res.Err != nil || res.Pass != tc.pass {
				t.Fatalf("err=%v pass=%v, want a %s verdict", res.Err, res.Pass, tc.name)
			}
		})
	}
}
