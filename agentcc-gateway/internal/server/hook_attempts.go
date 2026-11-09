package server

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
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
// result. kind and reason say why the attempt is made (plan section 6.1).
// It records the attempt on rc, as an error of unknown class until its finish
// completes it, so an attempt whose call never returns (a provider panic)
// still has a valid outcome. The finish feeds the per-model health when the
// result speaks about the model (attemptRun.feedsHealth). Keep the hook
// observer's report of the attempt (detail "<kind> <provider> <model>") and of
// its finish.
//
// It enforces the request deadline (attempt_budget.go, NG-27): the context
// it returns carries the deadline, so the upstream call is cut at it; an
// attempt that would start at or after it is recorded as skipped (reason
// deadline) and gets an already-done context, whose cause is the hop-skipped
// sentinel for its reason, and a finish that does nothing, so the call is
// never sent. A caller that can skip the call uses startAttempt, which
// returns the refusal (round 7 integrator line I3).
func (h *Handlers) beginAttempt(ctx context.Context, rc *models.RequestContext, kind models.AttemptKind, reason string, providerID, model string, credType models.CredentialType) (context.Context, attemptFinish) {
	attemptCtx, finish, refusal := h.startAttempt(ctx, rc, kind, reason, providerID, model, credType)
	if refusal != "" {
		return refusedContext(ctx, refusal), func(any, *models.Usage, error) {}
	}
	return attemptCtx, finish
}

