package main

import (
	"fmt"
	"net/url"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// upstream is how to reach one provider's real API: where it lives, the
// adapter config (credentials included) and which values must never reach a
// file or a log.
type upstream struct {
	baseURL string
	cfg     config.ProviderConfig
	// secrets are redacted from anything the recorder saves or prints.
	secrets []string
	// signedBody means the adapter signs the request body (AWS SigV4), so the
	// recorder cannot change it: an upstream_patch.json is refused.
	signedBody bool
}

// providerSpec says which environment variables a provider needs and how to
// turn them into an upstream. The key is the adapter package name, which is
// also the name its contract test passes to contracttest.Discover and the
// directory under --out.
type providerSpec struct {
	// need lists environment variables that must all be non-empty.
	need []string
	// help is shown with the skip message when a variable is missing.
	help  string
	build func(env func(string) string) upstream
}

var providerSpecs = map[string]providerSpec{
	"anthropic": {
		need: []string{"ANTHROPIC_API_KEY"},
		build: func(env func(string) string) upstream {
			return keyed("https://api.anthropic.com", "anthropic", env("ANTHROPIC_API_KEY"))
		},
	},
	"openai": {
		need: []string{"OPENAI_API_KEY"},
		build: func(env func(string) string) upstream {
			return keyed("https://api.openai.com", "openai", env("OPENAI_API_KEY"))
		},
	},
	"gemini": {
		need: []string{"GEMINI_API_KEY"},
		help: "Google AI Studio key (generativelanguage.googleapis.com); Vertex AI is not supported by the recorder",
		build: func(env func(string) string) upstream {
			return keyed("https://generativelanguage.googleapis.com", "gemini", env("GEMINI_API_KEY"))
		},
	},
	"cohere": {
		need: []string{"COHERE_API_KEY"},
		build: func(env func(string) string) upstream {
			return keyed("https://api.cohere.com", "cohere", env("COHERE_API_KEY"))
		},
	},
	"azure": {
		need: []string{"AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT"},
		help: "AZURE_OPENAI_ENDPOINT is https://<resource>.openai.azure.com; every recorded model ID must be a deployment name on that resource; AZURE_OPENAI_API_VERSION is optional",
		build: func(env func(string) string) upstream {
			u := keyed(env("AZURE_OPENAI_ENDPOINT"), "azure", env("AZURE_OPENAI_KEY"))
			if v := env("AZURE_OPENAI_API_VERSION"); v != "" {
				u.cfg.Headers = map[string]string{"api-version": v}
			}
			return u
		},
	},
	"bedrock": {
		need: []string{"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
		help: "AWS_SESSION_TOKEN is optional; the region is AWS_REGION, else AWS_DEFAULT_REGION, else us-east-1",
		build: func(env func(string) string) upstream {
			region := firstNonEmpty(env("AWS_REGION"), env("AWS_DEFAULT_REGION"), "us-east-1")
			return upstream{
				baseURL: "https://bedrock-runtime." + region + ".amazonaws.com",
				cfg: config.ProviderConfig{
					APIFormat:          "bedrock",
					AWSAccessKeyID:     env("AWS_ACCESS_KEY_ID"),
					AWSSecretAccessKey: env("AWS_SECRET_ACCESS_KEY"),
					AWSSessionToken:    env("AWS_SESSION_TOKEN"),
					AWSRegion:          region,
				},
				secrets:    []string{env("AWS_SECRET_ACCESS_KEY"), env("AWS_SESSION_TOKEN"), env("AWS_ACCESS_KEY_ID")},
				signedBody: true,
			}
		},
	},
}

// upstreamFor names the upstream a fixture's model key is recorded against,
// and the model ID that upstream knows. The openai suite files an
// OpenAI-compatible upstream as "<preset>@<upstream model>", the preset being
// a providers.KnownProviders entry (groq@openai/gpt-oss-120b); its
// request.json carries the bare upstream model. Every other key is the
// provider's own model ID ("@" is legal in Vertex-style IDs, so the split is
// limited to the openai provider and known presets).
func upstreamFor(provider, key string) (name, model string) {
	if provider == "openai" {
		preset, model, ok := strings.Cut(key, "@")
		if p, known := providers.KnownProviders[preset]; ok && known && model != "" && p.APIFormat == "openai" && p.BaseURL != "" {
			return preset, model
		}
	}
	return provider, key
}

// upstreamSpec is the spec for an upstream name from upstreamFor.
func upstreamSpec(provider, name string) providerSpec {
	if name == provider {
		return providerSpecs[provider]
	}
	return compatSpec(name)
}

// compatSpec reaches an OpenAI-compatible preset with <PRESET>_API_KEY. The
// adapter config names the preset as its type, so the registry fills in the
// format and API path prefix exactly as it does for config.yaml.
func compatSpec(preset string) providerSpec {
	p := providers.KnownProviders[preset]
	env := envName(preset) + "_API_KEY"
	return providerSpec{
		need: []string{env},
		help: "for the openai fixtures filed as " + preset + "@<model>",
		build: func(e func(string) string) upstream {
			return upstream{
				baseURL: p.BaseURL,
				cfg:     config.ProviderConfig{APIKey: e(env), Type: preset},
				secrets: []string{e(env)},
			}
		},
	}
}

// envName upper-cases s and turns anything but letters and digits into "_".
func envName(s string) string {
	return strings.Map(func(r rune) rune {
		switch {
		case 'a' <= r && r <= 'z':
			return r - 'a' + 'A'
		case 'A' <= r && r <= 'Z', '0' <= r && r <= '9':
			return r
		}
		return '_'
	}, s)
}

func keyed(baseURL, format, key string) upstream {
	return upstream{
		baseURL: baseURL,
		cfg:     config.ProviderConfig{APIKey: key, APIFormat: format},
		secrets: []string{key},
	}
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}

// knownProviders lists the providers the recorder can reach, sorted.
func knownProviders() []string {
	names := make([]string, 0, len(providerSpecs))
	for n := range providerSpecs {
		names = append(names, n)
	}
	sort.Strings(names)
	return names
}

// missingEnv returns the variables spec needs that are unset.
func (s providerSpec) missingEnv(env func(string) string) []string {
	var missing []string
	for _, v := range s.need {
		if env(v) == "" {
			missing = append(missing, v)
		}
	}
	return missing
}

// adapterConfig is the provider config the recorder hands to the gateway's
// registry. The base URL keeps the real host and path but uses plain http,
// and dial routes every connection to the teeing proxy, which makes the real
// https call: the adapter's own transport is otherwise not replaceable, and
// keeping the host means a SigV4 signature (which covers host, path and body,
// not the scheme) still verifies upstream.
func (u upstream) adapterConfig(real *url.URL, dial dialFunc) config.ProviderConfig {
	cfg := u.cfg
	cfg.BaseURL = "http://" + real.Host + strings.TrimRight(real.EscapedPath(), "/")
	cfg.DialContext = dial
	off := false
	cfg.AutoDiscover = &off // never probe /v1/models
	cfg.Local = &off        // no local defaults, no connectivity check
	return cfg
}

// parseBaseURL validates an upstream base URL.
func parseBaseURL(raw string) (*url.URL, error) {
	u, err := url.Parse(strings.TrimRight(raw, "/"))
	if err != nil {
		return nil, fmt.Errorf("base URL %q: %v", raw, err)
	}
	if u.Scheme != "https" && u.Scheme != "http" || u.Host == "" {
		return nil, fmt.Errorf("base URL %q: want http(s)://host[/path]", raw)
	}
	if u.RawQuery != "" || u.Fragment != "" || u.User != nil {
		return nil, fmt.Errorf("base URL %q: must not carry a query, fragment or user info", raw)
	}
	return u, nil
}
