package openai

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ResponsesBinding uses the same immutable endpoint/headers and invocation
// credential as CreateResponse and StreamResponse. A config reload preserves
// valid continuations only when the upstream namespace is unchanged. Neither
// the credential nor configured header values leave this one-way fingerprint.
func (p *Provider) ResponsesBinding(ctx context.Context) string {
	req := &http.Request{Header: make(http.Header)}
	p.headers.Apply(req)
	identity, _ := json.Marshal(struct {
		Provider   string
		Endpoint   string
		Credential string
		Headers    http.Header
	}{p.id, p.endpoint("/v1/responses"), p.credential(ctx), req.Header})
	return models.CredentialFingerprint(string(identity))
}
