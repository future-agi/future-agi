package webhook

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

func TestMissingWebhookDecisionIsOperational(t *testing.T) {
	for _, body := range []string{`{}`, `{"pass":null}`, `{"pass":"false"}`} {
		t.Run(body, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) }))
			defer s.Close()
			result := New("webhook", map[string]interface{}{"url": s.URL}).Check(context.Background(), &guardrails.CheckInput{})
			if result.Pass || !result.OperationalError {
				t.Fatalf("invalid decision classified as content: %+v", result)
			}
		})
	}
}
