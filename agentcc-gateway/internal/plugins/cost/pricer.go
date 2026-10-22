package cost

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Pricer finds what a model costs. It reads the injected price lookup (the
// catalog first, then the other price sources) and the
// model database at call time, never at construction, so a lookup the
// wiring replaced is the one it uses.
type Pricer struct {
	modelDB func() *modeldb.ModelDB
	lookup  func() models.PriceLookup // nil: the model database only
}

// NewPricer returns a Pricer reading modelDB and lookup, both called per
// price. lookup may be nil, or return nil, for the model database alone.
func NewPricer(modelDB func() *modeldb.ModelDB, lookup func() models.PriceLookup) *Pricer {
	if modelDB == nil {
		modelDB = func() *modeldb.ModelDB { return nil }
	}
	return &Pricer{modelDB: modelDB, lookup: lookup}
}

// Card returns the price card of model served by providerID through
// endpointID, and the cost source an attempt priced from it records.
//
// A card the injected lookup returns from a source other than the model
// database wins (the catalog, a price card). Otherwise the model database
// prices it, with the cache-write, reasoning and request prices the injected
// default does not carry. A model database price filed under the provider's
// own "<provider>/<model>" entry, where a managed provider's catalog is
// filed, is recorded as catalog for a managed attempt; any other is modeldb.
func (p *Pricer) Card(providerID, model, endpointID string, managed bool) (models.PriceCard, models.CostSource, bool) {
	if card, ok := p.injected(providerID, model, endpointID); ok && card.Source != models.PriceSourceModelDB {
		return card, costSourceOf(card.Source), true
	}
	prices, ok := p.modelDB().Prices(providerID, model)
	if !ok {
		return models.PriceCard{}, models.CostSourceUnpriced, false
	}
	if managed && prices.Qualified {
		return prices.Card, models.CostSourceCatalog, true
	}
	return prices.Card, models.CostSourceModelDB, true
}

func (p *Pricer) injected(providerID, model, endpointID string) (models.PriceCard, bool) {
	if p.lookup == nil {
		return models.PriceCard{}, false
	}
	lookup := p.lookup()
	if lookup == nil {
		return models.PriceCard{}, false
	}
	return lookup(providerID, model, endpointID)
}

// Priced reports whether model can be priced on endpoint: chat, responses,
// messages and completions need an input, output or request price,
// embeddings an input price, images a per-image price, speech a per-character
// price, transcription and translation a per-second price, and rerank a
// per-query or per-request price. endpoint is a capability name
// ("chat.completions", "audio.speech") or a request's endpoint type
// ("chat", "speech"); any other endpoint is not priced. Installed as the
// gateway's injected `priced`.
func (p *Pricer) Priced(endpoint, model string) bool {
	kind := endpointKindOf(endpoint)
	if kind == kindNone || model == "" {
		return false
	}
	for _, c := range lookupCandidates(model) {
		if card, ok := p.injected(c.provider, c.model, ""); ok && cardPrices(card, kind) {
			return true
		}
	}
	prices, ok := p.modelDB().Prices("", model)
	if !ok {
		return false
	}
	switch kind {
	case kindSpeech:
		return prices.PerCharacter > 0
	case kindSeconds:
		return prices.PerSecond > 0
	case kindRerank:
		return prices.PerQuery > 0 || prices.Card.Request > 0
	default:
		return cardPrices(prices.Card, kind)
	}
}

type lookupCandidate struct{ provider, model string }

// lookupCandidates are the (provider, model) pairs a provider-less model id
// may be filed under: the id as given, and "<provider>/<model>" split at its
// first slash, the way the catalog keys a managed provider's models.
func lookupCandidates(model string) []lookupCandidate {
	out := []lookupCandidate{{"", model}}
	if provider, rest, ok := strings.Cut(model, "/"); ok && provider != "" && rest != "" {
		out = append(out, lookupCandidate{provider, rest})
	}
	return out
}

// endpointKind is how an endpoint's calls are priced.
type endpointKind int

const (
	kindNone      endpointKind = iota
	kindTokens                 // chat, responses, messages, completions
	kindEmbedding              // input tokens
	kindImage                  // per image
	kindSpeech                 // per input character
	kindSeconds                // per second of audio
	kindRerank                 // per search unit or request
)

func endpointKindOf(endpoint string) endpointKind {
	switch endpoint {
	case "", "chat", "chat.completions", "completions", "responses", "messages":
		return kindTokens
	case "embedding", "embeddings":
		return kindEmbedding
	case "image", "images", "images.generations":
		return kindImage
	case "speech", "speech_stream", "audio.speech":
		return kindSpeech
	case "transcription", "translation", "audio.transcriptions", "audio.translations":
		return kindSeconds
	case "rerank":
		return kindRerank
	default:
		return kindNone
	}
}

// cardPrices reports whether card prices calls of kind. A card has no
// per-character, per-second or per-query line; of those it prices only
// rerank, through its per-request fee.
func cardPrices(card models.PriceCard, kind endpointKind) bool {
	switch kind {
	case kindTokens:
		return card.Prompt > 0 || card.Completion > 0 || card.Request > 0
	case kindEmbedding:
		return card.Prompt > 0
	case kindImage:
		return card.Image > 0
	case kindRerank:
		return card.Request > 0
	default:
		return false
	}
}
