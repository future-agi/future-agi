package pipeline_test

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// A positive control pins byte-identical BYOK diagnostic formatting and the
// effective text policy, independently of W1's exact-value source scrub.
func TestOptBR5PipelineBYOKDiagnosticControl(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID, rc.Provider = "r5-control", "fixture"
	rc.ErrorTextRedactor = func(s string) string { return strings.ReplaceAll(s, "tenant-policy-token", "[tenant-policy]") }
	original := &models.APIError{Status: 400, ProviderStatus: 400, Message: "benign diagnostic tenant-policy-token", Code: "fixture", Type: "invalid_request_error"}
	original.BindPublicationCredentialOwner(models.CredentialBYOK)
	var buf bytes.Buffer
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, nil)))
	defer slog.SetDefault(old)
	got := pipeline.NewEngine().Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { return original })
	if got != original || rc.Errors[0] != original || original.Message != "benign diagnostic tenant-policy-token" {
		t.Fatal("mutated original BYOK error")
	}
	found := false
	for _, line := range strings.Split(buf.String(), "\n") {
		var row map[string]any
		if json.Unmarshal([]byte(line), &row) != nil || row["msg"] != "provider call failed" {
			continue
		}
		found = true
		if row["error"] != "fixture: benign diagnostic [tenant-policy]" {
			t.Errorf("BYOK diagnostic changed beyond configured redaction: %v", row["error"])
		}
	}
	if !found {
		t.Fatal("no pipeline warning")
	}
}
