package providers

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// presetHealth is where one OpenAI-compatible preset (KnownProviders) sends
// requests today, and whether the provider's own sources say it serves its
// API there.
type presetHealth struct {
	// Preset is the KnownProviders key.
	Preset string
	// Calls are the URLs the preset's provider, built by NewRegistry as the
	// gateway builds it, sends a request to today, by endpoint (chat,
	// list_models, transcription, translation).
	Calls map[string]string
	// Source is where the provider documents its API's address, as
	// repo@commit:path with the full commit, and what it says.
	Source string
	// Gap is "" when Source documents the API at Calls. Otherwise it is the
	// preset's defect, "known gap <ID>: <reason>"; KNOWN_GAPS.md lists it.
	Gap string
	// Unverified says what no reachable source could confirm, when the
	// finding rests on indirect evidence. The Gap then says "unverified".
	Unverified string
}

// presetHealthTable is every OpenAI-format preset in KnownProviders. The
// sources were read on 2026-10-28; most of the providers' docs and API hosts
// are unreachable from where they were read, so the evidence is their
// official SDKs, docs repositories and cookbooks.
var presetHealthTable = []presetHealth{
	{
		Preset: "anyscale",
		Calls:  map[string]string{"chat": "https://api.endpoints.anyscale.com/v1/chat/completions"},
		Source: "anyscale/endpoint-cookbook@25ff9da22990328128a010c3b3236b1b5ab160e5:App_AgentTune/finetune_utils.py:230 " +
			"(api_base https://api.endpoints.anyscale.com/v1; the cookbook's last commit is 2024-03-22)",
		Gap: "known gap AN1: The anyscale preset still targets https://api.endpoints.anyscale.com/v1, which Anyscale has " +
			"likely withdrawn (unverified): its \"Changes to Anyscale Endpoints API\" notice ends multi-tenant access to the " +
			"Endpoints API from 2024-08-01, so every request through the preset may fail. Every anyscale@ cell is " +
			"unverifiable in contractcoverage/exemptions.json. If the notice is confirmed, drop the preset.",
		Unverified: "the notice is known from search snippets of docs.anyscale.com only; docs.anyscale.com, www.anyscale.com " +
			"and api.endpoints.anyscale.com are unreachable from here, and no GitHub source carries it",
	},
	{
		Preset: "cerebras",
		Calls:  map[string]string{"chat": "https://api.cerebras.ai/v1/chat/completions"},
		Source: "cerebras/cerebras-cloud-sdk-python@cacaf754daf384c7066e6584f06363b6f15f8599:src/cerebras/cloud/sdk/_client.py:103 " +
			"(base_url https://api.cerebras.ai; operations under /v1)",
	},
	{
		Preset: "deepinfra",
		Calls: map[string]string{
			"chat":        "https://api.deepinfra.com/v1/chat/completions",
			"list_models": "https://api.deepinfra.com/v1/models",
		},
		Source: "deepinfra/docs@7d28995cd8c9501cb2ee663b9dd4a69b2e0d23ed:account/authentication.mdx:80 " +
			"(https://api.deepinfra.com/v1/openai/chat/completions; the docs give 119 /v1/openai URLs and none of " +
			"/v1/chat/completions, /v1/embeddings or /v1/models); " +
			"huggingface/huggingface.js@49a4603dd75a0dd559f61fdeff2f79bc6210c46d:packages/inference/src/providers/deepinfra.ts:42,113 " +
			"(\"DeepInfra exposes OpenAI-compatible endpoints under the /v1/openai namespace\")",
		Gap: "known gap DI1: The deepinfra preset sends every request below https://api.deepinfra.com/v1 " +
			"(/v1/chat/completions, /v1/models), but DeepInfra serves its OpenAI-compatible API below " +
			"https://api.deepinfra.com/v1/openai, so no request through the preset reaches it. Fix: PathPrefix /v1/openai. Every deepinfra@ cell is not_served in " +
			"contractcoverage/exemptions.json until then. The docs are the evidence; api.deepinfra.com was not " +
			"reachable for a live probe.",
	},
	{
		Preset: "fireworks",
		Calls: map[string]string{
			"chat":          "https://api.fireworks.ai/inference/v1/chat/completions",
			"transcription": "https://api.fireworks.ai/inference/v1/audio/transcriptions",
			"translation":   "https://api.fireworks.ai/inference/v1/audio/translations",
		},
		Source: "fw-ai-external/python-sdk@374be9a61edba68214489c866c549c43a739b63a:src/fireworks/resources/chat/completions.py:1563 " +
			"(https://api.fireworks.ai/inference/v1/chat/completions); " +
			"fw-ai/cookbook@57968a2237b663be33078d2747a28463983ae134:archived/learn/audio/audio_prerecorded_speech_to_text/audio_prerecorded_speech_to_text.ipynb:112 " +
			"(https://audio-prod.us-virginia-1.direct.fireworks.ai/v1/audio/transcriptions; archived/learn/batch-api/batch_api.ipynb:113 " +
			"names audio-prod and audio-turbo)",
		Gap: "known gap FW3: Fireworks serves transcription and translation only on its audio hosts " +
			"(audio-prod and audio-turbo .us-virginia-1.direct.fireworks.ai/v1/audio/...), not under the fireworks " +
			"preset's https://api.fireworks.ai/inference/v1, so a transcription or translation request through the " +
			"preset fails. Chat and the rest of the preset are served at its base URL. The fireworks@ transcription and " +
			"translation cells are not_served in contractcoverage/exemptions.json. The cookbook is the evidence; " +
			"docs.fireworks.ai was not reachable to confirm.",
	},
	{
		Preset: "groq",
		Calls:  map[string]string{"chat": "https://api.groq.com/openai/v1/chat/completions"},
		Source: "groq/groq-python@55066d94acca028c3cb74e050f2711b80ade6298:src/groq/_client.py:91 " +
			"(base_url https://api.groq.com; operations under /openai/v1)",
	},
	{
		Preset: "huggingface",
		Calls: map[string]string{
			"chat":        "https://api-inference.huggingface.co/v1/chat/completions",
			"list_models": "https://api-inference.huggingface.co/v1/models",
		},
		Source: "huggingface/hub-docs@ac26a585391edeb10db1cf4aea4d4619c9648a2d:docs/inference-providers/index.md:177,492-502 " +
			"(base_url https://router.huggingface.co/v1, \"currently available for chat completion tasks only\"); " +
			"huggingface/huggingface.js@49a4603dd75a0dd559f61fdeff2f79bc6210c46d:packages/inference/src/config.ts:2 " +
			"(HF_ROUTER_URL https://router.huggingface.co)",
		Gap: "known gap HF1: The huggingface preset sends requests to api-inference.huggingface.co, a host Hugging Face " +
			"has retired: its OpenAI-compatible API is https://router.huggingface.co/v1 (chat completions, responses and " +
			"models), so every request through the preset fails. Fix: BaseURL https://router.huggingface.co, PathPrefix " +
			"/v1. Every huggingface@ cell is not_served in contractcoverage/exemptions.json until then. The docs " +
			"migration is the evidence (hub-docs 5c8bcc23971cbb112e2cd5ba3ad6018d5dbb3f49 replaced the last " +
			"api-inference examples with router URLs); the old host's HTTP 410 is corroborated by search results only.",
	},
	{
		Preset: "mistral",
		Calls:  map[string]string{"chat": "https://api.mistral.ai/v1/chat/completions"},
		Source: "mistralai/client-python@90e300904b64c36e0630f72ef475dbab7912068e:src/mistralai/client/sdkconfiguration.py:25 " +
			"(SERVER_GLOBAL https://api.mistral.ai; operations under /v1)",
	},
	{
		Preset: "openrouter",
		Calls:  map[string]string{"chat": "https://openrouter.ai/api/v1/chat/completions"},
		Source: "OpenRouterTeam/python-sdk@9a7be4e74bd3729f11d2138f048ab04c2b677482:src/openrouter/sdkconfiguration.py:22 " +
			"(SERVER_PRODUCTION https://openrouter.ai/api/v1)",
	},
	{
		Preset: "perplexity",
		Calls:  map[string]string{"chat": "https://api.perplexity.ai/v1/chat/completions"},
		Source: "perplexityai/perplexity-py@1a79c5d3a7b1ef8c2dce17c0f0aa7e23f9bbdced:src/perplexity/generated/api.py:4341 " +
			"(POST /chat/completions; embeddings, responses and models are under /v1); " +
			"ppl-ai/api-cookbook@eb1a3988fe56e9e1b2d72580fecf402dd8f6c0e2:docs/examples/disease-qa/disease_qa_tutorial.ipynb:90 " +
			"(https://api.perplexity.ai/chat/completions)",
		Gap: "known gap P4: The perplexity preset sends chat completions to https://api.perplexity.ai/v1/chat/completions, " +
			"while Perplexity's official SDK and cookbook call https://api.perplexity.ai/chat/completions and use /v1 only " +
			"for embeddings, responses and models; whether /v1/chat/completions is served too is unverified. The " +
			"perplexity@sonar fixtures pin the path the gateway sends; a recording settles it.",
		Unverified: "docs.perplexity.ai and api.perplexity.ai are unreachable from here, so whether Perplexity also " +
			"accepts /v1/chat/completions cannot be checked",
	},
	{
		Preset: "replicate",
		Calls: map[string]string{
			"chat":        "https://api.replicate.com/v1/chat/completions",
			"list_models": "https://api.replicate.com/v1/models",
		},
		Source: "replicate/replicate-python-stainless@48e138a15eeb44be36d871ad822b5e86d3852db3:api.md " +
			"(37 operations, .stats.yml configured_endpoints: 37; GET /models is the only one the gateway calls), " +
			"src/replicate/_client.py:140 (base_url https://api.replicate.com/v1)",
		Gap: "known gap RP2: The replicate preset points the OpenAI adapter at Replicate's own predictions API " +
			"(https://api.replicate.com/v1), which is not OpenAI-compatible: GET /v1/models is the only path the gateway " +
			"calls that it serves (in its own shape, gap RP1), so chat and every other request through the preset " +
			"fails. Fix: drop the preset, or keep it for the model list alone. The other replicate@ cells are " +
			"not_served in contractcoverage/exemptions.json. A third-party page cites an OpenAI-compatibility page on " +
			"replicate.com, which was not reachable; no official source mentions one.",
	},
	{
		Preset: "together",
		Calls:  map[string]string{"chat": "https://api.together.xyz/v1/chat/completions"},
		Source: "togethercomputer/together-py@3428f80887121c5715f670f3cffa71b2ed482c12:src/together/_client.py:156 " +
			"(base_url https://api.together.ai), CHANGELOG.md 3.0.0 (2026-10-22: /v1 removed from the base URL; chat " +
			"completions moved to https://api-inference.together.ai); " +
			"togethercomputer/together-python@807b79024fdcc4c3d37f49cc65b8d885322ae6b4:src/together/constants.py:12 " +
			"(the v1 SDK: BASE_URL https://api.together.xyz/v1)",
		Gap: "known gap TG8: The together preset still sends every request to https://api.together.xyz/v1, a host " +
			"Together's current SDK has moved off (together-py 3.0.0, 2026-10-22, uses https://api.together.ai, and " +
			"https://api-inference.together.ai for chat completions), so the preset depends on a legacy host Together " +
			"may retire (unverified). The v1 SDK (together-python) still uses api.together.xyz, so the old host is " +
			"taken to answer for now.",
		Unverified: "api.together.xyz and api.together.ai are unreachable from here, so whether the old host still " +
			"answers, and for how long, cannot be checked",
	},
	{
		Preset: "xai",
		Calls:  map[string]string{"chat": "https://api.x.ai/v1/chat/completions"},
		Source: "xai-org/xai-sdk-ts@51bfc686c74310226d601e6037fa6c2e562244f7:src/constants.ts:3 " +
			"(DEFAULT_BASE_URL https://api.x.ai/v1)",
	},
}

