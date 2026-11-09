package server

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func (h *Handlers) handleNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) {
	served, err := h.runNonStream(ctx, rc, provider, orgCfg)
	h.finishNonStream(w, rc, served, err)
}

// runNonStream runs a non-streaming chat completion through the pipeline
// (call-order steps 12 to 16) on provider, the provider routing resolved for
// rc, with the org's (or the global) failover and model fallbacks, and leaves
// the result on rc. The synchronous handler and async jobs both run it, so a
// job is routed, gated and billed as its request would have been.
//
// It also returns the provider instance that served the response. Retries can
// switch between the base instance and an org's instance with the same ID,
// so x-agentcc-timeout-ms reads the client timeout of this instance rather
// than one rebuilt from routing metadata.
func (h *Handlers) runNonStream(ctx context.Context, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) (providers.Provider, error) {
	// No attempt runs on an unfixed plan route: routing fixed it already
	// (resolveRoute, AnthropicMessages, and an async job's clone carries it),
	// so this only ever fixes one for a request that reached dispatch without
	// routing, from its state before any attempt (fallback_plan.go).
	fixFallbackRoute(rc)
	// Determine effective failover: org routing config takes priority over global.
	effectiveFailover := h.effectiveFailover(orgCfg)

	if fo, of, key := h.chatFailover(ctx, rc, orgCfg, effectiveFailover); fo != nil {
		return h.runNonStreamWithFailover(ctx, rc, fo, effectiveFailover, orgCfg, of, key)
	}

	// The request's fallback plan, as admission judged it: resolved on the
	// route routing fixed (fallback_plan.go).
	modelChain := h.effectiveFallbackPlan(rc, rc.FallbackRoute())

	originalModel := rc.Request.Model
	orgID := rc.Metadata["org_id"]
	servingProvider := provider

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		ctx, release := withRequestDeadline(ctx, rc) // I3
		defer release()
		h.planHedge(rc, orgCfg)
		var resp *models.ChatCompletionResponse
		err := h.retryRoutedCall(ctx, orgCfg, false, models.AttemptPrimary, nil, func(kind models.AttemptKind, previous error) error {
			var err error
			resp, err = h.callChat(ctx, rc, provider, rc.Provider, kind, previous)
			return err
		})
		if err == nil {
			rc.Response = resp
			rc.ResolvedModel = resp.Model
			return nil
		}

		if len(modelChain) == 0 || !shouldTryModelFallback(effectiveFailover, rc, orgCfg, err) {
			return err
		}

		lastErr, skipsBefore := err, rc.SkippedHopCount()
		for _, fbModel := range modelChain {
			fbProvider, fbProviderID, resolvedModel, ok := h.admitModelFallback(ctx, orgID, orgCfg, rc, fbModel)
			if !ok {
				continue
			}

			rc.Provider = fbProviderID
			rc.Request.Model = resolvedModel

			var fbResp *models.ChatCompletionResponse
			fbCallErr := h.retryRoutedCall(ctx, orgCfg, false, fallbackKind(rc), lastErr, func(kind models.AttemptKind, previous error) error {
				var err error
				fbResp, err = h.callChat(ctx, rc, fbProvider, fbProviderID, kind, previous)
				return err
			})
			lastErr = fbCallErr
			if fbCallErr == nil {
				servingProvider = fbProvider
				rc.Response = fbResp
				rc.ResolvedModel = fbResp.Model
				rc.Flags.FallbackUsed = true
				rc.Metadata["original_model"] = originalModel
				rc.Metadata["fallback_model"] = resolvedModel
				return nil
			}
		}

		return chainError(rc, skipsBefore, err, lastErr)
	}

	err := h.engine.Process(ctx, rc, providerCall)
	return servingProvider, err
}

// finishNonStream writes the result of runNonStream (call-order steps 17 to
// 20): served is the instance that served, err is what the pipeline returned.
func (h *Handlers) finishNonStream(w http.ResponseWriter, rc *models.RequestContext, served providers.Provider, err error) {
	if err != nil {
		h.failChat(w, rc, h.attemptLimitError(w, rc, err)) // I3
		return
	}

	if rc.Response == nil {
		if apiErr := withheldAnswerError(rc); apiErr != nil {
			h.failChat(w, rc, apiErr)
			return
		}
		h.failChat(w, rc, models.ErrInternal("no response from provider"))
		return
	}

	h.writeChatResponse(w, rc, true, served)
}

