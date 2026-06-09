// Package gemini implements the InboundTranslator for the "google"
// api_format — translating Gemini generateContent wire format into the
// gateway's canonical OpenAI ChatCompletionRequest shape, and back.
//
// Method implementations live in:
//   - to_openai.go  — RequestToCanonical
//   - to_gemini.go  — ResponseFromCanonical, ErrorFromCanonical
//   - stream.go     — StreamEventsFromCanonical
package gemini

import (
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// Translator implements translation.InboundTranslator for the Google Gemini
// generateContent API format.
type Translator struct{}

// New returns a new *Translator. The zero value is valid, but New() is the
// conventional constructor used by init().
func New() *Translator { return &Translator{} }

// Name returns the api_format identifier for this translator.
func (t *Translator) Name() string { return "google" }

// init registers this translator with the package-level registry so that
// translation.InboundFor("google") works as soon as any package imports
// this one — mirroring the pattern used by provider packages.
func init() { translation.Register(New()) }
