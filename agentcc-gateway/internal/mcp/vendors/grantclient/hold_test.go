package grantclient

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// Microsoft's hold: Django holds a binding's credential back by policy and
// answers HTTP 409 with error microsoft_coming_soon and, optionally, the
// sentence for the caller (mcp-vendor-credential-v1 $defs/Refusal message).
const holdReason = "microsoft_coming_soon"

// answering is a client of a Django that answers every fetch with status and
// body.
func answering(t *testing.T, status int, body string) *Client {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(s.Close)
	c, err := New(Options{BaseURL: s.URL, Secret: "fake-internal-secret-not-real"})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// The hold is Django's answer: a typed refusal carrying its sentence made
// plain and cut to 300 characters, and nothing else of the body (no connect
// hint, no secret). Any other 409, and the hold's code on a 200, stay
// ErrUnavailable. The golden's hold is a $defs/Refusal.
func TestGrantClientHoldCarriesDjangosSentence(t *testing.T) {
	hold, _ := schematest.ReadExample(t, "mcp-vendor-credential-v1.json")["hold"].(map[string]any)
	sentence, _ := hold["message"].(string)
	if hold["error"] != holdReason || sentence == "" {
		t.Fatalf("the golden hold %v is not microsoft_coming_soon with a message", hold)
	}
	for name, c := range map[string]struct {
		body map[string]any
		want string
	}{
		"the golden": {hold, sentence},
		"no other field": {map[string]any{
			"error": holdReason, "message": " Held.\n", "connect_hint": "https://connect.pipedream.com/fixture",
			"api_key": "fake-project-not-real",
		}, "Held."},
		"cut to 300 characters": {map[string]any{"error": holdReason, "message": strings.Repeat("é", 301)}, strings.Repeat("é", 300)},
		"no message":            {map[string]any{"error": holdReason}, ""},
	} {
		raw, err := json.Marshal(c.body)
		if err != nil {
			t.Fatal(err)
		}
		_, err = answering(t, http.StatusConflict, string(raw)).Fetch(context.Background(), testRequest())
		var r *Refusal
		if !errors.As(err, &r) || r.Reason != holdReason || r.ConnectHint != "" || r.Message != c.want || errors.Is(err, ErrUnavailable) {
			t.Fatalf("%s: %#v, want the hold with message %q", name, err, c.want)
		}
		if strings.Contains(err.Error(), "Held") || strings.Contains(err.Error(), "fake-project") {
			t.Fatalf("%s: the error %q carries the answer's text", name, err)
		}
	}
	for name, answer := range map[string]struct {
		status int
		body   string
	}{
		"another 409":            {http.StatusConflict, `{"error":"conflict","message":"Not the hold."}`},
		"an unknown refusal 409": {http.StatusConflict, `{"error":"a secret must not become a refusal code","message":"x"}`},
		"not JSON":               {http.StatusConflict, "Microsoft connections are coming soon."},
		"the hold's code on 200": {http.StatusOK, `{"error":"microsoft_coming_soon","message":"x"}`},
	} {
		_, err := answering(t, answer.status, answer.body).Fetch(context.Background(), testRequest())
		var r *Refusal
		if !errors.Is(err, ErrUnavailable) || errors.As(err, &r) {
			t.Fatalf("%s: %v, want ErrUnavailable", name, err)
		}
	}
	if errs := schematest.ValidateDef(t, "mcp-vendor-credential-v1.schema.json", "Refusal", hold); len(errs) > 0 {
		t.Fatalf("the golden hold breaks $defs/Refusal: %v", errs)
	}
}