// startAttempt is beginAttempt that returns, instead of a dead context, why
// the request's limits refused the attempt (a plan section 6.1 reason; the
// finish is then nil and the attempt is recorded as skipped, or, refused
// with attempt_budget because the request's attempt record has no slot for
// it, not recorded), or "". The
// context it returns carries the request's deadline when it has one; a
// non-stream attempt's finish releases it, a stream's lives on with the
// stream (its first chunk finishes the attempt) and is released with the
// request's.
func (h *Handlers) startAttempt(ctx context.Context, rc *models.RequestContext, kind models.AttemptKind, reason string, providerID, model string, credType models.CredentialType, selected ...providers.Provider) (context.Context, attemptFinish, string) {
	if h.hookObserver != nil {
		h.hookObserver(hookBeginAttempt, string(kind)+" "+providerID+" "+model, rc)
	}
	// The request's first attempt sets the billing mode every later one is
	// held to (mixed_billing.go).
	setChainMode(rc, credType)
	if refusal := attemptRefusal(rc); refusal != "" {
		// Begun and ended at once, so the observer sees both, as for an
		// attempt that was sent.
		h.recordRefusedAttempt(rc, kind, refusal, providerID, model)
		h.observeHook(hookFinishAttempt, rc)
		return ctx, nil, refusal
	}
	// No attempt, from any hop source, is sent in another billing mode than
	// the request's first, unless the node allows mixed billing chains. It
	// goes to the request's skipped-hop record in its own mode, never to its
	// attempts, before any hold is taken.
	if refusal := h.mixedBillingRefusal(rc, credType); refusal != "" {
		h.recordCrossModeSkip(rc, kind, refusal, providerID, model)
		restoreChainBillingMode(rc)
		h.observeHook(hookFinishAttempt, rc)
		return ctx, nil, refusal
	}
	// No attempt is sent that the request cannot record, before any hold is
	// taken for it: its answer or its charge would go unbilled
	// (hasAttemptRoom).
	if !h.hasAttemptRoom(rc, kind, providerID, model) {
		h.observeHook(hookFinishAttempt, rc)
		return ctx, nil, models.ReasonAttemptBudget
	}
	if admission := capbudget.RequestAdmission(rc); admission != nil {
		if capbudget.RecheckForwardedCapChat(rc) != nil || !admission.MatchesRoute(providerID, model) || !admission.ClaimAttempt() {
			h.recordRefusedAttempt(rc, kind, models.ReasonAttemptBudget, providerID, model)
			h.observeHook(hookFinishAttempt, rc)
			return ctx, nil, models.ReasonAttemptBudget
		}
	}
	if credType == models.CredentialManaged && h.mixedCatalogueHop(rc, rc.Billing.ReservePrice) {
		h.recordSkippedHop(rc, kind, models.ReasonPolicy, providerID, model)
		return ctx, nil, models.ReasonPolicy
	}
	if !h.sealOwnedHop(rc) {
		h.recordSkippedHop(rc, kind, models.ReasonSkippedInsufficientCredits, providerID, model)
		return ctx, nil, models.ReasonSkippedInsufficientCredits
	}
	// Sealing can establish an unknown prior outcome's full-hold liability.
	// Recheck before reserve/Begin so that newly charge-bearing slots cannot
	// leave a durably dispatched attempt with no room to record it.
	if rc.Billing.OwnedRequest && !h.hasAttemptRoom(rc, kind, providerID, model) {
		h.observeHook(hookFinishAttempt, rc)
		return ctx, nil, models.ReasonAttemptBudget
	}
	// A retry may reuse the primary hold only if the preceding attempt was
	// nonbillable. Reported usage (or a cancellation estimate) consumes that
	// hold, so take another through the same atomic ledger admission as hops.
	if kind == models.AttemptRetry && credentialType(rc) == models.CredentialManaged && (rc.Billing.OwnedRequest || retryConsumesHold(rc)) {
		if ok, err := h.reserveAdd(rc, providerID, model); !ok || err != nil {
			h.recordSkippedHop(rc, kind, models.ReasonSkippedInsufficientCredits, providerID, model)
			h.observeHook(hookFinishAttempt, rc)
			return ctx, nil, models.ReasonSkippedInsufficientCredits
		}
	}
	financialID := ""
	if card := rc.Billing.ReservePrice; credType == models.CredentialManaged && (rc.Billing.OwnedRequired || rc.Billing.OwnedRequest || (card != nil && card.Inclusive != nil)) {
		financialID = rc.Billing.OwnedAttemptID
		if financialID == "" {
			// Direct ledger callers can reserve before entering the hooks. Allocate
			// from the same sequence, never a log index; Begin still requires that
			// exact identity to have a live durable reservation.
			financialID = rc.NewFinancialAttemptID()
		}
		// Consume the reservation identity on this dispatch path, including a
		// refusal. A later hop must obtain its own durable reservation.
		rc.Billing.OwnedAttemptID = ""
		if card != nil && card.Inclusive != nil {
			var bindingErr error
			ctx, bindingErr = h.bindInclusiveCredential(ctx, rc, providerID, selected...)
			if bindingErr != nil {
				h.recordSkippedHop(rc, kind, models.ReasonSkippedInsufficientCredits, providerID, model)
				return ctx, nil, models.ReasonSkippedInsufficientCredits
			}
		}
		owner, ok := h.billing.(interface {
			BeginInclusive(*models.RequestContext, string) error
		})
		if !ok || financialID == "" || owner.BeginInclusive(rc, financialID) != nil {
			h.recordSkippedHop(rc, kind, models.ReasonSkippedInsufficientCredits, providerID, model)
			return ctx, nil, models.ReasonSkippedInsufficientCredits
		}
	}
	orgCreds := orgCredentials(rc)
	cred := h.attemptCredential(orgCreds, providerID, credType)
	a := rc.AddAttempt(models.Attempt{
		FinancialID:       financialID,
		InclusiveIdentity: rc.Billing.InclusiveIdentity,
		UpstreamKeyID:     rc.Billing.UpstreamKeyID,
		Kind:              kind,
		Reason:            reason,
		Provider:          providerID,
		Model:             model,
		CredentialType:    cred,
		Outcome:           models.OutcomeError,
		ErrorClass:        models.ErrClassUnknown,
		ErrorOrigin:       models.OriginGateway,
		StartedAt:         time.Now(),
		Pricing:           models.NewAttemptPricing(&rc.Billing),
		NativeTokenUsage:  h.nativeTokenUsage(providerID),
		InFlight:          true,
	})
	if a.AttemptID == "" {
		// The record filled after hasAttemptRoom (attempts recorded at once):
		// still never sent unrecorded.
		h.logUnrecordable(rc, kind, providerID, model)
		h.observeHook(hookFinishAttempt, rc)
		return ctx, nil, models.ReasonAttemptBudget
	}
	callCtx, release := attemptContext(ctx, rc)
	// One closure holds the attempt's state (captured by value), so an
	// attempt costs a single allocation here.
	id, started := a.AttemptID, a.StartedAt
	stream := rc.Request != nil && rc.Request.Stream
	return callCtx, func(resp any, usage *models.Usage, err error) {
		run := attemptRun{h: h, rc: rc, ctx: callCtx, id: id, kind: kind, reason: reason, started: started,
			providerID: providerID, model: model, stream: stream, orgCreds: orgCreds, cred: cred}
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
	id         string // never "": an attempt with no slot is not sent
	kind       models.AttemptKind
	reason     string
	started    time.Time
	providerID string
	model      string
	stream     bool
	orgCreds   bool                  // made with the org's own credentials
	cred       models.CredentialType // whose credential the attempt used
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
	generation := models.GenerationFromResponse(resp)
	var failure *models.APIError
	if errors.As(err, &failure) && failure.BillingEvidence != nil {
		generation.Observe(failure.BillingEvidence.Generation)
		usage = models.MergeUsage(usage, failure.BillingEvidence.Usage)
	}
	served := generation.Provider
	if usage != nil {
		usage.ParseDetails()
	}
	// This attempt's identity cannot inherit an earlier attempt's endpoint.
	r.rc.Billing.ServedUpstreamProvider = served
	if r.id != "" {
		status, msg := attemptStatusCode(err), routing.AttemptErrorMessage(err)
		r.rc.UpdateAttempt(r.id, func(a *models.Attempt) {
			a.InFlight = false
			a.Outcome, a.ErrorClass, a.ErrorOrigin = outcome, class, origin
			a.Generation.Observe(generation)
			a.StatusCode, a.ErrorMessage = status, msg
			a.ProviderRejected = failure != nil && failure.ProviderRejected
			if r.stream && err == nil {
				a.TTFBMs = elapsed.Milliseconds()
			} else {
				a.LatencyMs = elapsed.Milliseconds()
			}
			if served != "" {
				a.ServedUpstreamProvider = served
			}
			a.ServedModel, a.UpstreamGenerationID = servedIdentity(resp)
			copyAttemptTokens(a, usage)
			if usage != nil {
				a.UsageEstimated = usage.Estimated
				copyUsage := *usage
				if usage.Cost != nil {
					charge := *usage.Cost
					copyUsage.Cost = &charge
				}
				a.Usage = &copyUsage
			}
		})
	}
	r.debugLog(outcome, class, elapsed)
	// A provider_busy refusal (never dispatched) keeps the next hop in its
	// billing mode (fallbackHopAllowed) and says nothing about the model.
	busy := models.IsProviderBusy(err)
	r.rc.Billing.BusyCredential = ""
	if busy {
		r.rc.Billing.BusyCredential = r.cred
	}
	if busy || !r.feedsHealth(outcome, class, origin) {
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
// may run, its hold taken, else why not (plan section 6.1): policy for an
// eligibility refusal, skipped_insufficient_credits for a refused hold. It
// makes the same two calls as admitFallbackHop, in the same order, and tells
// them apart. A hop in another billing mode than the request's is refused
// (policy) before either, so it is never priced or held (mixed_billing.go);
// failoverHops.next checks that first, to record it as a skipped hop rather
// than an attempt. A hop the request could not record is refused
// (attempt_budget) before either too (hasAttemptRoom).
func (h *Handlers) admitHop(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string) string {
	if reason := h.mixedBillingRefusal(rc, credentialType(rc)); reason != "" {
		return reason
	}
	if !h.hasAttemptRoom(rc, models.AttemptFailover, providerID, model) {
		return models.ReasonAttemptBudget
	}
	if capbudget.RequestAdmission(rc) != nil {
		return models.ReasonAttemptBudget
	}
	if !h.fallbackHopAllowed(rc, orgCfg, providerID, model) {
		return models.ReasonPolicy
	}
	if ok, err := h.reserveAdd(rc, providerID, model); !ok || err != nil {
		return models.ReasonSkippedInsufficientCredits
	}
	return ""
}

// hasAttemptRoom reports whether the request can record one more attempt
// that is sent (models.RequestContext.CanRecordAttempt), and logs, without
// values, the one it cannot. Every place that admits or starts an attempt
// asks it first, before the attempt's hold: an attempt the request cannot
// record is never held or sent. A full record of models.MaxAttempts makes
// room by dropping an attempt that carries no charge (a failure the upstream
// charged nothing for, a skipped hop); only when every slot holds an attempt
// that served, may still be charged or is in flight does the chain stop,
// answering with its last error and billing exactly the attempts it made.
func (h *Handlers) hasAttemptRoom(rc *models.RequestContext, kind models.AttemptKind, providerID, model string) bool {
	if rc.CanRecordAttempt() {
		return true
	}
	h.logUnrecordable(rc, kind, providerID, model)
	return false
}

// logUnrecordable logs an attempt refused because the request's attempt
// record has no slot to give it, without the request's content or
// credentials.
func (h *Handlers) logUnrecordable(rc *models.RequestContext, kind models.AttemptKind, providerID, model string) {
	slog.Warn("attempt not sent: every slot of the request's attempt record holds an attempt that served, may be charged or is in flight",
		"request_id", rc.RequestID, "kind", string(kind), "provider", providerID, "model", model,
		"reason", models.ReasonAttemptBudget, "max_attempts", models.MaxAttempts)
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

// attemptReason is the reason (plan section 6.1) for an attempt made after
// prev failed: "" for a first attempt.
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
