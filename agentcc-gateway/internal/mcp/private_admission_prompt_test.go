package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAdmissionPromptShapeBounds(t *testing.T) {
	for _, raw := range []string{
		`{"name":"p","arguments":{"x":1}}`,
		`{"name":"p","arguments":{"x":{"nested":"v"}}}`,
		`{"name":"p","arguments":{"x":"a","x":"b"}}`,
		`{"name":"p","arguments":null}`,
		`{"name":"p","arguments":{},"phase":"INITIAL"}`,
		`{"name":"p","arguments":{"` + strings.Repeat("n", 129) + `":"v"}}`,
		`{"name":"p","arguments":{"x":"` + strings.Repeat("v", 4097) + `"}}`,
	} {
		if _, _, _, err := admissionPromptShape(json.RawMessage(raw)); err != errAdmissionDenied {
			t.Fatal("invalid prompt shape accepted")
		}
	}
	args := map[string]string{}
	for i := 0; i < 33; i++ {
		args[string(rune('a'+i))] = "v"
	}
	for _, values := range []map[string]string{args, {"a": strings.Repeat("v", 4096), "b": strings.Repeat("v", 4096), "c": strings.Repeat("v", 4096), "d": strings.Repeat("v", 4096)}} {
		raw, _ := json.Marshal(map[string]any{"name": "p", "arguments": values})
		if _, _, _, err := admissionPromptShape(raw); err != errAdmissionDenied {
			t.Fatal("aggregate prompt bound bypassed")
		}
	}
	selector, name, values, err := admissionPromptShape(json.RawMessage(`{"name":"p","arguments":{"x":"synthetic-value"}}`))
	if err != nil || name != "p" || values["x"] != "synthetic-value" || strings.Contains(string(selector), "synthetic-value") {
		t.Fatal("selector carries arguments or valid shape refused")
	}
}

type admissionPromptValidatorFunc func(context.Context, ScopeKey, AdmissionActor, string, map[string]string) error

func (f admissionPromptValidatorFunc) ValidatePrompt(ctx context.Context, s ScopeKey, a AdmissionActor, target string, args map[string]string) error {
	return f(ctx, s, a, target, args)
}

func TestAdmissionPromptRequiresApprovedTargetValidator(t *testing.T) {
	s, h, _, _ := admissionTestServer(t)
	raw := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
	r, ok := s.admissionAuthenticate(raw, admissionTrustedRequest(http.MethodPost))
	if !ok {
		t.Fatal("authentication failed")
	}
	defer releaseAdmissionIngress(r)
	params := json.RawMessage(`{"name":"p","arguments":{"x":"v"}}`)
	if _, _, _, err := s.admissionRequest(r, "prompts/get", params); err != errAdmissionUnavailable {
		t.Fatal("missing mandatory schema validator accepted")
	}
	h.Prompts = admissionPromptValidatorFunc(func(context.Context, ScopeKey, AdmissionActor, string, map[string]string) error {
		return errAdmissionDenied
	})
	if _, _, _, err := s.admissionRequest(r, "prompts/get", params); err != errAdmissionDenied {
		t.Fatal("unapproved prompt arguments accepted")
	}
}
