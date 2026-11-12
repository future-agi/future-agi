package server

import (
	"context"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"maps"
	"net/http"
	"slices"
	"time"
)

// routedStream is the admitted, response-bound stream start shared by chat and
// Messages. The caller owns cancellation only after this function succeeds.
type routedStream struct {
	chunks   <-chan models.StreamChunk
	errors   <-chan error
	first    *models.StreamChunk
	prefixes []models.StreamChunk
	provider providers.Provider
	ctx      context.Context
	cancel   context.CancelFunc
}

func (h *Handlers) openRoutedStream(ctx context.Context, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig, defaultRetry bool) (*routedStream, error) {
	// No attempt runs on an unfixed plan route: routing fixed it already
	// (resolveRoute, AnthropicMessages), so this only ever fixes one for a
	// request that reached dispatch without routing, from its state before
	// any attempt (fallback_plan.go).
	fixFallbackRoute(rc)
	var chunks <-chan models.StreamChunk
	var errCh <-chan error
	var firstChunk *models.StreamChunk
	var streamPrefixes []models.StreamChunk
	var servingProvider providers.Provider
	var upstreamStreamCancel context.CancelFunc
	var upstreamStreamCtx context.Context // the serving attempt's upstream call (I3: streamCutError)

	effectiveFailover := h.effectiveFailover(orgCfg)

	// openStream makes one stream attempt (call-order steps 14 and 15): it
	// ends when the first chunk arrives or the attempt fails. prev is the
	// previous attempt's error, nil for the first.
	//
	// callCtx bounds an attempt only until its first chunk. The stream itself
	// lives on the request context: failover cancels callCtx as soon as the
	// attempt returns, which would otherwise end the stream after one chunk.
	openStream := func(callCtx context.Context, callProvider providers.Provider, providerID string, kind models.AttemptKind, prev error) error {
		beginCtx, finish, refusal := h.startAttempt(callCtx, rc, kind, h.attemptReason(prev), providerID, rc.Request.Model, credentialType(rc), callProvider)
		if refusal != "" { // I3: an attempt past the deadline is never dispatched
			return routing.SkipHop(prev, refusal)
		}
		// The attempt startAttempt just recorded, the request's latest.
		attemptID := latestStreamAttempt(rc)
		h.rewriteUpstreamRouting(rc, callProvider, rc.Request)
		attemptCtx, attemptCancel := streamAttemptContext(ctx, rc) // I3: the deadline cuts a stream that started
		// Preserve the admitted credential without inheriting the short-lived
		// failover context's cancellation after the first chunk.
		attemptCtx = models.CopyInclusiveCredential(attemptCtx, beginCtx)
		attemptCtx, accepted := models.ObserveStreamAcceptance(attemptCtx)
		// Whether the start was sent and answered, observed
		// as a non-stream call is (dispatchTrace).
		clearDispatchedWithoutUsage(rc)
		clearCallEnd(rc)
		var sent dispatchTrace
		attemptCtx = sent.trace(attemptCtx)
		// A failed start cancels, but does not join, the provider goroutine.
		// Keep rc.Request as the routing/logging state; no later hop may write
		// the request that this hop is still encoding. Per-hop rewrites also
		// change Extra keys and message headers, so isolate those containers.
		request := *rc.Request
		request.Extra = maps.Clone(rc.Request.Extra)
		request.Messages = slices.Clone(rc.Request.Messages)
		candidateChunks, candidateErrCh := callProvider.StreamChatCompletion(attemptCtx, &request)
		if candidateChunks == nil {
			attemptCancel()
			err := models.ErrUpstreamProvider(http.StatusBadGateway, "upstream provider returned no stream")
			finish(nil, nil, err)
			return err
		}

		chunk, prefixes, err := waitForManagedStreamStart(beginCtx, candidateChunks, candidateErrCh, credentialType(rc) == models.CredentialManaged)
		stampProvider(err, providerID)
		var usage *models.Usage
		if chunk != nil {
			usage = chunk.Usage
		}
		if usage.NeedsTokenEstimate() && err != nil && accepted.Load() && ctx.Err() == context.Canceled {
			usage = billing.CompleteUsage(usage, estimatedManagedStreamUsage(rc, &streamedOutput{}))
		}
		// A managed start that was sent and got no answer at all (the
		// deadline cut it before any response byte, or the transport failed
		// once the upstream had it) is charged at its own hold, the same class
		// as a non-stream call (markDispatchedWithoutUsage). A start that got
		// an upstream status, or that its caller stopped before any answer,
		// stays void. Read before finish, which
		// releases the attempt's context.
		unanswered := err != nil && usage.NeedsTokenEstimate() && !callerStoppedStream(ctx, false) && sent.unanswered(beginCtx, err)
		// Why a start that was sent ended before its first chunk, including
		// a caller's stop (not charged at the hold, but still why it ended).
		if err != nil && (unanswered || (sent.written.Load() && callerStoppedStream(ctx, false))) {
			markCallEnd(rc, callEndAfter(err, attemptCtx, beginCtx))
		}
		finish(chunk, usage, err)
		if unanswered {
			markDispatchedWithoutUsage(rc, attemptID)
		}
		if err != nil {
			cancelAndDrainStream(attemptCancel, candidateChunks)
			return err
		}

		if upstreamStreamCancel != nil {
			cancelAndDrainStream(upstreamStreamCancel, chunks)
		}
		upstreamStreamCancel = attemptCancel
		upstreamStreamCtx = attemptCtx
		servingProvider = callProvider
		chunks = candidateChunks
		errCh = candidateErrCh
		firstChunk = chunk
		streamPrefixes = prefixes
		return nil
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		ctx, release := withRequestDeadline(ctx, rc) // I3: a retry backoff ends at the request deadline
		defer release()
		originalModel := rc.Request.Model
		orgID := rc.Metadata["org_id"]
		h.planHedge(rc, orgCfg)

		tryModelFallbacks := func(firstErr error) error {
			if !shouldTryModelFallback(effectiveFailover, rc, orgCfg, firstErr) {
				return firstErr
			}

			// The request's fallback plan, as admission judged it: resolved
			// on the route routing fixed (fallback_plan.go), never on
			// rc.Request.Model, which a provider failover has rewritten to a
			// target's model_override.
			modelChain := h.effectiveFallbackPlan(rc, rc.FallbackRoute())

			lastErr, skipsBefore := firstErr, rc.SkippedHopCount()
			for _, fbModel := range modelChain {
				fallbackModel := fbModel
				fbProvider, fbProviderID, resolvedModel, ok := h.admitModelFallback(ctx, orgID, orgCfg, rc, fallbackModel)
				if !ok {
					continue
				}

				rc.Provider = fbProviderID
				rc.Request.Model = resolvedModel
				err := h.retryRoutedCall(ctx, orgCfg, false, fallbackKind(rc), lastErr, func(kind models.AttemptKind, previous error) error {
					return openStream(ctx, fbProvider, fbProviderID, kind, previous)
				})
				if err == nil {
					rc.Flags.FallbackUsed = true
					rc.Metadata["original_model"] = originalModel
					rc.Metadata["fallback_model"] = resolvedModel
					return nil
				}
				lastErr = err
			}

			rc.Request.Model = originalModel
			return chainError(rc, skipsBefore, firstErr, lastErr)
		}

		// The failover chatFailover chose: with of, a customer key's over its
		// org's own providers (target ids map to the org's instances).
		if fo, of, key := h.chatFailover(ctx, rc, orgCfg, effectiveFailover); fo != nil {
			hops := failoverHops{routed: of.routedProvider(rc)}
			result, err := fo.Execute(ctx, key, func(callCtx context.Context, providerID string, modelOverride string) error {
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
						return models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf("provider %q not found", providerID))
					}
					if shouldApplyOrgProviderOverride(rc) {
						p = h.applyOrgProviderOverride(orgID, orgCfg, providerID, p)
					}
				}
				kind, admitted := hops.next(h, rc, orgCfg, providerID, model, p)
				if !admitted {
					return hops.lastErr
				}
				rc.Request.Model = model
				var started time.Time
				if of != nil {
					started = time.Now()
				}
				err := openStream(callCtx, p, providerID, kind, hops.lastErr)
				hops.lastErr = err
				if of != nil {
					of.record(callCtx, targetID, started, err)
				}
				return err
			})
			if err != nil {
				return tryModelFallbacks(err)
			}
			if result != nil {
				rc.Provider = of.recorded(result.ProviderID)
				if result.ModelOverride != "" {
					rc.Request.Model = result.ModelOverride
				}
				if result.FallbackUsed {
					rc.Flags.FallbackUsed = true
					rc.Metadata["original_provider"] = of.recorded(result.OriginalProvider)
					rc.Metadata["fallback_provider"] = of.recorded(result.ProviderID)
				}
				if result.StrategyName != "" {
					rc.Metadata["routing_strategy"] = result.StrategyName
				}
				if of != nil {
					of.stampStrategy(rc)
				}
			}
			return nil
		}

		if err := h.retryRoutedCall(ctx, orgCfg, defaultRetry, models.AttemptPrimary, nil, func(kind models.AttemptKind, previous error) error {
			return openStream(ctx, provider, rc.Provider, kind, previous)
		}); err != nil {
			return tryModelFallbacks(err)
		}
		return nil
	}

	err := providerCall(ctx, rc)
	if err != nil {
		if upstreamStreamCancel != nil {
			upstreamStreamCancel()
		}
		return nil, err
	}
	return &routedStream{chunks: chunks, errors: errCh, first: firstChunk, prefixes: streamPrefixes, provider: servingProvider, ctx: upstreamStreamCtx, cancel: upstreamStreamCancel}, nil
}
