package server

// MIXED-HOP-GUARD-1 round 3, the parts that need its new APIs: the chain
// bound is one value everywhere; an attempt the request's record has no
// slot for is refused before its hold and never sent, and logged without
// values; and a customer key's mirror refusal is logged at debug level,
// without values.

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The node's chains and an org's are held to the same bound, one less than
// the attempts a request records.
func TestModelFallbackBoundsAgree(t *testing.T) {
	if config.MaxModelFallbacks != models.MaxAttempts-1 || tenant.MaxModelFallbacks != config.MaxModelFallbacks {
		t.Fatalf("config.MaxModelFallbacks %d, tenant.MaxModelFallbacks %d, models.MaxAttempts %d; want both bounds at MaxAttempts-1",
			config.MaxModelFallbacks, tenant.MaxModelFallbacks, models.MaxAttempts)
	}
}

// fullRecord is a managed request context whose record holds MaxAttempts
// failed managed attempts, charged or not.
func fullRecord(charged bool) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = "req-full"
	rc.Metadata["billing_mode"] = billingManaged
	for range models.MaxAttempts {
		a := models.Attempt{Kind: models.AttemptModelFallback, Provider: "openrouter", Model: mixedHopCheap,
			CredentialType: models.CredentialManaged, Outcome: models.OutcomeError}
		if charged {
			cost := 0.0001
			a.PromptTokens, a.Usage = 10, &models.Usage{PromptTokens: 10, Cost: &cost}
		}
		rc.AddAttempt(a)
	}
	return rc
}

func TestMixedHopGuardAttemptNeedsASlot(t *testing.T) {
	g := newManagedTestGateway(t, mixedHopOrg())
	h := g.srv.handlers
	var reserves, judged int
	h.hookObserver = func(hook, _ string, _ *models.RequestContext) {
		switch hook {
		case hookReserveAdd:
			reserves++
		case hookFallbackHopAllowed:
			judged++
		}
	}

	t.Run("full of charged failures", func(t *testing.T) {
		reserves, judged = 0, 0
		rc := fullRecord(true)
		defer rc.Release()
		before := rc.AttemptsSnapshot()
		var refusal, admit string
		var finish attemptFinish
		logs := captureLogs(func() {
			// A managed retry would take a new hold (retryConsumesHold): it is
			// refused before it.
			_, finish, refusal = h.startAttempt(context.Background(), rc, models.AttemptRetry, models.ReasonStatus5xx,
				"openrouter", mixedHopServingModel, models.CredentialManaged)
			admit = h.admitHop(rc, g.tenants.Get("org-m"), "openrouter", mixedHopServingModel)
		})
		if refusal != models.ReasonAttemptBudget || finish != nil || admit != models.ReasonAttemptBudget {
			t.Fatalf("startAttempt refusal %q (finish %v), admitHop %q; want both refused attempt_budget", refusal, finish != nil, admit)
		}
		if reserves != 0 || judged != 0 {
			t.Fatalf("%d holds and %d eligibility verdicts for attempts with no slot; want none", reserves, judged)
		}
		after := rc.AttemptsSnapshot()
		if len(after) != len(before) || after[len(after)-1] != before[len(before)-1] {
			t.Fatal("a refused attempt changed the record")
		}
		if !strings.Contains(logs, "attempt not sent") || !strings.Contains(logs, "reason="+models.ReasonAttemptBudget) ||
			!strings.Contains(logs, "request_id=req-full") {
			t.Fatalf("the refusal was not logged:\n%s", logs)
		}
		assertNoSecrets(t, logs)
	})

	t.Run("full of uncharged failures", func(t *testing.T) {
		reserves, judged = 0, 0
		rc := fullRecord(false)
		defer rc.Release()
		_, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptModelFallback, models.ReasonStatus5xx,
			"openrouter", mixedHopServingModel, models.CredentialManaged)
		if refusal != "" || finish == nil {
			t.Fatalf("startAttempt refused %q; want the attempt to take an uncharged failure's slot", refusal)
		}
		snap := rc.AttemptsSnapshot()
		last := snap[len(snap)-1]
		if len(snap) != models.MaxAttempts || last.Model != mixedHopServingModel || !last.InFlight || last.Index != models.MaxAttempts {
			t.Fatalf("record %d long, last %+v; want the new attempt last and in flight", len(snap), last)
		}
		finish(nil, nil, &models.APIError{Status: http.StatusServiceUnavailable})
		if last := rc.AttemptsSnapshot()[models.MaxAttempts-1]; last.InFlight || last.Model != mixedHopServingModel || last.StatusCode != http.StatusServiceUnavailable {
			t.Fatalf("finished attempt %+v; want it finished in place", last)
		}
	})
}

// A customer key's mirror refusal is expected on every sampled request of a
// node that mirrors its own traffic, so it is logged at debug level, without
// values; a managed target's refusal stays at info.
func TestMixedHopGuardMirrorCustomerKeyRefusalLog(t *testing.T) {
	g := newManagedTestGateway(t, mixedHopOrg())
	mixedHopMirror(g, "operator", "gpt-4o")
	var debug bytes.Buffer
	orig := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&debug, &slog.HandlerOptions{Level: slog.LevelDebug})))
	w := g.chat(t, mixedHopChat(mixedHopOwn, "")(false), nil)
	slog.SetDefault(orig)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	var refusals []string
	for _, line := range strings.Split(debug.String(), "\n") {
		if strings.Contains(line, "traffic mirror refused") {
			refusals = append(refusals, line)
		}
	}
	if len(refusals) != 1 || !strings.Contains(refusals[0], "level=DEBUG") || !strings.Contains(refusals[0], "reason="+mirrorRefusedCustomerKey) {
		t.Fatalf("mirror refusals logged %q; want one, at debug level, reason %s", refusals, mirrorRefusedCustomerKey)
	}
	assertNoSecrets(t, refusals[0])

	g = newManagedTestGateway(t, mixedHopOrg())
	mixedHopMirror(g, "operator", "gpt-4o")
	info := captureLogs(func() { w = g.chat(t, mixedHopChat(mixedHopOwn, "")(false), nil) })
	if w.Code != http.StatusOK || strings.Contains(info, "traffic mirror refused") {
		t.Fatalf("status %d; the customer key's refusal reached the info log:\n%s", w.Code, info)
	}
}
