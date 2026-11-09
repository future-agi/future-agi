package main

import (
	"errors"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

// initMCPCredentials is the MCP wiring step "credentials" (mcpWiringOrder):
// with mcp.django.base_url and its own secret (mcp.django.internal_token_secret,
// AGENTCC_MCP_DJANGO_SECRET) set, a call on a per_user server runs with the
// calling user's upstream token, fetched just in time from Django and kept
// sealed in Redis (credentials.Resolver). Without Redis every call fetches
// its token. The same resolver gives a shared OAuth server (credential kind
// oauth, CN24) its organization's signed-in account's token as each request
// is sent. Without the Django channel, per_user servers refuse their calls
// (step 13) and shared OAuth servers do not connect. Setting only one of the
// two keys, or a secret equal to an admin token, stops the gateway from
// starting. Owner: M25.
func initMCPCredentials(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled || !d.cfg.MCP.UpstreamOAuthEnabled {
		return nil
	}
	cfg := d.cfg.MCP
	switch {
	case cfg.Django.BaseURL == "" && cfg.Django.InternalTokenSecret == "":
		slog.Info("mcp per-user credentials are off: mcp.django.base_url is not set, so per_user servers refuse their calls")
		return nil
	case cfg.Django.BaseURL == "":
		return errors.New("mcp.django.internal_token_secret (AGENTCC_MCP_DJANGO_SECRET) is set without mcp.django.base_url")
	}
	tokens, err := credentials.NewTokenClient(credentials.TokenClientOptions{
		BaseURL:     cfg.Django.BaseURL,
		Secret:      cfg.Django.InternalTokenSecret,
		AdminTokens: []string{d.cfg.Admin.Token, d.cfg.ControlPlane.AdminToken},
	})
	if err != nil {
		return err
	}
	codec, err := mcp.NewStateCodec(cfg.StateKeys, cfg.DevInsecureState)
	if err != nil {
		return err
	}
	var cache *credentials.TokenCache
	if d.redis != nil {
		cache = credentials.NewTokenCache(d.redis, codec, credentials.CacheOptions{})
	} else {
		slog.Warn("mcp per-user tokens are not cached: Redis is unavailable, so every per_user call fetches its token from Django")
	}
	// Connect links are sealed with the key Django's start page opens them
	// with: one derived from the secret the gateway shares with Django.
	links, err := credentials.ConnectCodec(cfg.Django.InternalTokenSecret)
	if err != nil {
		return errors.New("mcp.django.internal_token_secret (AGENTCC_MCP_DJANGO_SECRET) cannot seal connect links: it must be at least 32 bytes")
	}
	resolver, err := credentials.NewResolver(credentials.ResolverOptions{
		Tokens: tokens, Cache: cache, Codec: codec, ConnectCodec: links, DjangoBaseURL: cfg.Django.BaseURL,
	})
	if err != nil {
		return err
	}
	deps.CredentialResolver = resolver
	if !cfg.K2AppsEnabled {
		deps.CredentialResolver = k2DisabledResolver{resolver}
	}
	deps.SharedCredentials = resolver
	return nil
}
