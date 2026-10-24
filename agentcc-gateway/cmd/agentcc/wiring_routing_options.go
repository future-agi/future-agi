package main

import "github.com/futureagi/agentcc-gateway/internal/routing/reqopts"

// initRoutingOptions configures the key that hashes org ids into the upstream
// user of managed attempts (managed.upstream_user_hash_key; the config loader
// applies the AGENTCC_MANAGED_USER_HASH_KEY override). Configuration always
// wins over reqopts' lazy environment read; an empty key logs a startup
// warning and selects the unkeyed fallback.
func initRoutingOptions(d *wiringDeps) error {
	reqopts.ConfigureUpstreamUser(d.cfg.Managed.UpstreamUserHashKey)
	return nil
}
