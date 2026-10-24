package cost

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// priceModality prices the attempt that served an image, speech (whole,
// streamed or relayed), transcription (including a relayed one), translation
// or rerank request, in USD: per image by size and quality, per character of
// speech input, per second of audio, and per
// search unit (or per request) of rerank. modality is false for any other
// endpoint, which is priced on tokens. ok is false when the model has no
// price for the endpoint, or the gateway could not measure the units (a
// transcription of audio whose length it cannot read).
func (p *Plugin) priceModality(rc *models.RequestContext, a *models.Attempt) (usd float64, source models.CostSource, ok, modality bool) {
	switch rc.EndpointType {
	case "image":
		usd, source, ok = p.imageCost(rc, a)
	case "speech", "speech_stream", "speech_relay":
		usd, source, ok = p.unitCost(a, speechCharacters(rc), func(pr unitPrices) float64 { return pr.perCharacter })
	case "transcription", "translation", "transcription_relay":
		secs, measured := audioSeconds(rc)
		if !measured {
			return 0, models.CostSourceUnpriced, false, true
		}
		usd, source, ok = p.unitCost(a, secs, func(pr unitPrices) float64 { return pr.perSecond })
	case "rerank":
		usd, source, ok = p.rerankCost(rc, a)
	default:
		return 0, "", false, false
	}
	return usd, source, ok, true
}

// imageCost is the images served times one image's price: the injected
// card's per-image price when it comes from the catalog or a price card, else
// the model database's price for the requested size and quality.
func (p *Plugin) imageCost(rc *models.RequestContext, a *models.Attempt) (float64, models.CostSource, bool) {
	n := len(rc.ImageResponse.Data)
	var size, quality string
	if req := rc.ImageRequest; req != nil {
		size, quality = req.Size, req.Quality
		if n == 0 && req.N != nil {
			n = *req.N
		}
	}
	n = max(n, 1)
	pr := p.pricer()
	if card, ok := pr.injected(a.Provider, a.Model, endpointID(rc, a)); ok && card.Source != models.PriceSourceModelDB && card.Image > 0 {
		return float64(n) * card.Image, costSourceOf(card.Source), true
	}
	if price, ok := pr.modelDB().ImagePrice(a.Model, size, quality); ok {
		return float64(n) * price, models.CostSourceModelDB, true
	}
	return 0, models.CostSourceUnpriced, false
}

// rerankCost is the request's search units times the per-query price, or
// the per-request fee. A search unit is one query over up to 100 documents,
// the rule the per-query rerank prices are quoted under. The units are
// counted from the request's documents; the response's billed_units are
// not read.
func (p *Plugin) rerankCost(rc *models.RequestContext, a *models.Attempt) (float64, models.CostSource, bool) {
	units := 1.0
	if req := rc.RerankRequest; req != nil && len(req.Documents) > documentsPerSearchUnit {
		units = float64((len(req.Documents) + documentsPerSearchUnit - 1) / documentsPerSearchUnit)
	}
	if usd, source, ok := p.unitCost(a, units, func(pr unitPrices) float64 { return pr.perQuery }); ok {
		return usd, source, true
	}
	if card, source, ok := p.pricer().Card(a.Provider, a.Model, endpointID(rc, a), a.CredentialType == models.CredentialManaged); ok && card.Request > 0 {
		return card.Request, source, true
	}
	return 0, models.CostSourceUnpriced, false
}

// documentsPerSearchUnit is how many documents one rerank search unit
// covers.
const documentsPerSearchUnit = 100

// unitPrices are the per-unit prices only the model database carries.
type unitPrices struct{ perCharacter, perSecond, perQuery float64 }

// unitCost is units times the per-unit price rate picks from the model
// database's prices for a's model.
func (p *Plugin) unitCost(a *models.Attempt, units float64, rate func(unitPrices) float64) (float64, models.CostSource, bool) {
	prices, ok := p.pricer().modelDB().Prices(a.Provider, a.Model)
	if !ok {
		return 0, models.CostSourceUnpriced, false
	}
	price := rate(unitPrices{prices.PerCharacter, prices.PerSecond, prices.PerQuery})
	if price <= 0 {
		return 0, models.CostSourceUnpriced, false
	}
	return units * price, models.CostSourceModelDB, true
}

// speechCharacters is the length of the text a speech request voiced, which
// the handler records once the upstream returned the audio.
func speechCharacters(rc *models.RequestContext) float64 {
	if n, err := strconv.Atoi(rc.Metadata["input_characters"]); err == nil && n >= 0 {
		return float64(n)
	}
	return 0
}

// audioSeconds is the length of a transcription's audio, which the handler
// records when it can read it.
func audioSeconds(rc *models.RequestContext) (float64, bool) {
	secs, err := strconv.ParseFloat(rc.Metadata["audio_seconds"], 64)
	if err != nil || secs < 0 {
		return 0, false
	}
	return secs, true
}

// isSpeechEndpoint reports whether endpoint voices text, priced per
// character: a whole, a streamed, or a relayed speech request.
func isSpeechEndpoint(endpoint string) bool {
	switch endpoint {
	case "speech", "speech_stream", "speech_relay":
		return true
	}
	return false
}

// isAudioSecondsEndpoint reports whether endpoint is priced per second of
// audio: a transcription (including a relayed one) or a translation.
func isAudioSecondsEndpoint(endpoint string) bool {
	switch endpoint {
	case "transcription", "translation", "transcription_relay":
		return true
	}
	return false
}
