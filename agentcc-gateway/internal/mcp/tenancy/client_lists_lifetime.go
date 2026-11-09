package tenancy

import (
	"context"
	"encoding/json"
)

func (t *timeoutClient) ListTools(ctx context.Context) ([]json.RawMessage, error) {
	ctx, done, err := t.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer done()
	raw, err := t.UpstreamClient.ListTools(ctx)
	if err = t.finish(ctx, err); err != nil {
		return nil, err
	}
	return raw, nil
}
func (t *timeoutClient) ListResources(ctx context.Context) ([]json.RawMessage, error) {
	ctx, done, err := t.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer done()
	raw, err := t.UpstreamClient.ListResources(ctx)
	if err = t.finish(ctx, err); err != nil {
		return nil, err
	}
	return raw, nil
}
func (t *timeoutClient) ListPrompts(ctx context.Context) ([]json.RawMessage, error) {
	ctx, done, err := t.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer done()
	raw, err := t.UpstreamClient.ListPrompts(ctx)
	if err = t.finish(ctx, err); err != nil {
		return nil, err
	}
	return raw, nil
}
func (t *timeoutClient) Healthy() bool {
	return (t.life == nil || t.life.Err() == nil) && (t.grant == nil || t.grant.Lifetime == nil || t.grant.Lifetime.Err() == nil) && t.UpstreamClient.Healthy()
}
func (t *timeoutClient) Close() error {
	if t.cleanup != nil {
		t.cleanup()
	}
	return t.UpstreamClient.Close()
}

// CredentialLifetime lets the entry reconnect a shared OAuth client when its
// pinned grant ends. Retained handles keep their original, ended lifetime.
func (t *timeoutClient) CredentialLifetime() context.Context       { return t.life }
func (t *timeoutClient) CheckCredential(ctx context.Context) error { return t.finish(ctx, nil) }
