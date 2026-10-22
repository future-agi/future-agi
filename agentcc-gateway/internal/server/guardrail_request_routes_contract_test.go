package server

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// api_contracts/gateway/guardrail-request-routes.json lists the routes whose
// request a check on the request reads, what it reads on each, the POST
// routes that send no text a model reads, and the APIs this gateway does not
// serve. The console's Guardrails page lists the routes from it
// (requestRoutes.test.jsx checks that), and the release notes say the same.
// These tests keep it true: every route the gateway registers that
// can send a request to a provider is in it, and every route it says a check
// reads is one a request-check test sends a request the check blocks to.

type requestRoutesContract struct {
	Read []struct {
		Route    string   `json:"route"`
		Patterns []string `json:"patterns"`
		Reads    string   `json:"reads"`
		Unread   string   `json:"unread"`
		Note     string   `json:"note"`
	} `json:"read"`
	NoModelText []struct {
		Pattern string `json:"pattern"`
		Why     string `json:"why"`
	} `json:"no_model_text"`
	NotServed []struct {
		Route string `json:"route"`
	} `json:"not_served"`
}

func readRequestRoutesContract(t *testing.T) requestRoutesContract {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "guardrail-request-routes.json"))
	if err != nil {
		t.Fatal(err)
	}
	var c requestRoutesContract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	return c
}

// requestRouteScope says whether a registered route can carry a client's
// request to a provider: a POST under /v1 (including /v1beta) or /a2a, or
// the batch API.
func requestRouteScope(method, pattern string) bool {
	if method != "POST" {
		return false
	}
	return pattern == "/-/batches" || pattern == "/a2a" ||
		strings.HasPrefix(pattern, "/v1/") || strings.HasPrefix(pattern, "/v1beta/")
}

// Every POST route the gateway registers under /v1, /v1beta and /a2a, and
// the batch API, is in the contract: a route a check reads, or one that
// sends no text a model reads. Every pattern the contract names is
// registered, and no API it says is not served is.
func TestRequestRoutesContract_EveryRouteIsClassified(t *testing.T) {
	c := readRequestRoutesContract(t)
	g := newOtherAPIsGateway(t, postBlockSetup{a2a: true})
	registered := map[string]bool{}
	for _, r := range g.srv.router.Routes() {
		registered[r.Method+" "+r.Pattern] = true
	}
	classified := map[string]bool{}
	for _, r := range c.Read {
		if len(r.Patterns) == 0 || r.Reads == "" {
			t.Errorf("%s: no pattern or no reads", r.Route)
		}
		for _, p := range r.Patterns {
			classified[p] = true
		}
	}
	for _, r := range c.NoModelText {
		if classified[r.Pattern] {
			t.Errorf("%s is both read and no_model_text", r.Pattern)
		}
		if r.Why == "" {
			t.Errorf("%s: no_model_text says no why", r.Pattern)
		}
		classified[r.Pattern] = true
	}
	for route := range registered {
		method, pattern, _ := strings.Cut(route, " ")
		if requestRouteScope(method, pattern) && !classified[route] {
			t.Errorf("%s is registered and the contract does not say whether a check reads it", route)
		}
	}
	for route := range classified {
		if !registered[route] {
			t.Errorf("the contract names %s, which the gateway does not register", route)
		}
	}
	for _, r := range c.NotServed {
		if registered[r.Route] {
			t.Errorf("%s is registered, but the contract says it is not served", r.Route)
		}
	}
}

// requestCheckProbes are the requests the request-check tests send a
// request a check blocks to, as "METHOD path": the chat-shaped surfaces and
// count routes, the non-chat routes, and the routes other tests probe
// (named below).
func requestCheckProbes() []string {
	var probes []string
	for _, sf := range requestSurfaces {
		probes = append(probes, "POST "+sf.path)
	}
	for _, sf := range countTokensSurfaces {
		probes = append(probes, "POST "+sf.path)
	}
	for _, sf := range otherAPISurfaces {
		probes = append(probes, "POST "+sf.path)
	}
	for _, tc := range otherAPIUnreadable {
		probes = append(probes, "POST "+tc.path)
	}
	for _, tc := range genaiProviderQualified {
		probes = append(probes, "POST /v1beta/models/gemini/"+postBlockGemini+":"+tc.action)
	}
	return append(probes,
		"POST /v1/completions", // TestRequestCheck_CompletionsSendsTheRewrite, ...CompletionsTokenIDsAreRefusedUnread
		"POST /v1/a2a",         // TestRequestCheck_AsyncAndA2AAreChecked
		"POST /a2a",            // TestRequestCheck_AsyncAndA2AAreChecked
		"POST /-/batches",      // TestRequestCheck_BatchItemsAreChecked
	)
}

// patternRegexp matches a concrete "METHOD path" against a route or pattern
// of the contract: a {param} is one path segment, and a route's ":action"
// must be the path's.
func patternRegexp(route string) *regexp.Regexp {
	quoted := regexp.QuoteMeta(route)
	quoted = regexp.MustCompile(`\\\{[a-z_]+\\\}`).ReplaceAllString(quoted, `[^/]+`)
	return regexp.MustCompile("^" + quoted + "$")
}

// Every route the contract says a check reads, under each of its patterns,
// is one a request-check test sends a request to.
func TestRequestRoutesContract_EveryReadRouteIsProbed(t *testing.T) {
	c := readRequestRoutesContract(t)
	probes := requestCheckProbes()
	probed := func(re *regexp.Regexp, action string) bool {
		for _, p := range probes {
			if re.MatchString(p) && (action == "" || strings.HasSuffix(p, ":"+action)) {
				return true
			}
		}
		return false
	}
	for _, r := range c.Read {
		action := ""
		if i := strings.LastIndex(r.Route, ":"); i > 0 {
			action = r.Route[i+1:]
		}
		if !probed(patternRegexp(r.Route), "") {
			t.Errorf("%s: no request-check test sends a request to it", r.Route)
		}
		for _, p := range r.Patterns {
			if !probed(patternRegexp(p), action) {
				t.Errorf("%s: no request-check test sends a request to its pattern %s", r.Route, p)
			}
		}
	}
}
