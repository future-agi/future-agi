package main

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// fakeFetcher serves canned repositories; nothing here touches the network.
type fakeFetcher struct {
	heads map[string]string // repo -> commit
	files map[string]string // URL -> body
	asked []string          // "repo ref" of every LsRemote call
}

func (f *fakeFetcher) LsRemote(_ context.Context, repo, ref string) (string, error) {
	f.asked = append(f.asked, repo+" "+ref)
	if shaRE.MatchString(ref) {
		return ref, nil
	}
	sha, ok := f.heads[repo]
	if !ok {
		return "", fmt.Errorf("git ls-remote %s: repository not found", repo)
	}
	return sha, nil
}

func (f *fakeFetcher) Get(_ context.Context, url string) ([]byte, error) {
	body, ok := f.files[url]
	if !ok {
		return nil, fmt.Errorf("GET %s: HTTP 404", url)
	}
	return []byte(body), nil
}

const (
	sha1 = "1111111111111111111111111111111111111111"
	sha2 = "2222222222222222222222222222222222222222"
	sha3 = "3333333333333333333333333333333333333333"
)

func acmeSpec(extra string) string {
	return `{"openapi": "3.1.0", "info": {"version": "1"}, "paths": {"/chat": {"post": {"responses": {"200": {"content": {"application/json": {"schema": {
  "type": "object", "properties": {
    "id": {"type": "string"},
    "stop_reason": {"type": "string", "enum": ["end", "max"` + extra + `]}
  }}}}}}}}}}`
}

func testOptions(dir string) options {
	return options{
		dir: dir, timeout: time.Minute,
		sources: []contractspec.Source{{
			Provider: "acme", Format: contractspec.FormatOpenAPI, Repo: "acme/spec", Path: "openapi.json",
			Roots: []contractspec.RootDef{{Name: "chat", Op: "POST /chat"}},
		}},
		unfetchable: []contractspec.Unfetchable{{
			Provider: "beta", Reason: "no spec",
			Signal: &contractspec.Signal{Repo: "beta/sdk", Path: ".stats.yml", Key: "openapi_spec_hash", What: "hash"},
		}},
	}
}

func TestRunUpdateThenCheck(t *testing.T) {
	dir := t.TempDir()
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	f := &fakeFetcher{
		heads: map[string]string{"acme/spec": sha1, "beta/sdk": sha2},
		files: map[string]string{
			"https://raw.githubusercontent.com/acme/spec/" + sha1 + "/openapi.json": acmeSpec(""),
			"https://raw.githubusercontent.com/beta/sdk/" + sha2 + "/.stats.yml":    "configured_endpoints: 4\nopenapi_spec_hash: aaa\n",
		},
	}
	var out bytes.Buffer
	o := testOptions(dir)
	o.fetch, o.update = true, true
	if code := run(context.Background(), o, f, &out, &out, now); code != exitOK {
		t.Fatalf("update exit %d:\n%s", code, out.String())
	}
	prov, err := contractspec.LoadProvenance(os.DirFS(dir), "acme")
	if err != nil {
		t.Fatal(err)
	}
	if prov.Commit != sha1 || !strings.Contains(prov.URL, sha1) || prov.SHA256 != contractspec.SHA256Hex([]byte(acmeSpec(""))) || prov.FetchedAt != "2026-10-28T12:00:00Z" {
		t.Errorf("provenance %+v", prov)
	}
	m, err := contractspec.LoadManifest(os.DirFS(dir))
	if err != nil || m.Entry("beta") == nil || m.Entry("beta").Observed.Value != "aaa" {
		t.Fatalf("manifest %+v, %v", m, err)
	}

	// Unchanged upstream: no drift.
	out.Reset()
	o = testOptions(dir)
	o.fetch, o.check = true, true
	if code := run(context.Background(), o, f, &out, &out, now); code != exitOK {
		t.Fatalf("check of an unchanged spec exit %d:\n%s", code, out.String())
	}

	// The spec moves on: a new stop reason, and the signal changes.
	f.heads["acme/spec"], f.heads["beta/sdk"] = sha3, sha3
	f.files["https://raw.githubusercontent.com/acme/spec/"+sha3+"/openapi.json"] = acmeSpec(`, "refusal"`)
	f.files["https://raw.githubusercontent.com/beta/sdk/"+sha3+"/.stats.yml"] = "openapi_spec_hash: bbb\n"
	out.Reset()
	reports := filepath.Join(t.TempDir(), "reports")
	o.report = reports
	code := run(context.Background(), o, f, &out, &out, now)
	if code != exitDrift {
		t.Fatalf("check of a drifted spec exit %d, want %d:\n%s", code, exitDrift, out.String())
	}
	for _, want := range []string{
		"committed: acme/spec@" + sha1[:12],
		"latest:    acme/spec@" + sha3[:12],
		"FAIL  new enum value       $.stop_reason  + refusal",
		"FAIL  change signal moved: beta/sdk .stats.yml openapi_spec_hash: aaa -> bbb",
		"acme        drift",
		"beta        drift",
	} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("output lacks %q:\n%s", want, out.String())
		}
	}
	body, err := os.ReadFile(filepath.Join(reports, "acme.md"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"+ refusal", "go run ./cmd/contract-specs -fetch -update -provider acme", "allowlist it with a reason"} {
		if !strings.Contains(string(body), want) {
			t.Errorf("issue body lacks %q:\n%s", want, body)
		}
	}
	if _, err := os.Stat(filepath.Join(reports, "beta.md")); err != nil {
		t.Errorf("no issue body for the moved signal: %v", err)
	}
	summary, _ := os.ReadFile(filepath.Join(reports, "summary.md"))
	if !strings.Contains(string(summary), "| acme | drift |") {
		t.Errorf("summary:\n%s", summary)
	}
}

