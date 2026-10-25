package cost

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// attemptCost is what pricing one attempt found.
type attemptCost struct {
	managed, billable, priced, estimated bool
	upstream, billed, landed             int64
	upstreamFeeBps, processingFeeBps     int
	source                               models.CostSource
}

// FinalizeRequest prices every attempt of rc and fills rc.Billing.
//
// Each attempt is priced on its own (PriceAttempt) and its costs are written
// back with rc.UpdateAttempt. The attempt that served is billable; any other
// is billable only when the upstream reported tokens for it, since one that
// failed before the upstream counted anything charged nothing. A cache hit
// made no upstream call and is not billable. A request whose attempts no one
// recorded is priced as the one attempt it made.
//
// rc.Billing.BilledMicros is the sum of the billable managed attempts'
// billed micros, never re-rounded, and the request's "cost" metadata is
// exactly that amount for a managed request (the BYOK upstream sum for a BYOK
// one), so what the client is shown is what settles.
func (p *Plugin) FinalizeRequest(rc *models.RequestContext) {
	if rc.Flags.CacheHit {
		rc.Metadata["cost_source"] = string(models.CostSourceCacheHit)
		rc.Billing.CostSource = logCostSource(models.CostSourceCacheHit, nil, -1)
		return
	}
	attempts := rc.AttemptsSnapshot()
	recorded := len(attempts) > 0
	if !recorded {
		if !served(rc) {
			return // the request failed before anything was served
		}
		attempts = []models.Attempt{implicitAttempt(rc)}
	}
	serving := servingIndex(rc, attempts)
	costs := make([]attemptCost, len(attempts))
	for i := range attempts {
		costs[i] = p.priceAttempt(rc, &attempts[i], i == serving)
		if recorded {
			c := costs[i]
			rc.UpdateAttempt(attempts[i].AttemptID, func(a *models.Attempt) { c.applyTo(a) })
		}
	}
	p.writeRequestBilling(rc, costs, serving)
}

// applyTo records c on its attempt.
func (c attemptCost) applyTo(a *models.Attempt) {
	a.Billable = c.billable
	a.UsageEstimated = a.UsageEstimated || c.estimated
	if !c.billable {
		return
	}
	a.UpstreamCostMicros, a.BilledCostMicros, a.CostSource = c.upstream, c.billed, c.source
	if c.managed {
		a.LandedCostMicros, a.UpstreamFeeBps, a.ProcessingFeeBps = c.landed, c.upstreamFeeBps, c.processingFeeBps
	}
}

// priceAttempt prices a, which served the request when serving is true.
func (p *Plugin) priceAttempt(rc *models.RequestContext, a *models.Attempt, serving bool) attemptCost {
	c := attemptCost{managed: a.CredentialType == models.CredentialManaged}
	if serving {
		c.estimated = a.UsageEstimated || rc.Metadata["usage_estimated"] == "true"
	}
	usage := attemptUsage(rc, a, serving)
	if !serving && (usage == nil || usage.PromptTokens+usage.CompletionTokens == 0) {
		return c // failed before the upstream counted anything
	}
	c.billable = true

	fees := p.fees()
	if c.managed {
		c.upstreamFeeBps, c.processingFeeBps = fees.UpstreamFeeBps[a.Provider], fees.ProcessingFeeBps
	}
	priced := *a
	priced.ProcessingFeeBps = c.processingFeeBps

	var usd float64
	var ok, modality bool
	if serving {
		usd, c.source, ok, modality = p.priceEndpoint(rc, a)
	}
	switch {
	case modality:
		if ok {
			c.upstream = usdToMicros(usd)
			c.billed, c.landed = billedAndLanded(c.upstream, c.managed, p.markupBps, c.upstreamFeeBps, c.processingFeeBps)
		}
	case usage == nil:
		ok = false // the response carried no usage to price
	default:
		card, source := p.cardFor(rc, a, serving, c.managed, usage)
		var got string
		c.upstream, c.billed, c.landed, got, ok = PriceAttempt(&priced, usage, card, p.markupBps, c.upstreamFeeBps)
		c.source = source
		if got == string(models.CostSourceProviderReported) {
			c.source = models.CostSourceProviderReported
		}
		p.addServerToolFees(rc, &priced, usage, card, ok, &c)
	}
	if c.priced = ok; !ok {
		c.upstream, c.billed, c.landed, c.source = 0, 0, 0, models.CostSourceUnpriced
		if serving && usedNothing(rc, usage, modality) {
			// The answer reported zero tokens, characters or seconds: there
			// was nothing to price, so the request is not flagged unpriced.
			c.source = ""
			return c
		}
		if c.managed {
			countUnpricedManaged()
			slog.Warn("managed attempt has no price; it was not billed",
				"request_id", rc.RequestID, "provider", a.Provider, "model", a.Model, "endpoint", rc.EndpointType)
		} else if serving && usedBillable(rc, usage, modality) {
			// The operator hears once per model how to price it (a managed
			// attempt is logged above, every time, as it is not billed).
			p.warnUnpriced(rc, a.Model, priceHintFor(rc.EndpointType))
		}
	}
	return c
}

