package server

// x-agentcc-user-id (and metadata.agent.user_id, the x-omega-user-id
// alias) accepts what the public docs present: any printable value of 1 to
// 255 bytes, such as "jane+tag@example.com", "user 42" or a 200-character
// id. Control characters, characters that do not print, invalid UTF-8 and
// longer values are refused. The session id keeps its rule (at most 255
// bytes; handlers.go maxSessionIDLen). The other id fields keep the id rule
// (A-Z a-z 0-9 . _ : @ / -, at most 128): they key Redis state (run
// budgets, affinity).

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// agentContextCapture records the agent context and session of the last
// request that reached the pipeline.
type agentContextCapture struct {
	mu      sync.Mutex
	agent   models.AgentContext
	session string
	userID  string
}

func (c *agentContextCapture) Name() string  { return "agent-capture" }
func (c *agentContextCapture) Priority() int { return 998 }
func (c *agentContextCapture) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	c.mu.Lock()
	c.agent, c.session, c.userID = rc.Agent.Clone(), rc.SessionID, rc.UserID
	c.mu.Unlock()
	return pipeline.ResultContinue()
}
func (c *agentContextCapture) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (c *agentContextCapture) last() (models.AgentContext, string, string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.agent, c.session, c.userID
}

func agentBodyUserID(v string) string {
	b, _ := json.Marshal(v)
	return `{"model":"gpt-4o","metadata":{"agent":{"user_id":` + string(b) + `}},"messages":[{"role":"user","content":"hi"}]}`
}

func agentErrorParam(b []byte) string {
	var e models.ErrorResponse
	if json.Unmarshal(b, &e) != nil || e.Error.Param == nil {
		return ""
	}
	return *e.Error.Param
}

func TestAgentHeaderValues(t *testing.T) {
	up := startHookUpstream(t, 0)
	capture := &agentContextCapture{}
	ht, _ := deadlineServer(t, up.URL, capture)

	long200 := strings.Repeat("u", 200)
	exact255 := strings.Repeat("é", 127) + "x" // 255 bytes, 128 characters
	documented := []string{
		"jane+tag@example.com", "user 42", "Jane Doe", "user-42", "{123e4567-e89b}",
		strings.Repeat("u", 129), long200, exact255, "Zoë Ångström", "用户42", "a/b:c@d.e_f-g", "#42 (admin)",
	}

	t.Run("documented values are accepted from every channel", func(t *testing.T) {
		for _, v := range documented {
			for _, ch := range []struct {
				name    string
				body    string
				headers []string
			}{
				{"x-agentcc-user-id", hookChatBody, []string{"x-agentcc-user-id", v}},
				{"x-omega-user-id", hookChatBody, []string{"x-omega-user-id", v}},
				{"metadata.agent.user_id", agentBodyUserID(v), nil},
			} {
				w := ht.chat(t, ch.body, ch.headers...)
				if w.Code != http.StatusOK {
					t.Errorf("%s %q (%d bytes): HTTP %d %s", ch.name, v, len(v), w.Code, w.Body.String())
					continue
				}
				agent, _, userID := capture.last()
				if agent.UserID != v {
					t.Errorf("%s %q: the agent context's user id is %q", ch.name, v, agent.UserID)
				}
				// With no x-agentcc-user and no body user, the
				// agent context's user id is the request's user id.
				if userID != v {
					t.Errorf("%s %q: the request's user id is %q, want the agent user id", ch.name, v, userID)
				}
			}
		}
	})

	t.Run("control, non-printing, invalid and over-long values are refused", func(t *testing.T) {
		for _, c := range []struct{ name, v string }{
			{"256 bytes", strings.Repeat("u", 256)},
			{"256 bytes of 2-byte characters", strings.Repeat("é", 128)},
			{"a tab", "user\t42"},
			{"a newline", "user\n42"},
			{"a carriage return", "user\r42"},
			{"NUL", "user\x0042"},
			{"DEL", "user\x7f42"},
			{"a C1 control", "user\u008542"},
			{"a no-break space", "user\u00a042"},
			{"a zero-width space", "user\u200b42"},
			{"a byte-order mark", "\ufeffuser"},
		} {
			// httptest skips net/http's own header check, so the header
			// carries every value, as the body does.
			for _, ch := range []struct {
				param   string
				body    string
				headers []string
			}{
				{"metadata.agent.user_id", agentBodyUserID(c.v), nil},
				{"x-agentcc-user-id", hookChatBody, []string{"x-agentcc-user-id", c.v}},
			} {
				w := ht.chat(t, ch.body, ch.headers...)
				if w.Code != http.StatusBadRequest || deadlineErrorCode(w.Body.Bytes()) != errAgentHeaderInvalid ||
					agentErrorParam(w.Body.Bytes()) != ch.param {
					t.Errorf("%s via %s: HTTP %d %s, want 400 %s param %s", c.name, ch.param, w.Code, w.Body.String(), errAgentHeaderInvalid, ch.param)
					continue
				}
				if msg := w.Body.String(); !strings.Contains(msg, "1 to 255 bytes") {
					t.Errorf("%s: the message does not state the rule: %s", c.name, msg)
				}
			}
		}
	})

	t.Run("invalid UTF-8 in a header is refused", func(t *testing.T) {
		// JSON cannot carry it: a decoder reads invalid bytes as U+FFFD.
		w := ht.chat(t, hookChatBody, "x-agentcc-user-id", "user\xff42")
		if w.Code != http.StatusBadRequest || deadlineErrorCode(w.Body.Bytes()) != errAgentHeaderInvalid {
			t.Errorf("HTTP %d %s, want 400 %s", w.Code, w.Body.String(), errAgentHeaderInvalid)
		}
	})

	t.Run("the session id keeps its rule: any value of at most 255 bytes", func(t *testing.T) {
		for _, v := range []string{"jane+tag@example.com", "user 42", strings.Repeat("s", 255)} {
			w := ht.chat(t, hookChatBody, "x-agentcc-session-id", v)
			_, session, _ := capture.last()
			if w.Code != http.StatusOK || session != v {
				t.Errorf("session %q: HTTP %d, session %q: %s", v, w.Code, session, w.Body.String())
			}
		}
		// x-agentcc-session-id is checked first by the caller-metadata step
		// (handlers.go, session_id_too_long); x-omega-session-id by the agent
		// parse step (agent_header_invalid). Both are 400.
		for header, code := range map[string]string{"x-agentcc-session-id": "session_id_too_long", "x-omega-session-id": errAgentHeaderInvalid} {
			w := ht.chat(t, hookChatBody, header, strings.Repeat("s", 256))
			if w.Code != http.StatusBadRequest || deadlineErrorCode(w.Body.Bytes()) != code {
				t.Errorf("a 256-byte %s: HTTP %d %s, want 400 %s", header, w.Code, w.Body.String(), code)
			}
		}
	})

	t.Run("the other id fields keep the id rule", func(t *testing.T) {
		for _, h := range []string{"x-agentcc-run-id", "x-agentcc-agent-id", "x-agentcc-budget-scope", "x-agentcc-workspace-id"} {
			w := ht.chat(t, hookChatBody, h, "user 42")
			if w.Code != http.StatusBadRequest || deadlineErrorCode(w.Body.Bytes()) != errAgentHeaderInvalid {
				t.Errorf("%s \"user 42\": HTTP %d %s, want 400 %s", h, w.Code, w.Body.String(), errAgentHeaderInvalid)
			}
		}
	})
}