// planHedge asks hedgePlan (call-order step 13) whether to race the routed
// target against others. A plan is dispatched by G16, which owns this file
// from W3; hedgePlan returns none until then.
func (h *Handlers) planHedge(rc *models.RequestContext, orgCfg *tenant.OrgConfig) {
	targets := [1]routing.RoutingTarget{{ProviderID: rc.Provider, ModelOverride: rc.Request.Model}}
	_ = h.hedgePlan(rc, orgCfg, targets[:])
}

// callChat makes one non-stream upstream attempt (call-order steps 14 and
// 15): beginAttempt, rewriteUpstreamRouting, the call, then the attempt's
// finish. prev is the previous attempt's error, nil for the first.
func (h *Handlers) callChat(ctx context.Context, rc *models.RequestContext, p providers.Provider, providerID string, kind models.AttemptKind, prev error) (*models.ChatCompletionResponse, error) {
	strict := capbudget.RequestAdmission(rc) != nil
	if strict {
		// First reject plugin/caller additions; then qualify the exact payload
		// after the trusted final rewrite, before any billable attempt exists.
		if err := capbudget.RecheckCapChat(rc); err != nil {
			return nil, err
		}
		h.rewriteUpstreamRouting(rc, p, rc.Request)
		if err := capbudget.RecheckForwardedCapChat(rc); err != nil {
			return nil, err
		}
	}
	attemptCtx, finish, refusal := h.startAttempt(ctx, rc, kind, h.attemptReason(prev), providerID, rc.Request.Model, credentialType(rc), p)
	if refusal != "" { // I3: an attempt past the deadline is never dispatched
		return nil, routing.SkipHop(prev, refusal)
	}
	if !strict {
		h.rewriteUpstreamRouting(rc, p, rc.Request)
	}
	rc.CredentialSource = credentialSourceOf(p, rc.Metadata["org_id"])
	resp, err := p.ChatCompletion(attemptCtx, rc.Request)
	stampProvider(err, providerID)
	// Record the outcome before post-plugins finalize admission accounting.
	if attemptCtx.Err() == context.DeadlineExceeded {
		rc.Flags.Timeout = true
	}
	if resp == nil {
		finish(nil, nil, err)
	} else {
		finish(resp, resp.Usage, err)
	}
	return resp, err
}

// runNonStreamWithFailover runs the request through fo, the failover
// chatFailover chose: with of, the customer key's over its org's own
// providers, whose target ids map to the org's instances (of.target) and
// whose errors move down the model fallback chain only as effective, the
// failover in force for the org, decides; without, the gateway's failover
// over its providers, which tries the chain on any error. key is the model
// fo's router knows the request by.
func (h *Handlers) runNonStreamWithFailover(ctx context.Context, rc *models.RequestContext, fo, effective *routing.Failover, orgCfg *tenant.OrgConfig, of *orgFailover, key string) (providers.Provider, error) {
	originalModel := rc.Request.Model
	orgID := rc.Metadata["org_id"]
	var servingProvider providers.Provider

	// Wrap the failover loop inside engine.Process so pre-plugins run once,
	// then the failover retries only the provider call, then post-plugins run once.
	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		ctx, release := withRequestDeadline(ctx, rc) // I3: a retry backoff ends at the request deadline
		defer release()
		h.planHedge(rc, orgCfg)
		hops := failoverHops{routed: of.routedProvider(rc)}
		foResult, err := fo.Execute(ctx, key, func(ctx context.Context, providerID string, modelOverride string) error {
			model := originalModel
			if modelOverride != "" {
				model = modelOverride
			}
			var p providers.Provider
			targetID := providerID
			if of != nil {
				var err error
				if p, providerID, err = of.target(h, targetID, model); err != nil {
					return err
				}
			} else {
				var ok bool
				if p, ok = h.registry.GetProvider(providerID); !ok {
					return fmt.Errorf("provider %q not found", providerID)
				}
				if shouldApplyOrgProviderOverride(rc) {
					p = h.applyOrgProviderOverride(orgID, orgCfg, providerID, p)
				}
			}
			kind, admitted := hops.next(h, rc, orgCfg, providerID, model)
			if !admitted {
				return hops.lastErr
			}

			// Reset for this attempt.
			rc.Provider = providerID
			rc.Response = nil
			rc.ResolvedModel = ""
			rc.Request.Model = model

			var started time.Time
			if of != nil {
				started = time.Now()
			}
			resp, err := h.callChat(ctx, rc, p, providerID, kind, hops.lastErr)
			hops.lastErr = err
			if of != nil {
				of.record(targetID, started, err)
			}
			if err != nil {
				return err
			}
			servingProvider = p
			rc.Response = resp
			rc.ResolvedModel = resp.Model
			return nil
		})

		if err != nil && of != nil && !shouldTryModelFallback(effective, rc, orgCfg, err) {
			return err
		}
		if err != nil {
			// Provider failover exhausted — try the model fallback plan. It is
			// resolved on the route routing fixed (fallback_plan.go), never on
			// rc.Request.Model, which the failover has rewritten to a target's
			// model_override.
			modelChain := h.effectiveFallbackPlan(rc, rc.FallbackRoute())

			lastErr, skipsBefore := err, rc.SkippedHopCount()
			for _, fbModel := range modelChain {
				// Resolve and admit the fallback model: org providers first, then
				// the global registry, in the request's billing mode.
				fbProvider, fbProviderID, resolvedModel, ok := h.admitModelFallback(ctx, orgID, orgCfg, rc, fbModel)
				if !ok {
					continue
				}

				rc.Provider = fbProviderID
				rc.Request.Model = resolvedModel

				fbResp, fbCallErr := h.callChat(ctx, rc, fbProvider, fbProviderID, fallbackKind(rc), lastErr)
				lastErr = fbCallErr
				if fbCallErr == nil {
					servingProvider = fbProvider
					rc.Response = fbResp
					rc.ResolvedModel = fbResp.Model
					rc.Flags.FallbackUsed = true
					rc.Metadata["original_model"] = originalModel
					rc.Metadata["fallback_model"] = resolvedModel
					return nil
				}
			}

			return chainError(rc, skipsBefore, err, lastErr)
		}

		// Record failover metadata.
		if foResult.FallbackUsed {
			rc.Flags.FallbackUsed = true
			rc.Metadata["failover_original_provider"] = of.recorded(foResult.OriginalProvider)
		}
		rc.Metadata["failover_attempts"] = fmt.Sprintf("%d", foResult.Attempts)
		if foResult.StrategyName != "" {
			rc.Metadata["routing_strategy"] = foResult.StrategyName
		}
		if of != nil {
			of.stampStrategy(rc)
		}
		return nil
	}

	err := h.engine.Process(ctx, rc, providerCall)
	return servingProvider, err
}

