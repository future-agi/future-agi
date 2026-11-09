package server

// A held stream lets none of a blocked answer out when it ends early or when
// the request's deadline fires while a check reads it. Each test asserts the
// stream guarantee: no frame of a blocked answer reaches a streaming client.
//
//   - The streaming checker in stop mode holds a chat stream and checks it
//     every check_interval characters. A stream that ends before the
//     provider's (the request deadline, a provider error) gets the checker's
//     final check on the part it read, and the text after the last interval
//     check never goes out unread.
//   - A held stream whose checks are still running when the request deadline
//     fires: the checks run to their end, each within its own timeout, on a
//     context the request's end does not cancel, so under guardrails.fail_open
//     a blocking check still blocks.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The streaming checker in stop mode (default check_interval 100) holds the
// chat stream; the request deadline ends it 29 characters in, before the
// checker's next check. The checker's final check reads that part: the
// client gets none of it. With bodies logged the post-plugin rules read the
// assembled answer too (the control).
func TestStreamHold_CheckerReadsTheTailAtTheDeadline(t *testing.T) {
	for _, bodies := range []bool{false, true} {
		t.Run(fmt.Sprintf("bodies logged %t", bodies), func(t *testing.T) {
			setup := postBlockSetup{staticRule: leakageCheck, capture: bodies,
				streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 100, FailureAction: "stop"},
				upstream:  stallingStreamedAnswer(heldDeadlineLeak)}
			g := newPostBlockGateway(t, setup)
			for i := 0; i < 3; i++ {
				code, body := g.doAs(t, postBlockKey, "/v1/chat/completions", answerChatBody(true),
					map[string]string{"x-agentcc-request-timeout": "300"})
				t.Logf("run %d: status %d body %q", i, code, body)
				if strings.Contains(body, "DROP TABLE") {
					t.Errorf("run %d: the held stream released the blocked statement at the deadline", i)
				}
			}
		})
	}
}

// The same checker-held stream, ended by a provider error after the first
// delta (the upstream aborts the connection).
func TestStreamHold_CheckerReadsTheTailOnAnUpstreamError(t *testing.T) {
	abort := func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var req struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.Unmarshal(raw, &req)
		if !req.Stream {
			return
		}
		writeStreamedAnswer(w, r.URL.Path, req.Model, heldDeadlineLeak, func() {
			time.Sleep(200 * time.Millisecond)
			panic(http.ErrAbortHandler)
		})
	}
	g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 100, FailureAction: "stop"},
		upstream:  abort})
	code, body := g.do(t, "/v1/chat/completions", answerChatBody(true))
	t.Logf("status %d body %q", code, body)
	if strings.Contains(body, "DROP TABLE") {
		t.Errorf("the held stream released the blocked statement on an upstream error")
	}
}

// slowLeakage is data-leakage-prevention that takes 1 s to answer.
type slowLeakage struct{ inner guardrails.Guardrail }

func (s slowLeakage) Name() string            { return "slow-leakage" }
func (s slowLeakage) Stage() guardrails.Stage { return guardrails.StagePost }
func (s slowLeakage) Check(ctx context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	time.Sleep(time.Second)
	return s.inner.Check(ctx, in)
}

// The stream ends normally and fast; the request deadline (300 ms, set by
// the client) fires while a 1 s blocking rule in the config file (a sync post
// rule) reads the held answer, on chat with no streaming checker.
// fail_open on (the default). The rule runs to its end, within its own
// timeout, and blocks.
func TestStreamHold_DeadlineDuringARuleNeverReleasesTheAnswer(t *testing.T) {
	for _, withDeadline := range []bool{false, true} {
		t.Run(fmt.Sprintf("deadline %t", withDeadline), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{failOpen: true, staticRule: "slow-leakage",
				checks:   map[string]guardrails.Guardrail{"slow-leakage": slowLeakage{leakage.New(nil)}},
				upstream: serveStreamedAnswer(heldLeakDeltas)})
			var h map[string]string
			if withDeadline {
				h = map[string]string{"x-agentcc-request-timeout": "300"}
			}
			code, body := g.doAs(t, postBlockKey, "/v1/chat/completions", answerChatBody(true), h)
			t.Logf("status %d body %.600q", code, body)
			if strings.Contains(body, "DROP TABLE") {
				t.Errorf("the held stream released the blocked statement (deadline %t)", withDeadline)
			}
		})
	}
}

// The same race with a 1 s blocking organization check, on every streaming
// route. The check runs to its end, within its own timeout, and blocks.
func TestStreamHold_DeadlineDuringAnOrgCheckNeverReleasesTheAnswer(t *testing.T) {
	for _, withDeadline := range []bool{false, true} {
		for _, rt := range heldRoutes {
			t.Run(fmt.Sprintf("deadline %t/%s", withDeadline, rt.name), func(t *testing.T) {
				g := newPostBlockGateway(t, postBlockSetup{failOpen: true,
					checks: map[string]guardrails.Guardrail{"slow-leakage": slowLeakage{leakage.New(nil)}},
					orgChecks: map[string]*tenant.GuardrailCheck{
						"slow-leakage": {Enabled: true, Action: "block", ConfidenceThreshold: 0.1},
					},
					upstream: serveStreamedAnswer(heldLeakDeltas)})
				var h map[string]string
				if withDeadline {
					h = map[string]string{"x-agentcc-request-timeout": "300"}
				}
				code, body := g.doAs(t, postBlockKey, rt.path, rt.body, h)
				t.Logf("status %d body %.600q", code, body)
				if strings.Contains(body, "DROP TABLE") {
					t.Errorf("the held stream released the blocked statement (deadline %t)", withDeadline)
				}
			})
		}
	}
}