func TestRunPinnedRefAndErrors(t *testing.T) {
	dir := t.TempDir()
	f := &fakeFetcher{heads: map[string]string{}, files: map[string]string{
		"https://raw.githubusercontent.com/acme/spec/" + sha2 + "/openapi.json": acmeSpec(""),
	}}
	var out bytes.Buffer
	o := testOptions(dir)
	o.providers, o.fetch, o.update, o.ref = []string{"acme"}, true, true, sha2
	if code := run(context.Background(), o, f, &out, &out, time.Now()); code != exitOK {
		t.Fatalf("pinned update exit %d:\n%s", code, out.String())
	}
	if prov, _ := contractspec.LoadProvenance(os.DirFS(dir), "acme"); prov == nil || prov.Commit != sha2 {
		t.Errorf("provenance not pinned at -ref: %+v", prov)
	}

	// An upstream that cannot be fetched is an error (exit 2), not drift.
	out.Reset()
	o = testOptions(dir)
	o.providers, o.fetch, o.check = []string{"acme"}, true, true
	if code := run(context.Background(), o, f, &out, &out, time.Now()); code != exitError {
		t.Errorf("unreachable upstream exit %d, want %d:\n%s", code, exitError, out.String())
	}

	for _, bad := range []options{
		{},                           // nothing to read
		{fetch: true, specFile: "x"}, // exclusive
		{fetch: true, ref: sha1},     // -ref with several providers
		{fetch: true, report: "r"},   // -report without -check
		{fetch: true, providers: []string{"nope"}},
	} {
		base := testOptions(dir)
		bad.sources, bad.unfetchable, bad.dir = base.sources, base.unfetchable, dir
		if code := run(context.Background(), bad, f, &out, &out, time.Now()); code != exitError {
			t.Errorf("options %+v: exit %d, want %d", bad, code, exitError)
		}
	}
}

// A root whose operation disappears from the spec is drift, and -update
// keeps the rest current without deleting its committed subset.
func TestRunUnextractableRoot(t *testing.T) {
	dir := t.TempDir()
	f := &fakeFetcher{heads: map[string]string{"acme/spec": sha1}, files: map[string]string{
		"https://raw.githubusercontent.com/acme/spec/" + sha1 + "/openapi.json": acmeSpec(""),
	}}
	o := testOptions(dir)
	o.providers, o.fetch, o.update = []string{"acme"}, true, true
	o.sources[0].Roots = append(o.sources[0].Roots, contractspec.RootDef{Name: "embed", Op: "POST /embed"})
	var out bytes.Buffer
	if code := run(context.Background(), o, f, &out, &out, time.Now()); code != exitError {
		t.Fatalf("update with an unextractable root: exit %d, want %d:\n%s", code, exitError, out.String())
	}
	if _, err := os.Stat(filepath.Join(dir, "acme", "chat.json")); err != nil {
		t.Errorf("the extractable root was not written: %v", err)
	}
	out.Reset()
	o.update, o.check = false, true
	if code := run(context.Background(), o, f, &out, &out, time.Now()); code != exitDrift {
		t.Fatalf("check: exit %d, want %d:\n%s", code, exitDrift, out.String())
	}
	if !strings.Contains(out.String(), "FAIL  root not extractable") || !strings.Contains(out.String(), `op "POST /embed" not in spec`) {
		t.Errorf("output:\n%s", out.String())
	}
}

// xaiSpec is a minimal stand-in for xai-org/xai-sdk-ts spec/openapi.json:
// every operation and schema the xai definition roots at, chat's chunk
// schema attached to no operation as upstream has it.
func xaiSpec(extra string) string {
	obj := `{"type": "object", "properties": {"id": {"type": "string"}}}`
	op := func(method string) string {
		return `{"` + method + `": {"responses": {"200": {"content": {"application/json": {"schema": ` + obj + `}}}, "400": {"description": "Bad request."}}}}`
	}
	return `{"openapi": "3.1.0", "info": {"version": "1.0.0"}, "paths": {
  "/v1/chat/completions": {"post": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChatResponse"}}}}, "400": {"description": "Bad request."}}}},
  "/v1/completions": ` + op("post") + `,
  "/v1/embeddings": ` + op("post") + `,
  "/v1/images/generations": ` + op("post") + `,
  "/v1/responses": ` + op("post") + `,
  "/v1/models": ` + op("get") + `},
 "components": {"schemas": {
  "ChatResponse": {"type": "object", "properties": {"id": {"type": "string"}, "choices": {"type": "array", "items": {"type": "object", "properties": {
    "finish_reason": {"type": ["string", "null"], "enum": ["stop", "length"` + extra + `]}}}}}},
  "ChatResponseChunk": ` + obj + `}}}`
}

