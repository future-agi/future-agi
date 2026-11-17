package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A background response's answer first passes the gateway when GET
// /v1/responses/{id} refreshes it from the provider. The output checks in
// force for the caller run on that answer before the gateway stores or
// serves it, as they run on every other answer: a check that blocks the
// answer withholds it with the refusal the create path gives, and an answer
// no check can read is refused while a check on the answer would block it.
// The org turns its output check on after the background request was
// accepted: while such a check applies, the create path refuses a
// background request (answer_unavailable).

const bgCheckedID = "resp_bg_checked"

// bgCheckedUpstream accepts the background request as queued and answers
// the gateway's refresh with terminal.
func bgCheckedUpstream(terminal string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/responses":
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":%q,"object":"response","status":"queued","background":true,"store":true,"model":"gpt-4o","output":[]}`, bgCheckedID)
		case r.Method == http.MethodGet && r.URL.Path == "/v1/responses/"+bgCheckedID:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, terminal)
		}
	}
}

func bgCheckedCompleted(output string) string {
	return fmt.Sprintf(`{"id":%q,"object":"response","status":"completed","background":true,"store":true,"model":"gpt-4o","output":%s}`, bgCheckedID, output)
}

func bgCheckedMessage(text string) string {
	t, _ := json.Marshal(text)
	return fmt.Sprintf(`[{"type":"message","role":"assistant","content":[{"type":"output_text","text":%s}]}]`, t)
}

// get sends GET path with the org's key and returns what the client got.
func (g *postBlockGateway) get(t *testing.T, path string) (int, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.Header.Set("Authorization", "Bearer "+postBlockKey)
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

// createBackground starts the background response and, when action is set,
// then turns on the org's output check (data-leakage-prevention) in that
// mode.
func createBackground(t *testing.T, g *postBlockGateway, action string) {
	t.Helper()
	code, body := g.do(t, "/v1/responses", `{"model":"gpt-4o","input":"hi","background":true,"store":true}`)
	if code != http.StatusOK || !strings.Contains(body, `"status":"queued"`) {
		t.Fatalf("create: %d %s", code, body)
	}
	if action == "" {
		return
	}
	tenants := g.srv.handlers.tenantStore
	current := tenants.Get(postBlockOrg)
	tenants.Set(postBlockOrg, &tenant.OrgConfig{Providers: current.Providers, Guardrails: &tenant.GuardrailConfig{
		Checks: map[string]*tenant.GuardrailCheck{leakageCheck: {Enabled: true, Action: action, ConfidenceThreshold: 0.1}},
	}})
}

func TestResponsesBackground_RefreshRunsTheOutputChecks(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{upstream: bgCheckedUpstream(bgCheckedCompleted(bgCheckedMessage(postBlockLeak)))})
	createBackground(t, g, "block")
	for _, poll := range []string{"refresh", "again"} {
		code, body := g.get(t, "/v1/responses/"+bgCheckedID)
		if strings.Contains(body, "DROP TABLE") {
			t.Fatalf("%s: the blocked answer was served: status %d, body %s", poll, code, body)
		}
		if code != http.StatusForbidden {
			t.Errorf("%s: status %d, want the guardrail's 403; body %s", poll, code, body)
		}
		assertWireError(t, openaiWire, strings.TrimSpace(body), leakageCheck)
	}
}

func TestResponsesBackground_RefreshRefusesAnAnswerNoCheckCanRead(t *testing.T) {
	// The body passes the gateway's id and status check, but its output is not
	// a list of output items, so no check can read the answer.
	g := newPostBlockGateway(t, postBlockSetup{upstream: bgCheckedUpstream(bgCheckedCompleted(`"` + postBlockLeak + `"`))})
	createBackground(t, g, "block")
	code, body := g.get(t, "/v1/responses/"+bgCheckedID)
	if strings.Contains(body, "DROP TABLE") {
		t.Fatalf("an answer no check could read was served: status %d, body %s", code, body)
	}
	var e wireError
	if code != http.StatusForbidden || json.Unmarshal([]byte(body), &e) != nil || e.Error.Code != "answer_unavailable" || !strings.Contains(e.Error.Message, leakageCheck) {
		t.Errorf("status %d, body %s; want 403 answer_unavailable naming %s", code, body, leakageCheck)
	}
}

// Controls: with no output check, or one that only logs, the refreshed
// answer is served as before.
func TestResponsesBackground_RefreshServesAnswersNoCheckBlocks(t *testing.T) {
	for _, action := range []string{"", "log"} {
		t.Run("check="+action, func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{upstream: bgCheckedUpstream(bgCheckedCompleted(bgCheckedMessage(postBlockLeak)))})
			createBackground(t, g, action)
			code, body := g.get(t, "/v1/responses/"+bgCheckedID)
			if code != http.StatusOK || !strings.Contains(body, `"status":"completed"`) || !strings.Contains(body, "DROP TABLE") {
				t.Errorf("status %d, body %s; want the completed answer", code, body)
			}
		})
	}
}
