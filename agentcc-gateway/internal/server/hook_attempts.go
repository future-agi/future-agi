package server

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// attemptFinish ends an attempt begun with beginAttempt: resp is the
// upstream's response (nil on error; for a stream, nil or its first chunk,
// whose usage comes at its end), usage its usage, err the call's error.
type attemptFinish func(resp any, usage *models.Usage, err error)

// beginAttempt is call-order step 14: it is called before every upstream
// call (primary, retry, failover, model and auto fallback, stream start)
// and returns the context for the call and the finish to call once with its
// result. kind and reason say why the attempt is made.
// It records the attempt on rc, as an error of unknown class until its finish
// completes it, so an attempt whose call never returns (a provider panic)
// still has a valid outcome. The finish feeds the per-model health when the
// result speaks about the model (attemptRun.feedsHealth). Keep the hook
// observer's report of the attempt (detail "<kind> <provider> <model>") and of
// its finish.
//
// It enforces the request deadline (attempt_budget.go): the context
// it returns carries the deadline, so the upstream call is cut at it; an
// attempt that would start at or after it is recorded as skipped (reason
// deadline) and gets an already-done context, whose cause is the hop-skipped
// sentinel for its reason, and a finish that does nothing, so the call is
// never sent. A caller that can skip the call uses startAttempt, which
// returns the refusal.
func (h *Handlers) beginAttempt(ctx context.Context, rc *models.RequestContext, kind models.AttemptKind, reason string, providerID, model string, credType models.CredentialType) (context.Context, attemptFinish) {
	attemptCtx, finish, refusal := h.startAttempt(ctx, rc, kind, reason, providerID, model, credType)
	if refusal != "" {
		return refusedContext(ctx, refusal), func(any, *models.Usage, error) {}
	}
	return attemptCtx, finish
}

// startAttempt is beginAttempt that returns, instead of a dead context, why
// the request's limits refused the attempt (the skip reason; the
// finish is then nil and the attempt is recorded as skipped), or "". The
// context it returns carries the request's deadline when it has one; a
// non-stream attempt's finish releases it, a stream's lives on with the
// stream (its first chunk finishes the attempt) and is released with the
// request's.
func (h *Handlers) startAttempt(ctx context.Context, rc *models.RequestContext, kind models.AttemptKind, reason string, providerID, model string, credType models.CredentialType) (context.Context, attemptFinish, string) {
	if h.hookObserver != nil {
		h.hookObserver(hookBeginAttempt, string(kind)+" "+providerID+" "+model, rc)
	}
	if refusal := attemptRefusal(rc); refusal != "" {
		// Begun and ended at once, so the observer sees both, as for an
		// attempt that was sent.
		h.recordRefusedAttempt(rc, kind, refusal, providerID, model)
		h.observeHook(hookFinishAttempt, rc)
		return ctx, nil, refusal
	}
	callCtx, release := attemptContext(ctx, rc)
	orgCreds := orgCredentials(rc)
	a := rc.AddAttempt(models.Attempt{
		Kind:           kind,
		Reason:         reason,
		Provider:       providerID,
		Model:          model,
		CredentialType: h.attemptCredential(orgCreds, providerID, credType),
		Outcome:        models.OutcomeError,
		ErrorClass:     models.ErrClassUnknown,
		ErrorOrigin:    models.OriginGateway,
		StartedAt:      time.Now(),
	})
	// One closure holds the attempt's state (captured by value), so an
	// attempt costs a single allocation here.
	id, started := a.AttemptID, a.StartedAt
	stream := rc.Request != nil && rc.Request.Stream
	return callCtx, func(resp any, usage *models.Usage, err error) {
		run := attemptRun{h: h, rc: rc, ctx: callCtx, id: id, kind: kind, reason: reason, started: started,
			providerID: providerID, model: model, stream: stream, orgCreds: orgCreds}
		run.finish(resp, usage, err)
		// After finish read the context's outcome. A stream runs on under
		// it past its first chunk; ChatCompletion's context releases it.
		if release != nil && !stream {
			release()
		}
	}, ""
}

// attemptRun is one attempt between beginAttempt and its finish.
type attemptRun struct {
	h          *Handlers
	rc         *models.RequestContext
	ctx        context.Context
	id         string // "" past models.MaxAttempts: nothing to update
	kind       models.AttemptKind
	reason     string
	started    time.Time
	providerID string
	model      string
	stream     bool
	orgCreds   bool // made with the org's own credentials
}

