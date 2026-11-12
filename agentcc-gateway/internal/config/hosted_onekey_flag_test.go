package config

import (
	"fmt"
	"testing"
)

// 708 add.40 (b), environment side: on the hosted profile only the exact
// value "true" of AGENTCC_HOSTED_ONEKEY_ACTIVATION lets a managed provider
// load. Every other spelling refuses startup without echoing it, and unset
// or "false" keeps the preparation refusal. (The flag is read in one place:
// cmd/agentcc TestHostedOneKeyFlagReadInOnePlace.)
func TestHostedOneKeyFlagOnlyExactTrueEnables(t *testing.T) {
	const provider = "providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: openai, managed: true}}\n"
	for i, tc := range []struct{ value, want string }{
		{"true", ""},
		{"", "hosted preparation refuses providers.*.managed=true"},
		{"false", "hosted preparation refuses providers.*.managed=true"},
		{"1", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"t", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"TRUE", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"True", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"yes", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"on", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{" true", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"true ", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"0", "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
	} {
		t.Run(fmt.Sprintf("%d", i), func(t *testing.T) {
			text := hostedFixture(t) + provider
			t.Setenv(hostedActivationEnv, tc.value)
			assertHostedOneKeyLoad(t, text, tc.want)
		})
	}
}