// The xai definition's fetch path: the latest commit of xai-org/xai-sdk-ts
// by git ls-remote, then spec/openapi.json raw at that commit, every root
// extracted and the provenance pinned to the immutable URL.
func TestRunXAIFetchPath(t *testing.T) {
	src, ok := contractspec.SourceFor("xai")
	if !ok {
		t.Fatal("no xai definition")
	}
	rawAt := func(sha string) string {
		return "https://raw.githubusercontent.com/xai-org/xai-sdk-ts/" + sha + "/spec/openapi.json"
	}
	dir := t.TempDir()
	f := &fakeFetcher{
		heads: map[string]string{"xai-org/xai-sdk-ts": sha1},
		files: map[string]string{rawAt(sha1): xaiSpec("")},
	}
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	o := options{dir: dir, timeout: time.Minute, sources: []contractspec.Source{src}, fetch: true, update: true}
	var out bytes.Buffer
	if code := run(context.Background(), o, f, &out, &out, now); code != exitOK {
		t.Fatalf("update exit %d:\n%s", code, out.String())
	}
	if len(f.asked) != 1 || f.asked[0] != "xai-org/xai-sdk-ts HEAD" {
		t.Errorf("ls-remote calls %q, want the latest commit of xai-org/xai-sdk-ts", f.asked)
	}
	prov, err := contractspec.LoadProvenance(os.DirFS(dir), "xai")
	if err != nil {
		t.Fatal(err)
	}
	var defined []string
	for _, r := range src.Roots {
		defined = append(defined, r.Name)
	}
	sort.Strings(defined)
	if prov.URL != rawAt(sha1) || prov.Commit != sha1 || prov.Repo != "xai-org/xai-sdk-ts" || prov.Path != "spec/openapi.json" ||
		prov.SHA256 != contractspec.SHA256Hex([]byte(xaiSpec(""))) || prov.License == "" || !reflect.DeepEqual(prov.Roots, defined) {
		t.Errorf("provenance %+v, want %s pinned with roots %v", prov, rawAt(sha1), defined)
	}

	// Upstream moves on: -check re-resolves the latest commit and fetches
	// the spec raw at it; a new finish reason is drift.
	f.heads["xai-org/xai-sdk-ts"] = sha3
	f.files[rawAt(sha3)] = xaiSpec(`, "end_turn"`)
	out.Reset()
	o.update, o.check = false, true
	if code := run(context.Background(), o, f, &out, &out, now); code != exitDrift {
		t.Fatalf("check of a drifted spec exit %d, want %d:\n%s", code, exitDrift, out.String())
	}
	for _, want := range []string{
		"committed: xai-org/xai-sdk-ts@" + sha1[:12],
		"latest:    xai-org/xai-sdk-ts@" + sha3[:12],
		"xai/chat:",
		"+ end_turn",
	} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("output lacks %q:\n%s", want, out.String())
		}
	}
}

func TestRunLocalSpec(t *testing.T) {
	dir := t.TempDir()
	spec := filepath.Join(t.TempDir(), "openapi.json")
	if err := os.WriteFile(spec, []byte(acmeSpec("")), 0o644); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	o := testOptions(dir)
	o.providers, o.specFile = []string{"acme"}, spec
	if code := run(context.Background(), o, &fakeFetcher{}, &out, &out, time.Now()); code != exitOK {
		t.Fatalf("exit %d:\n%s", code, out.String())
	}
	if !strings.Contains(out.String(), "acme/chat") || !strings.Contains(out.String(), "3 paths") {
		t.Errorf("output:\n%s", out.String())
	}
}

func TestParseLsRemote(t *testing.T) {
	sha, err := parseLsRemote([]byte(sha1+"\tHEAD\n"), "HEAD")
	if err != nil || sha != sha1 {
		t.Errorf("HEAD: %q, %v", sha, err)
	}
	sha, err = parseLsRemote([]byte(sha1+"\trefs/tags/v1\n"+sha2+"\trefs/tags/v1^{}\n"), "v1")
	if err != nil || sha != sha2 {
		t.Errorf("annotated tag: %q, %v (want the peeled commit)", sha, err)
	}
	if _, err := parseLsRemote(nil, "main"); err == nil {
		t.Error("an empty listing was accepted")
	}
	if validRepo("a/b/../c") || validRepo("-x") || !validRepo("openai/openai-openapi") {
		t.Error("validRepo")
	}
}

func TestLookupKey(t *testing.T) {
	doc := []byte("sources:\n  mistral-openapi:\n    sourceBlobDigest: sha256:abc\n")
	if v, err := lookupKey(doc, "sources.mistral-openapi.sourceBlobDigest"); err != nil || v != "sha256:abc" {
		t.Errorf("%q, %v", v, err)
	}
	if _, err := lookupKey(doc, "sources.nope"); err == nil {
		t.Error("a missing key was accepted")
	}
}
