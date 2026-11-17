package auth

// This file is not built in this tree (the go tool ignores testdata). It runs
// Test733DeployOrderNeverChangesReadinessOrKeys against a pre-733 gateway:
// extract agentcc-gateway from the pre-733 commit (bbaf95c973), copy
// deploy_order_733_test.go into its internal/auth, and copy this file there as
// deploy_order_gateway_test.go. Only the pre-733 rows run there.

import "github.com/futureagi/agentcc-gateway/internal/config"

const deployOrderGateway = deployOrderPre733

// A pre-733 store has no mode: it loads every config key as given and every
// synced key, whether or not a control plane URL is set.
func deployOrderStore(cfg config.AuthConfig, _ bool) *KeyStore {
	return NewKeyStore(cfg)
}

// A pre-733 config seed never waits for a key list.
func deployOrderSeedPending(*KeyStore, string) bool {
	return false
}
