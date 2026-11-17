package models

// ModelCap is the set of models a request's upstream attempts may serve.
// A delegated Console run's grant names them (fe1c field 20); the server
// stamps the cap on the request from the verified grant alone
// (server.peekDelegatedKey), never from a header, the body or metadata, so a
// caller can neither set nor widen it. The zero value caps nothing.
type ModelCap struct {
	// Capped is set when the request is held to Models.
	Capped bool
	// Models are the ids the request may serve, compared exactly
	// (case-sensitively). Empty with Capped set permits no model.
	Models []string
	// Refused is set once an attempt was refused for serving RefusedModel,
	// a model outside the cap: the request then answers 403
	// model_not_permitted (server.attemptLimitError).
	Refused      bool
	RefusedModel string
}

// Permits reports whether the cap lets a request serve model: always when
// nothing is capped, else only for one of Models, exactly. "" is never
// permitted under a cap.
func (c ModelCap) Permits(model string) bool {
	if !c.Capped {
		return true
	}
	if model == "" {
		return false
	}
	for _, id := range c.Models {
		if id == model {
			return true
		}
	}
	return false
}

// clone is the cap for a copy of the request (CloneRequest): the same models
// in a slice of its own, and no refusal, since the copy's attempts are its
// own.
func (c ModelCap) clone() ModelCap {
	return ModelCap{Capped: c.Capped, Models: append([]string(nil), c.Models...)}
}
