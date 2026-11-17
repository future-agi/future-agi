package models

// GenerationEvidence is upstream evidence, never a gateway-generated response ID
// or the requested/routed model. It stays attached to one attempt. Conflicting
// events retain the last observed identity and require reconciliation; an empty
// terminal event cannot erase an earlier observation.
//
// Kept internal until the versioned settlement sidecar is supported by the drain.
// Do not append these fields to the closed managed-settlement-v1/log-v2 records.
type GenerationEvidence struct {
	ID          string
	ServedModel string
	Provider    string
	Conflict    bool
}

func (e *GenerationEvidence) Observe(next GenerationEvidence) {
	observe := func(dst *string, value string) {
		if value == "" {
			return
		}
		if *dst != "" && *dst != value {
			e.Conflict = true
		}
		*dst = value
	}
	observe(&e.ID, next.ID)
	observe(&e.ServedModel, next.ServedModel)
	observe(&e.Provider, next.Provider)
	e.Conflict = e.Conflict || next.Conflict
}

func GenerationFromResponse(response any) GenerationEvidence {
	switch r := response.(type) {
	case *ChatCompletionResponse:
		if r != nil {
			return GenerationEvidence{ID: r.ID, ServedModel: r.Model, Provider: r.UpstreamProvider}
		}
	case *StreamChunk:
		if r != nil {
			return GenerationEvidence{ID: r.ID, ServedModel: r.Model, Provider: r.UpstreamProvider, Conflict: r.GenerationConflict}
		}
	}
	return GenerationEvidence{}
}

// ProviderBillingEvidence survives an HTTP/error event without putting billing
// data in the client error metadata. Usage is observed, never synthesized here.
type ProviderBillingEvidence struct {
	Generation GenerationEvidence
	Usage      *Usage
}
