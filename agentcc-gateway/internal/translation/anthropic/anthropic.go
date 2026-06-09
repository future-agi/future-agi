// Package anthropic implements the InboundTranslator for the "anthropic"
// api_format — translating Anthropic Messages API wire format into the
// gateway's canonical OpenAI ChatCompletionRequest shape, and back.
//
// File layout:
//
//	anthropic.go    — Translator struct, Name(), compile-time assertions, init()
//	types.go        — local Anthropic request/response struct types
//	to_openai.go    — RequestToCanonical  (Anthropic → OpenAI canonical)
//	to_anthropic.go — ResponseFromCanonical + ErrorFromCanonical
//	stream.go       — StreamEventsFromCanonical (incremental state machine)
package anthropic

import (
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// Translator implements translation.InboundTranslator for the Anthropic
// Messages API format.
//
// All interface methods are implemented across the sibling files in this
// package; Go allows method sets to span multiple files within a single package.
type Translator struct{}

// New returns a new *Translator. The zero value is valid, but New() is the
// conventional constructor used by init().
func New() *Translator { return &Translator{} }

// Name returns the api_format identifier for this translator.
func (t *Translator) Name() string { return "anthropic" }

// Compile-time assertion that *Translator satisfies translation.InboundTranslator.
var _ translation.InboundTranslator = (*Translator)(nil)

// init registers this translator with the package-level registry so that
// translation.InboundFor("anthropic") works as soon as any package imports
// this one — mirroring the pattern used by provider packages.
func init() { translation.Register(New()) }