var (
	// presetGapDef is a presetHealth's Gap: a definition with an ID.
	presetGapDef = regexp.MustCompile(`^known gap [A-Z]{1,3}[1-9][0-9]*: \S`)
	// presetSourceRef is a source with a full commit: repo@<40 hex>:path.
	presetSourceRef = regexp.MustCompile(`[\w.-]+/[\w.-]+@[0-9a-f]{40}:\S`)
)

// TestPresetHealth pins where each OpenAI-compatible preset sends requests
// today: it builds the preset's provider through NewRegistry, as the gateway
// does, dials every connection to a local server, and compares each URL the
// provider calls with the table. The table's gaps are preset defects
// (KNOWN_GAPS.md); presets.go is not changed here. Fixing a preset fails
// this test: update its Calls, and delete its gap once Source documents the
// API there (and reclassify its contractcoverage/exemptions.json cells).
func TestPresetHealth(t *testing.T) {
	var want []string
	for name, p := range KnownProviders {
		if p.APIFormat == "openai" {
			want = append(want, name)
		}
	}
	sort.Strings(want)
	var got []string
	seen := map[string]bool{}
	for _, h := range presetHealthTable {
		if seen[h.Preset] {
			t.Errorf("preset %q is in presetHealthTable twice", h.Preset)
		}
		seen[h.Preset] = true
		got = append(got, h.Preset)
	}
	sort.Strings(got)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("presetHealthTable has %v; KnownProviders' openai-format presets are %v: every preset needs an entry", got, want)
	}

	for _, h := range presetHealthTable {
		t.Run(h.Preset, func(t *testing.T) {
			if !presetSourceRef.MatchString(h.Source) {
				t.Errorf("Source %q names no repo@<full commit>:path", h.Source)
			}
			if h.Gap != "" && !presetGapDef.MatchString(h.Gap) {
				t.Errorf("Gap %q is not a \"known gap <ID>: <reason>\" definition", h.Gap)
			}
			if h.Unverified != "" && !strings.Contains(h.Gap, "unverified") {
				t.Errorf("an unverified finding needs a Gap that says it is unverified")
			}
			if len(h.Calls) == 0 || h.Calls["chat"] == "" {
				t.Fatalf("Calls pins at least the chat URL")
			}
			endpoints := make([]string, 0, len(h.Calls))
			for e := range h.Calls {
				endpoints = append(endpoints, e)
			}
			sort.Strings(endpoints)
			for _, e := range endpoints {
				if got := presetCallURL(t, h.Preset, e); got != h.Calls[e] {
					t.Errorf("the %s preset now sends %s to %s, not %s: update Calls; if %s documents the API there, delete the gap and reclassify the preset's cells in contractcoverage/exemptions.json",
						h.Preset, e, got, h.Calls[e], h.Source)
				}
			}
		})
	}
}

