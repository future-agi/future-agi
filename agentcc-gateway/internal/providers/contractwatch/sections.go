package contractwatch

import (
	"net/http"
	"sort"
	"strings"
)

// Spec says how model-watch reaches one registry section's provider: the
// environment variables it needs (the same ones cmd/contract-recorder reads,
// so one set of secrets serves both) and how to build its lister.
type Spec struct {
	// Env lists variables that must all be set; a section missing any is
	// skipped, never failed.
	Env []string
	// Help is shown with the skip message.
	Help string
	// NewLister builds the lister; nil when the provider has no list API
	// (list "static" or "none" in the registry).
	NewLister func(env func(string) string, client *http.Client) Lister
}

// Specs is keyed by registry section name.
var Specs = map[string]Spec{
	"anthropic": {
		Env: []string{"ANTHROPIC_API_KEY"},
		NewLister: func(env func(string) string, c *http.Client) Lister {
			return AnthropicLister{BaseURL: "https://api.anthropic.com", APIKey: env("ANTHROPIC_API_KEY"), Client: c}
		},
	},
	"openai": {
		Env: []string{"OPENAI_API_KEY"},
		NewLister: func(env func(string) string, c *http.Client) Lister {
			return OpenAILister{BaseURL: "https://api.openai.com", Path: "/v1/models", APIKey: env("OPENAI_API_KEY"), Client: c}
		},
	},
	"gemini": {
		Env:  []string{"GEMINI_API_KEY"},
		Help: "Google AI Studio key; Vertex AI is not listed",
		NewLister: func(env func(string) string, c *http.Client) Lister {
			return GeminiLister{BaseURL: "https://generativelanguage.googleapis.com", APIKey: env("GEMINI_API_KEY"), Client: c}
		},
	},
	"cohere": {
		Env: []string{"COHERE_API_KEY"},
		NewLister: func(env func(string) string, c *http.Client) Lister {
			return CohereLister{BaseURL: "https://api.cohere.com", APIKey: env("COHERE_API_KEY"), Client: c}
		},
	},
	"bedrock": {
		Env:  []string{"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
		Help: "AWS_SESSION_TOKEN is optional; the region is AWS_REGION, else AWS_DEFAULT_REGION, else us-east-1",
		NewLister: func(env func(string) string, c *http.Client) Lister {
			return BedrockLister{
				Region: BedrockRegion(env),
				Creds: awsCredentials{
					AccessKeyID:     env("AWS_ACCESS_KEY_ID"),
					SecretAccessKey: env("AWS_SECRET_ACCESS_KEY"),
					SessionToken:    env("AWS_SESSION_TOKEN"),
				},
				Client: c,
			}
		},
	},
	"groq": {
		Env:  []string{"GROQ_API_KEY"},
		Help: "for the openai suite's groq@<model> fixtures",
		NewLister: func(env func(string) string, c *http.Client) Lister {
			return OpenAILister{BaseURL: "https://api.groq.com", Path: "/openai/v1/models", APIKey: env("GROQ_API_KEY"), Client: c}
		},
	},
	// The other OpenAI-compatible presets the openai suite has fixtures for
	// (<preset>@<model>), listed at the preset's base URL and path prefix
	// (providers.KnownProviders; sections_test.go pins them) with the
	// recorder's <PRESET>_API_KEY. The registry keeps the aggregators
	// (openrouter, together, fireworks) at list "none": see their notes.
	"mistral":    compatSpec("mistral", "https://api.mistral.ai", "/v1/models"),
	"cerebras":   compatSpec("cerebras", "https://api.cerebras.ai", "/v1/models"),
	"together":   compatSpec("together", "https://api.together.xyz", "/v1/models"),
	"fireworks":  compatSpec("fireworks", "https://api.fireworks.ai/inference", "/v1/models"),
	"openrouter": compatSpec("openrouter", "https://openrouter.ai/api", "/v1/models"),
	// Perplexity's Sonar models have no list API (its GET /v1/models lists
	// the Agent API's models, not the chat-completions ones the preset
	// calls), so the registry holds a static list. The key is still required:
	// a NEW model is only worth reporting if it can be recorded.
	"perplexity": {
		Env:  []string{"PERPLEXITY_API_KEY"},
		Help: "for the openai suite's perplexity@<model> fixtures",
	},
	// Azure serves deployments a customer names, not models; there is nothing
	// to list. Its credentials still gate -newest re-recording.
	"azure": {
		Env:  []string{"AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT"},
		Help: "the azure fixtures' model IDs must be deployment names on that resource",
	},
	// The gemini suite's Vertex AI fixtures (vertex@<publisher model>). Vertex
	// needs a short-lived OAuth access token, which a scheduled job cannot
	// hold, so the section is never listed; the variables (the recorder's)
	// gate -newest for a local run.
	"vertex": {
		Env:  []string{"VERTEX_PROJECT", "VERTEX_ACCESS_TOKEN"},
		Help: "for the gemini suite's vertex@<model> fixtures: VERTEX_ACCESS_TOKEN from `gcloud auth print-access-token`",
	},
}

// compatSpec lists an OpenAI-compatible preset with <PRESET>_API_KEY, the
// variable cmd/contract-recorder reads for the openai suite's
// <preset>@<model> fixtures.
func compatSpec(preset, baseURL, path string) Spec {
	env := strings.ToUpper(preset) + "_API_KEY"
	return Spec{
		Env:  []string{env},
		Help: "for the openai suite's " + preset + "@<model> fixtures",
		NewLister: func(e func(string) string, c *http.Client) Lister {
			return OpenAILister{BaseURL: baseURL, Path: path, APIKey: e(env), Client: c}
		},
	}
}

// SpecNames returns Specs' keys, sorted.
func SpecNames() []string {
	names := make([]string, 0, len(Specs))
	for n := range Specs {
		names = append(names, n)
	}
	sort.Strings(names)
	return names
}

// MissingEnv returns the variables s needs that env does not set.
func (s Spec) MissingEnv(env func(string) string) []string {
	var missing []string
	for _, v := range s.Env {
		if env(v) == "" {
			missing = append(missing, v)
		}
	}
	return missing
}

// BedrockRegion is the region the Bedrock lister and cmd/contract-recorder
// use: AWS_REGION, else AWS_DEFAULT_REGION, else us-east-1.
func BedrockRegion(env func(string) string) string {
	for _, v := range []string{env("AWS_REGION"), env("AWS_DEFAULT_REGION")} {
		if v != "" {
			return v
		}
	}
	return "us-east-1"
}
