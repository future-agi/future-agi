package contractwatch

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func envOf(vars map[string]string) func(string) string {
	return func(k string) string { return vars[k] }
}

// TestCheckSkipsWithoutCredentials runs Check on the committed registry with
// an empty environment: every listed provider is skipped with the variable
// it needs, nothing reaches the network, and the run is clean.
func TestCheckSkipsWithoutCredentials(t *testing.T) {
	reg, _ := loadCommitted(t)
	rc := &refusingClient{}
	rep := Check(context.Background(), reg, CheckOptions{Env: envOf(nil), Client: rc.client(), Plan: PlanOptions{ProvidersDir: ".."}})
	if len(rc.hosts) > 0 {
		t.Fatalf("contacted %v without credentials", rc.hosts)
	}
	for _, s := range rep.Sections {
		switch reg.Providers[s.Name].List {
		case ListNone:
			if s.State != StateNone {
				t.Errorf("%s: %s, want unlisted", s.Name, s.State)
			}
		default:
			if s.State != StateSkipped || !strings.Contains(s.Detail, "not set") {
				t.Errorf("%s: %s %q, want skipped for a missing variable", s.Name, s.State, s.Detail)
			}
		}
	}
	if rep.Failed() || len(rep.Plan) != 0 {
		t.Errorf("a run with nothing listed must be clean: %+v", rep)
	}
}

// TestCheckUsesRealHosts builds the real listers (no overrides) with
// credentials set and a client that refuses everything: each provider is
// attempted at its real host, and each failure is reported, not fatal.
func TestCheckUsesRealHosts(t *testing.T) {
	reg, _ := loadCommitted(t)
	rc := &refusingClient{}
	env := envOf(map[string]string{
		"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k", "GEMINI_API_KEY": "k", "COHERE_API_KEY": "k",
		"GROQ_API_KEY": "k", "AWS_ACCESS_KEY_ID": "AKID", "AWS_SECRET_ACCESS_KEY": "secret", "AWS_REGION": "eu-west-1",
		"MISTRAL_API_KEY": "k", "CEREBRAS_API_KEY": "k", "TOGETHER_API_KEY": "k", "FIREWORKS_API_KEY": "k", "OPENROUTER_API_KEY": "k",
		"XAI_API_KEY": "k", "REPLICATE_API_KEY": "k",
	})
	rep := Check(context.Background(), reg, CheckOptions{Env: env, Client: rc.client(), Plan: PlanOptions{ProvidersDir: ".."}})
	// Sections in name order; the aggregators (list "none") are not listed.
	want := []string{"api.anthropic.com", "bedrock.eu-west-1.amazonaws.com", "api.cerebras.ai", "api.cohere.com", "generativelanguage.googleapis.com",
		"api.groq.com", "api.mistral.ai", "api.openai.com", "api.x.ai"}
	if strings.Join(rc.hosts, ",") != strings.Join(want, ",") {
		t.Errorf("hosts = %v, want %v", rc.hosts, want)
	}
	for _, s := range rep.Sections {
		if l := reg.Providers[s.Name].List; l != ListAPI {
			continue
		}
		if s.State != StateError {
			t.Errorf("%s: %s, want error", s.Name, s.State)
		}
	}
	if !rep.Failed() {
		t.Error("a failed listing must fail the run")
	}
}

func TestCheckWithListers(t *testing.T) {
	reg := mustParse(t, planRegistry)
	root := planTree(t)
	env := envOf(map[string]string{"GEMINI_API_KEY": "k", "GROQ_API_KEY": "k", "AWS_REGION": "nowhere"})
	listers := map[string]Lister{
		"gemini": StaticLister{Models: []string{"gemini-3.8-flash", "gemini-4-argon", "gemini-4.1-argon"}},
		"groq": ListerFunc(func(context.Context) ([]Model, error) {
			return nil, errors.New("HTTP 503: upstream down")
		}),
	}
	rep := Check(context.Background(), reg, CheckOptions{Env: env, Listers: listers, Sections: []string{"gemini", "groq", "anthropic"},
		Timeout: time.Second, Plan: PlanOptions{ProvidersDir: root}})
	states := map[string]SectionState{}
	for _, s := range rep.Sections {
		states[s.Name] = s.State
	}
	if states["gemini"] != StateListed || states["groq"] != StateError || states["anthropic"] != StateSkipped || len(states) != 3 {
		t.Errorf("states = %v (cohere is not selected)", states)
	}
	if len(rep.Plan) != 1 || rep.Plan[0].NewModel != "gemini-4.1-argon" || rep.Plan[0].TemplateModel != "gemini-4-argon" {
		t.Errorf("plan = %+v", rep.Plan)
	}
	if !rep.Failed() {
		t.Error("NEW models must fail the run")
	}
	var text strings.Builder
	rep.WriteText(&text)
	for _, want := range []string{"gemini: listed 3 models: 1 new", "groq: FAILED to list: HTTP 503", "anthropic: skipped: ANTHROPIC_API_KEY not set", "NEW gemini gemini-4.1-argon: template gemini-4-argon"} {
		if !strings.Contains(text.String(), want) {
			t.Errorf("text output lacks %q:\n%s", want, text.String())
		}
	}
}

func TestCheckStaticList(t *testing.T) {
	reg := mustParse(t, `{
  "schema_version": 1, "about": "t",
  "providers": {
    "perplexity": {"list": "static", "note": "no list API", "fixtures": {"provider": "openai", "prefix": "perplexity@"},
      "static": {"checked": "2026-01-02", "source": "https://docs.example/models", "models": ["sonar", "sonar-pro"]},
      "models": [{"id": "sonar", "status": "covered"}]}
  }
}`)
	root := fakeTree(t, map[string][]string{"openai/perplexity%40sonar": {"text_basic"}})
	now := func() time.Time { return time.Date(2026, 10, 28, 0, 0, 0, 0, time.UTC) }
	rep := Check(context.Background(), reg, CheckOptions{Env: envOf(map[string]string{"PERPLEXITY_API_KEY": "k"}), Now: now, Plan: PlanOptions{ProvidersDir: root}})
	if len(rep.Plan) != 1 || rep.Plan[0].NewModel != "sonar-pro" || rep.Plan[0].Recorder.As != "perplexity@sonar-pro" {
		t.Fatalf("plan = %+v", rep.Plan)
	}
	if len(rep.Notes) != 1 || !strings.Contains(rep.Notes[0], "last checked on 2026-01-02") {
		t.Errorf("an old static list needs a reminder: %v", rep.Notes)
	}
	if len(rep.Stale) != 0 {
		t.Errorf("stale: %v", rep.Stale)
	}
}

func TestCheckRejectsOddRegion(t *testing.T) {
	reg, _ := loadCommitted(t)
	rc := &refusingClient{}
	env := envOf(map[string]string{"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b", "AWS_REGION": "evil.example.com/x"})
	rep := Check(context.Background(), reg, CheckOptions{Env: env, Client: rc.client(), Sections: []string{"bedrock"}, Plan: PlanOptions{ProvidersDir: ".."}})
	if len(rc.hosts) != 0 || rep.Sections[0].State != StateError {
		t.Errorf("a malformed region must not be dialled: hosts %v, %+v", rc.hosts, rep.Sections[0])
	}
}
