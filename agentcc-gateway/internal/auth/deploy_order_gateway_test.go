package auth

import "github.com/futureagi/agentcc-gateway/internal/config"

// deployOrderGateway is the gateway generation this tree builds, for
// Test733DeployOrderNeverChangesReadinessOrKeys. Its pre-733 column replaces
// this file with testdata/deploy_order_gateway_pre733_test.go in the pre-733
// tree.
const deployOrderGateway = deployOrder733

// deployOrderStore makes the store main.go makes: control-plane mode exactly
// when a control plane URL is set (ControlPlaneConfig.SyncsKeys).
func deployOrderStore(cfg config.AuthConfig, controlPlane bool) *KeyStore {
	return NewKeyStore(cfg, controlPlane)
}

// deployOrderSeedPending is whether a config seed answers 503
// key_list_not_loaded rather than 401 or serving.
func deployOrderSeedPending(ks *KeyStore, raw string) bool {
	return ks.ConfigKeyPending(raw)
}
