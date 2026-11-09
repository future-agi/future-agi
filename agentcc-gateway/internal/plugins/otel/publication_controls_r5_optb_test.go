package otel

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
)

func TestOptBR5OTelBYOKDiagnosticControl(t *testing.T) {
	rc := newTestRC()
	rc.ErrorTextRedactor = func(s string) string { return strings.ReplaceAll(s, "tenant-policy-token", "[tenant-policy]") }
	original := &models.APIError{Status: 400, ProviderStatus: 400, Message: "benign diagnostic tenant-policy-token", Code: "fixture", Type: "invalid_request_error"}
	original.BindPublicationCredentialOwner(models.CredentialBYOK)
	rc.Errors = []error{original}
	var buf bytes.Buffer
	p := NewWithExporter(otelpkg.NewStdoutExporter(&buf), 1, true)
	p.includeBodies = false
	p.ProcessRequest(context.Background(), rc)
	p.ProcessResponse(context.Background(), rc)
	var span otelpkg.Span
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &span); err != nil {
		t.Fatal(err)
	}
	if span.Status != "ERROR" || span.Attributes["error.message"] != "fixture: benign diagnostic [tenant-policy]" {
		t.Fatalf("unexpected BYOK publication: %v", span.Attributes["error.message"])
	}
	if rc.Errors[0] != original || original.Message != "benign diagnostic tenant-policy-token" {
		t.Fatal("export mutated original BYOK error")
	}
}
