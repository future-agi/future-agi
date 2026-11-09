package openai

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func (p *Provider) credential(ctx context.Context) string {
	return models.InclusiveCredential(ctx, p.id, p.apiKey)
}
func (p *Provider) InclusiveCredentialIdentity(ctx context.Context) string {
	return models.CredentialFingerprint(p.credential(ctx))
}

// Pin the actual secret inside the invocation context; a registry reload between
// route resolution and dispatch cannot bind one key while sending another.
func (p *Provider) BindInclusiveCredential(ctx context.Context) (context.Context, string) {
	token := p.credential(ctx)
	return models.WithInclusiveCredential(ctx, p.id, token), models.CredentialFingerprint(token)
}