// presetCallURL builds preset's provider as the gateway does (NewRegistry
// applies the preset), sends one endpoint request, and returns the URL it
// went to: every connection is dialled to a local TLS server that records
// the Host and path and answers 404.
func presetCallURL(t *testing.T, preset, endpoint string) string {
	t.Helper()
	var (
		mu   sync.Mutex
		seen string
	)
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = "https://" + r.Host + r.URL.Path
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"error":{"message":"not found","type":"invalid_request_error"}}`)
	}))
	defer srv.Close()
	addr := srv.Listener.Addr().String()
	var d net.Dialer
	reg, err := NewRegistry(&config.Config{Providers: map[string]config.ProviderConfig{
		preset: {
			Type:    preset,
			APIKey:  "test-key",
			Models:  []string{"m"},
			SkipTLS: true, // the local server's certificate is self-signed
			DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
				return d.DialContext(ctx, network, addr)
			},
		},
	}})
	if err != nil {
		t.Fatalf("NewRegistry: %v", err)
	}
	p, ok := reg.GetProvider(preset)
	if !ok {
		t.Fatalf("NewRegistry built no %s provider", preset)
	}
	defer func() { _ = p.Close() }()

	ctx := context.Background()
	audio := []byte("RIFF")
	switch endpoint {
	case "chat":
		_, _ = p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "m", Messages: []models.Message{{Role: "user", Content: []byte(`"p"`)}}})
	case "list_models":
		_, _ = p.ListModels(ctx)
	case "transcription":
		tp, ok := p.(TranscriptionProvider)
		if !ok {
			t.Fatalf("the %s provider serves no transcription", preset)
		}
		_, _ = tp.CreateTranscription(ctx, &models.TranscriptionRequest{Model: "m", FileName: "a.wav", FileData: audio})
	case "translation":
		tp, ok := p.(TranslationProvider)
		if !ok {
			t.Fatalf("the %s provider serves no translation", preset)
		}
		_, _ = tp.CreateTranslation(ctx, &models.TranslationRequest{Model: "m", FileName: "a.wav", FileData: audio})
	default:
		t.Fatalf("endpoint %q has no driver in presetCallURL", endpoint)
	}
	mu.Lock()
	defer mu.Unlock()
	if seen == "" {
		t.Fatalf("the %s provider sent no %s request upstream", preset, endpoint)
	}
	return seen
}
