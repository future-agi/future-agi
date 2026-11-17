package redisstate_test

import "github.com/futureagi/agentcc-gateway/internal/auth"

// Only the callback adapter changes; retained r3 assertions are unmodified.
func applyCredentialTestMessage(ks *auth.KeyStore, message string) {
	if receiver, ok := interface{}(ks).(interface{ ApplyRevocation(string) bool }); ok {
		receiver.ApplyRevocation(message)
	} else {
		ks.Revoke(message)
	}
}