// failoverHops tells the attempts of one provider-failover run apart: a call
// to the provider tried last is a retry, a call to another is a failover hop,
// which must be admitted first (call-order step 14).
type failoverHops struct {
	prevProvider string
	lastErr      error // the previous attempt's error
	// routed is the provider the route judged, when an org failover runs
	// the request (orgFailover.routedProvider): a first attempt its strategy
	// sends elsewhere is admitted as a hop is.
	routed string
}

// next returns the kind of the attempt about to be made on model at
// providerID, and whether it may be made. A refused hop is not dispatched: it
// is recorded as a skipped attempt (as a skipped hop, apart from the
// attempts, when it would change the request's billing mode), and lastErr
// becomes the hop-skipped sentinel wrapping the previous attempt's error,
// which the caller returns to Failover.Execute. Execute neither retries the
// hop nor records a breaker result for it, and keeps the previous error as
// the run's.
func (f *failoverHops) next(h *Handlers, rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string) (models.AttemptKind, bool) {
	kind := models.AttemptPrimary
	switch {
	case f.prevProvider == "":
		if f.routed != "" && providerID != f.routed && !f.admit(h, rc, orgCfg, kind, providerID, model) {
			return kind, false
		}
	case providerID == f.prevProvider:
		kind = models.AttemptRetry
	default:
		kind = models.AttemptFailover
		if !f.admit(h, rc, orgCfg, kind, providerID, model) {
			return kind, false
		}
	}
	f.prevProvider = providerID
	return kind, true
}

// admit admits a hop of kind to model on providerID, or records why not and
// makes lastErr the hop-skipped sentinel. A hop that would change the
// request's billing mode goes to the skipped-hop record (mixed_billing.go);
// any other refusal admitHop makes is recorded as a skipped attempt.
func (f *failoverHops) admit(h *Handlers, rc *models.RequestContext, orgCfg *tenant.OrgConfig, kind models.AttemptKind, providerID, model string) bool {
	if reason := h.mixedBillingRefusal(rc, credentialType(rc)); reason != "" {
		h.recordCrossModeSkip(rc, kind, reason, providerID, model)
		f.lastErr = routing.SkipHop(f.lastErr, reason)
		return false
	}
	if reason := h.admitHop(rc, orgCfg, providerID, model); reason != "" {
		h.recordSkippedHop(rc, kind, reason, providerID, model)
		f.lastErr = routing.SkipHop(f.lastErr, reason)
		return false
	}
	return true
}
