package logging

import (
	"errors"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestW4GatewayErrorSurvivesFlusher(t *testing.T) {
	for _, tc := range []struct {
		name string
		err  error
		keep bool
	}{
		{"budget", models.ErrGatewayRefusal(429, models.ErrTypeRateLimit, "budget_exceeded", "gateway budget explanation"), true},
		{"empty stream", models.ErrGatewayUpstream("gateway stream explanation"), true},
		{"provider lookalike", models.ErrUpstreamProvider(429, "gateway budget explanation"), false},
		{"untyped", errors.New("gateway budget explanation"), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "w4"
			rc.Errors = []error{tc.err}
			rec := TraceRecord{RequestID: "w4", StatusCode: 429, ErrorMessage: tc.err.Error()}
			p := &Plugin{}
			p.redactPublishedError(&rec, rc, models.ContentModeFull)
			entry := buildLogEntryV1(rec)
			if strings.Contains(entry.ErrorMessage, "explanation") != tc.keep {
				t.Fatalf("flusher text=%q keep=%t", entry.ErrorMessage, tc.keep)
			}
			if tc.keep {
				rec.ErrorMessage = "changed diagnostic"
				if strings.Contains(buildLogEntryV1(rec).ErrorMessage, "changed diagnostic") {
					t.Fatal("modified record inherited origin")
				}
			}
			p.redactPublishedError(&rec, rc, models.ContentModeNone)
			if buildLogEntryV1(rec).ErrorMessage != "" {
				t.Fatal("none published error text")
			}
		})
	}
}
