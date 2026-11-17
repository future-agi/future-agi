package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const hostedActivationEnv = "AGENTCC_HOSTED_ONEKEY_ACTIVATION"

func TestHostedOneKeyActivation(t *testing.T) {
	const provider = "providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: openai, managed: true%s}}\n"
	for _, tc := range []struct {
		name, flag, extra, suffix, want string
	}{
		{name: "default off", want: "hosted preparation refuses providers.*.managed=true"},
		{name: "explicit off", flag: "false", want: "hosted preparation refuses providers.*.managed=true"},
		{name: "on", flag: "true"},
		{name: "aggregator preset", flag: "true", extra: ", type: openrouter"},
		{name: "invalid flag", flag: "not-a-boolean", want: "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{name: "TLS bypass", flag: "true", extra: ", skip_tls: true", want: "insecure or local provider overrides"},
		{name: "local", flag: "true", extra: ", local: true", want: "insecure or local provider overrides"},
		{name: "local kind", flag: "true", extra: ", type: ollama", want: "hosted one-key requires an allowed managed provider kind"},
		{name: "unknown kind", flag: "true", extra: ", type: unreviewed", want: "hosted one-key requires an allowed managed provider kind"},
		{name: "mixed billing profile", flag: "true", suffix: "managed: {mixed_billing_chains: true, spend_guard: {max_usd_per_minute: 5}}\n", want: "hosted one-key refuses managed.mixed_billing_chains=true"},
		{name: "mixed chain", flag: "true", suffix: "routing: {model_fallbacks: {catalogue/model: [token/model]}}\n", want: "hosted profile refuses model fallback chains, failover and retries"},
		{name: "failover", flag: "true", suffix: "routing: {failover: {enabled: true}}\n", want: "hosted profile refuses model fallback chains, failover and retries"},
		{name: "retry", flag: "true", suffix: "routing: {retry: {enabled: true}}\n", want: "hosted profile refuses model fallback chains, failover and retries"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			text := hostedFixture(t) + fmt.Sprintf(provider, tc.extra) + tc.suffix
			t.Setenv(hostedActivationEnv, tc.flag)
			assertHostedOneKeyLoad(t, text, tc.want)
		})
	}
}

func assertHostedOneKeyLoad(t *testing.T, text, want string) {
	t.Helper()
	err := loadHostedFixture(t, text)
	if want == "" {
		if err != nil {
			t.Fatalf("qualified activation refused: %v", err)
		}
		return
	}
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Fatalf("wanted refusal %q, got %v", want, err)
	}
	for _, name := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET", hostedActivationEnv} {
		value := os.Getenv(name)
		if len(value) > 10 && strings.Contains(err.Error(), value) {
			t.Fatal("refusal exposed an operator value")
		}
	}
}

func TestHostedOneKeyTransportAndFormatRefusals(t *testing.T) {
	for _, tc := range []struct{ url, want string }{
		{"http://upstream.synthetic.invalid", "credential-free HTTPS"},
		{"https://user:password@upstream.synthetic.invalid", "credential-free HTTPS"},
		{"https://upstream.synthetic.invalid?credential=synthetic", "credential-free HTTPS"},
		{"https://upstream.synthetic.invalid#fragment", "credential-free HTTPS"},
		{"https://127.0.0.1", "private provider URLs"},
		{"https://10.0.0.1", "private provider URLs"},
		{"https://169.254.169.254", "private provider URLs"},
		{"https://[::1]", "private provider URLs"},
		{"https://[::ffff:127.0.0.1]", "private provider URLs"},
		{"https://localhost", "development provider URLs"},
		{"https://api.localhost.", "development provider URLs"},
		{"https://api.local", "development provider URLs"},
	} {
		t.Run(tc.url, func(t *testing.T) {
			text := hostedFixture(t)
			t.Setenv(hostedActivationEnv, "true")
			assertHostedOneKeyLoad(t, text+fmt.Sprintf("providers: {paid: {base_url: '%s', api_format: openai, managed: true}}\n", tc.url), tc.want)
		})
	}
	for _, format := range []string{"anthropic", "bedrock", "gemini", "google", "cohere", "azure", "deepgram", "cartesia", "OpenAI", "unverified"} {
		t.Run(format, func(t *testing.T) {
			text := hostedFixture(t)
			t.Setenv(hostedActivationEnv, "true")
			assertHostedOneKeyLoad(t, text+fmt.Sprintf("providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: %s, managed: true}}\n", format), "hosted one-key requires managed providers.*.api_format=openai")
		})
	}
}

func TestHostedOneKeyOffPreservesPreparationRouting(t *testing.T) {
	text := hostedFixture(t) + "routing: {model_fallbacks: {first: [second]}, failover: {enabled: true}, retry: {enabled: true}}\n"
	assertHostedOneKeyLoad(t, text, "")
	t.Setenv(hostedActivationEnv, "true")
	assertHostedOneKeyLoad(t, text, "hosted profile refuses model fallback chains, failover and retries")
}

func TestHostedOneKeyOperatorScopeAndReload(t *testing.T) {
	text := hostedFixture(t) + "providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: openai, managed: true}}\n"
	t.Setenv(hostedActivationEnv, "")
	assertHostedOneKeyLoad(t, text+"hosted: {onekey_activation: true}\n", "hosted preparation refuses providers.*.managed=true")
	t.Setenv(hostedActivationEnv, "true")
	assertHostedOneKeyLoad(t, text, "")
	t.Setenv(hostedActivationEnv, "false")
	assertHostedOneKeyLoad(t, text, "hosted preparation refuses providers.*.managed=true")
	// Hosted activation neither opts organizations in nor changes self-hosted
	// adapter policy. YAML cannot select or override the operator flag.
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "self-hosted")
	t.Setenv(hostedActivationEnv, "true")
	path := filepath.Join(t.TempDir(), "self-hosted.yaml")
	if err := os.WriteFile(path, []byte("providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: anthropic, managed: true}}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err != nil {
		t.Fatalf("hosted flag changed self-hosted policy: %v", err)
	}
}
