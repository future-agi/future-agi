package server

import (
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

// Pin the ruled text independently of the production projection helper.
func w4ClosedMessage(status int, id string) string {
	category, action := "upstream_unavailable", "retry later; contact support with the correlation id"
	if status == 401 {
		category, action = "authentication_error", "contact support with the correlation id"
	}
	if status == 403 {
		category, action = "permission_error", "contact support with the correlation id"
	}
	if id == "" {
		id = "unavailable"
	}
	return category + ": " + action + " (correlation id: " + id + ")"
}

func w4AssertProjectedError(t *testing.T, w *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	id := w.Header().Get("X-Agentcc-Request-Id")
	if id == "" {
		t.Fatal("missing request correlation header")
	}
	want := map[string]any{"message": w4ClosedMessage(status, id), "type": "upstream_error", "code": code, "param": nil}
	if got := dxR4Error(t, w); !reflect.DeepEqual(got, want) {
		t.Fatalf("projected error=%v want=%v", got, want)
	}
}

func w4AssertTerminalOrder(t *testing.T, body, hiddenDiagnostic string) {
	t.Helper()
	answer := strings.Index(body, "hello there")
	terminal, count := -1, 0
	for _, frame := range strings.Split(body, "\n\n") {
		data, ok := strings.CutPrefix(frame, "data: ")
		if !ok {
			continue
		}
		var envelope struct {
			Error *struct{ Message, Type, Code string } `json:"error"`
		}
		if json.Unmarshal([]byte(data), &envelope) != nil || envelope.Error == nil {
			continue
		}
		count++
		terminal = strings.Index(body, frame)
		e := envelope.Error
		prefix := "upstream_unavailable: retry later; contact support with the correlation id (correlation id: "
		if e.Code != "provider_502" || e.Type != "upstream_error" || !strings.HasPrefix(e.Message, prefix) || !strings.HasSuffix(e.Message, ")") || len(e.Message) <= len(prefix)+1 {
			t.Fatalf("wrong terminal error: %+v", e)
		}
	}
	if answer < 0 || terminal <= answer || count != 1 || strings.Contains(body, "[DONE]") || strings.Contains(body, hiddenDiagnostic) {
		t.Fatalf("want answer then one projected upstream error, no DONE: %s", body)
	}
}