// attemptUsage is the usage a is priced on, as its own copy: the response's
// for the attempt that served (every prompt token at the cached rate under
// the cache_status "hit"), else the token counts recorded on a.
func attemptUsage(rc *models.RequestContext, a *models.Attempt, serving bool) *models.Usage {
	if !serving {
		if a.PromptTokens+a.CompletionTokens == 0 {
			return nil
		}
		return &models.Usage{PromptTokens: a.PromptTokens, CompletionTokens: a.CompletionTokens}
	}
	var u *models.Usage
	switch {
	case rc.EndpointType == "embedding":
		if rc.EmbeddingResponse != nil && rc.EmbeddingResponse.Usage != nil {
			u = &models.Usage{PromptTokens: rc.EmbeddingResponse.Usage.PromptTokens}
		}
	case rc.Response != nil && rc.Response.Usage != nil:
		cp := *rc.Response.Usage
		u = &cp
	}
	if u != nil && rc.Metadata["cache_status"] == "hit" {
		u.ParseDetails()
		u.PromptDetails.CachedTokens = u.PromptTokens
		u.PromptDetails.CacheWriteTokens = 0
	}
	return u
}

// implicitAttempt is the one attempt a request made when no attempt was
// recorded for it.
func implicitAttempt(rc *models.RequestContext) models.Attempt {
	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}
	credType := models.CredentialBYOK
	if rc.Metadata["billing_mode"] == models.BillingModeManaged {
		credType = models.CredentialManaged
	}
	return models.Attempt{
		Provider: rc.Provider, Model: model, CredentialType: credType, Outcome: models.OutcomeSuccess,
		EndpointID: rc.Billing.EndpointID, ServedUpstreamProvider: rc.Billing.ServedUpstreamProvider,
	}
}

// servingIndex is the attempt that served the request: the last that
// succeeded, else the last one, or -1 when nothing was served.
func servingIndex(rc *models.RequestContext, attempts []models.Attempt) int {
	if !served(rc) {
		return -1
	}
	for i := len(attempts) - 1; i >= 0; i-- {
		if attempts[i].Outcome == models.OutcomeSuccess {
			return i
		}
	}
	return len(attempts) - 1
}

// served reports whether the request got an answer to price.
func served(rc *models.RequestContext) bool {
	switch rc.EndpointType {
	case "embedding":
		return rc.EmbeddingResponse != nil
	case "image":
		return rc.ImageResponse != nil
	case "speech", "speech_stream", "speech_relay":
		return rc.Metadata["input_characters"] != "" // set once the audio came back, or the relay closed
	case "transcription":
		return rc.TranscriptionResp != nil
	case "transcription_relay":
		return rc.Metadata["audio_seconds"] != "" // set when the relay closed
	case "translation":
		return rc.TranslationResp != nil
	case "rerank":
		return rc.RerankResponse != nil
	case "ocr":
		return rc.OCRResponse != nil
	default:
		return rc.Response != nil
	}
}

// usedNothing reports whether the serving attempt's answer reported that it
// used nothing: zero tokens, zero characters of speech, or a measured zero
// seconds of audio. An answer with no usage at all, or audio whose length
// could not be read, did not report zero and stays unpriced.
func usedNothing(rc *models.RequestContext, usage *models.Usage, modality bool) bool {
	switch {
	case isSpeechEndpoint(rc.EndpointType):
		return speechCharacters(rc) == 0
	case isAudioSecondsEndpoint(rc.EndpointType):
		secs, measured := audioSeconds(rc)
		return measured && secs == 0
	case modality:
		return false
	default:
		return usage != nil && usage.PromptTokens+usage.CompletionTokens == 0
	}
}

// usedBillable reports whether the serving attempt used something billable:
// tokens, or the units its endpoint is priced in (characters of speech,
// seconds of audio, images, pages, rerank searches).
func usedBillable(rc *models.RequestContext, usage *models.Usage, modality bool) bool {
	switch {
	case isSpeechEndpoint(rc.EndpointType):
		return speechCharacters(rc) > 0
	case isAudioSecondsEndpoint(rc.EndpointType):
		secs, measured := audioSeconds(rc)
		return measured && secs > 0
	case modality:
		return true
	default:
		return usage != nil && usage.PromptTokens+usage.CompletionTokens > 0
	}
}