// finish completes the attempt: its outcome, error class and origin, status,
// latency, tokens and the upstream that served it, then feeds the per-model
// health and, at debug level, logs it. A stream's attempt finishes at its
// first chunk: that time is its ttfb_ms, and its latency_ms stays unset, for
// the stream's end to fill.
func (r *attemptRun) finish(resp any, usage *models.Usage, err error) {
	r.h.observeHook(hookFinishAttempt, r.rc)
	elapsed := time.Since(r.started)
	outcome, class, origin := routing.AttemptResult(r.ctx, err)
	served := servedUpstream(resp)
	if usage != nil {
		usage.ParseDetails()
	}
	if err == nil && served != "" {
		r.rc.Billing.ServedUpstreamProvider = served
	}
	if r.id != "" {
		status, msg := attemptStatusCode(err), routing.AttemptErrorMessage(err)
		r.rc.UpdateAttempt(r.id, func(a *models.Attempt) {
			a.Outcome, a.ErrorClass, a.ErrorOrigin = outcome, class, origin
			a.StatusCode, a.ErrorMessage = status, msg
			if r.stream && err == nil {
				a.TTFBMs = elapsed.Milliseconds()
			} else {
				a.LatencyMs = elapsed.Milliseconds()
			}
			if err == nil {
				a.ServedUpstreamProvider = served
			}
			copyAttemptTokens(a, usage)
		})
	}
	r.debugLog(outcome, class, elapsed)
	if !r.feedsHealth(outcome, class, origin) {
		return
	}
	// A stream's latency sample is its time to first chunk.
	if rec, ok := r.h.Deps().ModelHealth.(routing.ModelHealthRecorder); ok {
		rec.Record(r.providerID, r.model, elapsed, class)
	}
}

// nodeTimeShare is how much of the node's own timeout an attempt must have had
// for its gateway timeout to count against the model. The rest allows for the
// request's own work before its first call.
const nodeTimeShare = 0.9

// feedsHealth reports whether the attempt's result speaks about the model on
// this node, whose health every org shares (routing.ModelHealth also ignores
// the error classes the upstream did not cause). It does not when the org's
// own credentials made the call (its key, its quota, possibly its own upstream
// behind the same provider id), when the caller left, or when a gateway
// timeout cut the call short of the node's own time for the model: a deadline
// the caller (x-agentcc-timeout) or the org chose, or one that earlier
// attempts used up.
func (r *attemptRun) feedsHealth(outcome models.AttemptOutcome, class models.ErrorClass, origin models.ErrorOrigin) bool {
	switch {
	case r.orgCreds, outcome == models.OutcomeCancelled:
		return false
	case class == models.ErrClassTimeout && origin == models.OriginGateway:
		deadline, ok := r.ctx.Deadline()
		if !ok {
			return true // not a deadline the request set
		}
		return deadline.Sub(r.started) >= time.Duration(nodeTimeShare*float64(r.h.nodeTimeout(r.rc.Model)))
	}
	return true
}

// nodeTimeout is the timeout the node itself gives a request for model: its
// config.yaml model timeout, else the server default (nodeModelTimeout), held
// at server.max_request_timeout (clampTimeout) like every timeout the node
// resolves. These are resolveTimeout's last two steps, without the caller's
// header or the org's model timeout. feedsHealth compares an attempt's time
// against it, so a model timeout above the ceiling must read as the ceiling:
// the request is cut there, and that cut is the node's own time.
func (h *Handlers) nodeTimeout(model string) time.Duration {
	return h.clampTimeout(h.nodeModelTimeout(model))
}

// nodeModelTimeout is nodeTimeout before the ceiling: the config.yaml model
// timeout, else the server default. Only an async job, which writes no
// synchronous response, runs to it unclamped (requestTimeout).
func (h *Handlers) nodeModelTimeout(model string) time.Duration {
	if mt := h.modelTimeouts.Load(); mt != nil {
		if d, ok := (*mt)[model]; ok && d > 0 {
			return d
		}
	}
	return h.defaultTimeout
}

// debugLog logs the finished attempt at debug level, the one view of an
// internal key's attempts (its requests are not in the request log). Nothing
// is built unless debug logging is on.
func (r *attemptRun) debugLog(outcome models.AttemptOutcome, class models.ErrorClass, elapsed time.Duration) {
	logger := slog.Default()
	if !logger.Enabled(r.ctx, slog.LevelDebug) {
		return
	}
	logger.LogAttrs(r.ctx, slog.LevelDebug, "upstream attempt",
		slog.String("request_id", r.rc.RequestID), slog.String("attempt_id", r.id),
		slog.String("kind", string(r.kind)), slog.String("reason", r.reason),
		slog.String("provider", r.providerID), slog.String("model", r.model),
		slog.String("outcome", string(outcome)), slog.String("error_class", string(class)),
		slog.Int64("latency_ms", elapsed.Milliseconds()))
}

