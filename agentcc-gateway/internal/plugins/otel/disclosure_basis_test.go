package otel

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestBodyCaptureDisclosureNamesMaskingBasis(t *testing.T) {
	var output bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&output, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })

	New(config.OTelConfig{Enabled: true, IncludeBodies: true, Exporter: "stdout"})

	var entry struct {
		Message string `json:"msg"`
	}
	if err := json.Unmarshal(output.Bytes(), &entry); err != nil {
		t.Fatalf("decode capture disclosure: %v", err)
	}
	const want = "otel body capture is enabled: prompts and completions go to the collector; opted-in MCP full-content capture exports tool inputs and outputs to the organization's collector. It masks secret-named fields and recognized Bearer, sk-, AIza, AKIA/ASIA and JWT formats. Prefix-less Future AGI keys can remain in other text; other sensitive content may still be exported"
	if entry.Message != want {
		t.Fatalf("capture disclosure = %q, want %q", entry.Message, want)
	}
}