// attemptStatusCode is an attempt's HTTP status: 200 on success, else the
// status its error carries (0 when it carries none).
func attemptStatusCode(err error) int {
	if err == nil {
		return 200
	}
	return routing.AttemptStatus(err)
}

// copyAttemptTokens copies usage's token counts onto a. usage's details must
// have been parsed.
func copyAttemptTokens(a *models.Attempt, usage *models.Usage) {
	if usage == nil {
		return
	}
	a.PromptTokens = usage.PromptTokens
	a.CompletionTokens = usage.CompletionTokens
	a.CachedTokens = usage.PromptDetails.CachedTokens
	a.CacheWriteTokens = usage.PromptDetails.CacheWriteTokens
	a.ReasoningTokens = usage.CompletionDetails.ReasoningTokens
}

// servedUpstream is the upstream an aggregator reports as having served resp
// (OpenRouter's `provider` field), "" when it reports none.
func servedUpstream(resp any) string {
	switch r := resp.(type) {
	case *models.ChatCompletionResponse:
		if r != nil {
			return r.UpstreamProvider
		}
	case *models.StreamChunk:
		if r != nil {
			return r.UpstreamProvider
		}
	}
	return ""
}

// attemptCredential is whose credential pays for an attempt on providerID:
// managed when the instance dispatched is the node's own managed instance,
// byok otherwise. An attempt made with the org's own credentials (orgCreds)
// is byok even on a managed provider's id (the org's own openrouter is a
// separate instance under the same id); an internal key's call to the managed
// instance is managed. credType, the call site's view from the billing mode,
// decides only when there is no registry to ask.
func (h *Handlers) attemptCredential(orgCreds bool, providerID string, credType models.CredentialType) models.CredentialType {
	if h.registry == nil {
		return credType
	}
	if !orgCreds && h.registry.IsManaged(providerID) {
		return models.CredentialManaged
	}
	return models.CredentialBYOK
}

// orgCredentials reports whether the request's upstream calls now use the
// org's own credentials. Every place that swaps in an org's own provider
// instance (its provider for the model, its key on a global provider id, the
// org build for a key limited to its org's providers) sets billing mode byok.
func orgCredentials(rc *models.RequestContext) bool {
	return rc.Metadata["billing_mode"] == billingBYOK
}

// admitHop admits a fallback hop to model on providerID (call-order step 14):
// the hop must be allowed, then take its own hold. It returns "" when the hop
// may run, its hold taken, else why not: policy for an
// eligibility refusal, skipped_insufficient_credits for a refused hold. It
// makes the same two calls as admitFallbackHop, in the same order, and tells
// them apart.
func (h *Handlers) admitHop(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string) string {
	if !h.fallbackHopAllowed(rc, orgCfg, providerID, model) {
		return models.ReasonPolicy
	}
	if ok, err := h.reserveAdd(rc, providerID, model); !ok || err != nil {
		return models.ReasonSkippedInsufficientCredits
	}
	return ""
}

// recordSkippedHop records a fallback hop refused for reason as an attempt
// of outcome skipped. It was never dispatched, so it has no latency and does
// not feed the model's health.
func (h *Handlers) recordSkippedHop(rc *models.RequestContext, kind models.AttemptKind, reason, providerID, model string) {
	class, origin := routing.ClassifyError(routing.SkipHop(nil, reason), 0)
	rc.AddAttempt(models.Attempt{
		Kind:           kind,
		Reason:         reason,
		Provider:       providerID,
		Model:          model,
		CredentialType: h.attemptCredential(orgCredentials(rc), providerID, credentialType(rc)),
		Outcome:        models.OutcomeSkipped,
		ErrorClass:     class,
		ErrorOrigin:    origin,
		StartedAt:      time.Now(),
	})
}

// attemptReason is the reason for an attempt made after prev failed: ""
// for a first attempt.
func (h *Handlers) attemptReason(prev error) string {
	return routing.AttemptReason(prev)
}

// credentialType is whose credential pays for the request's attempts: the
// managed account when it is billed as managed, the caller's own otherwise.
func credentialType(rc *models.RequestContext) models.CredentialType {
	if rc.Metadata["billing_mode"] == billingManaged {
		return models.CredentialManaged
	}
	return models.CredentialBYOK
}

// fallbackKind is the kind of a model-fallback attempt: an auto-routed
// request falls back to its runners-up.
func fallbackKind(rc *models.RequestContext) models.AttemptKind {
	return routing.FallbackAttemptKind(rc.Metadata["auto_model"] != "")
}
