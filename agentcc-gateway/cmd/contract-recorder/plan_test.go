package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// fixtureTree builds a throwaway providers directory.
type fixtureTree struct {
	t    *testing.T
	root string
}

func newFixtureTree(t *testing.T) *fixtureTree {
	return &fixtureTree{t: t, root: t.TempDir()}
}

func (f *fixtureTree) write(rel, content string) {
	f.t.Helper()
	p := filepath.Join(f.root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		f.t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fixtureTree) read(rel string) string {
	f.t.Helper()
	b, err := os.ReadFile(filepath.Join(f.root, filepath.FromSlash(rel)))
	if err != nil {
		f.t.Fatal(err)
	}
	return string(b)
}

// model writes a model directory: meta.json plus one case per name, each
// with a request.json (stream when the name starts with "stream") and a
// synthetic response file.
func (f *fixtureTree) model(provider, model string, cases ...string) {
	f.t.Helper()
	dir := provider + "/testdata/contracts/" + contracttest.ModelDir(model)
	f.write(dir+"/meta.json", metaJSON(provider, model))
	for _, c := range cases {
		stream := strings.HasPrefix(c, "stream")
		f.write(dir+"/"+c+"/request.json", requestJSON(model, stream))
		f.write(dir+"/"+c+"/"+responseFile(provider, stream), "synthetic")
	}
}

func metaJSON(provider, model string) string {
	m := &contracttest.Meta{
		Provider: provider, Model: model, Source: contracttest.SourceSynthetic,
		Notes: "Synthetic: authored from docs <a&b>.",
		AllowedUnknownFields: []contracttest.AllowedField{
			{Path: "$.usage.service_tier", Reason: `known gap: "tier" dropped`},
		},
	}
	b, err := encodeMeta(m)
	if err != nil {
		panic(err)
	}
	return string(b)
}

func requestJSON(model string, stream bool) string {
	s := `{
  "model": "` + model + `",
  "messages": [
    {
      "role": "user",
      "content": "Say hi <b>."
    }
  ],
  "max_tokens": 64`
	if stream {
		s += `,
  "stream": true`
	}
	return s + "\n}\n"
}

func TestPlanInPlace(t *testing.T) {
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic", "stream_text", "tool_use")
	f.model("anthropic", "claude-b", "text_basic")

	pl, err := planProvider(f.root, "anthropic", selection{Models: []string{"claude-a"}, Cases: []string{"text_*", "stream_*"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(pl.Jobs) != 2 {
		t.Fatalf("jobs = %d, want 2 (claude-a text_basic, stream_text)", len(pl.Jobs))
	}
	j := pl.Jobs[0]
	wantDir := filepath.Join(f.root, "anthropic", "testdata", "contracts", "claude-a", "stream_text")
	if j.Name != "stream_text" || j.OutDir != wantDir || !j.Stream || j.ResponseFile() != "response.sse" || j.WriteRequest {
		t.Errorf("job 0 = %+v, want stream_text in place at %s", j, wantDir)
	}
	if string(j.Body) != requestJSON("claude-a", true) {
		t.Errorf("in-place body must be request.json unchanged, got %s", j.Body)
	}
	if len(pl.Metas) != 1 || pl.Metas[0].Cases != 3 || len(pl.Metas[0].Jobs) != 2 || pl.Metas[0].ClonedFrom != "" {
		t.Errorf("metas = %+v, want one in-place update of claude-a covering 2 of 3 cases", pl.Metas)
	}
}

func TestPlanOutLayout(t *testing.T) {
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	out := t.TempDir()

	pl, err := planProvider(f.root, "anthropic", selection{Out: out})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(out, "anthropic", "claude-a", "text_basic"); pl.Jobs[0].OutDir != want {
		t.Errorf("OutDir = %s, want %s", pl.Jobs[0].OutDir, want)
	}
	if len(pl.Metas) != 0 {
		t.Errorf("an --out recording must not plan meta writes (live mode reads the repo's): %+v", pl.Metas)
	}
}

func TestPlanEncodedModelDir(t *testing.T) {
	const nova = "us.amazon.nova-2-lite-v1:0"
	f := newFixtureTree(t)
	f.model("bedrock", nova, "text_basic", "stream_text")
	out := t.TempDir()

	pl, err := planProvider(f.root, "bedrock", selection{Models: []string{nova}, Out: out})
	if err != nil {
		t.Fatal(err)
	}
	if len(pl.Jobs) != 2 {
		t.Fatalf("jobs = %d, want 2: -model takes the real ID", len(pl.Jobs))
	}
	j := pl.Jobs[1]
	if j.Model != nova || j.Target != nova {
		t.Errorf("model = %q / %q, want the decoded ID %q", j.Model, j.Target, nova)
	}
	if want := filepath.Join(out, "bedrock", "us.amazon.nova-2-lite-v1%3A0", "text_basic"); j.OutDir != want {
		t.Errorf("OutDir = %s, want %s", j.OutDir, want)
	}
	if got := pl.Jobs[0].ResponseFile(); got != contracttest.ResponseEventStreamFile {
		t.Errorf("bedrock stream response file = %s, want %s", got, contracttest.ResponseEventStreamFile)
	}

	// In place too.
	pl, err = planProvider(f.root, "bedrock", selection{Cases: []string{"text_basic"}})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(f.root, "bedrock", "testdata", "contracts", "us.amazon.nova-2-lite-v1%3A0", "text_basic"); pl.Jobs[0].OutDir != want {
		t.Errorf("in-place OutDir = %s, want %s", pl.Jobs[0].OutDir, want)
	}
}

func TestPlanRejectsNonCanonicalModelDir(t *testing.T) {
	for _, dir := range []string{"us.amazon.nova-2-lite-v1:0", "us.amazon.nova-2-lite-v1%3a0", "bad%zz"} {
		t.Run(dir, func(t *testing.T) {
			f := newFixtureTree(t)
			f.write("bedrock/testdata/contracts/"+dir+"/text_basic/request.json", requestJSON("x", false))
			if _, err := planProvider(f.root, "bedrock", selection{}); err == nil {
				t.Errorf("model directory %q accepted; want an error naming the ModelDir spelling", dir)
			}
		})
	}
}

func TestPlanClone(t *testing.T) {
	const newModel = "claude-next:1@2026"
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic", "stream_text")
	f.write("anthropic/testdata/contracts/claude-a/text_basic/upstream_patch.json", `{"thinking":{"type":"enabled","budget_tokens":1024}}`)

	pl, err := planProvider(f.root, "anthropic", selection{From: "claude-a", As: newModel, Cases: []string{"text_basic"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(pl.Jobs) != 1 {
		t.Fatalf("jobs = %d, want 1", len(pl.Jobs))
	}
	j := pl.Jobs[0]
	newDir := filepath.Join(f.root, "anthropic", "testdata", "contracts", "claude-next%3A1%402026")
	if j.Target != newModel || j.OutDir != filepath.Join(newDir, "text_basic") || !j.WriteRequest || j.Patch == nil {
		t.Errorf("job = %+v, want a clone into %s carrying the patch", j, newDir)
	}
	if want := strings.Replace(requestJSON("claude-a", false), `"claude-a"`, `"`+newModel+`"`, 1); string(j.Body) != want {
		t.Errorf("cloned request:\n%s\nwant (only the model changed, key order kept):\n%s", j.Body, want)
	}
	mw := pl.Metas[0]
	if mw.Path != filepath.Join(newDir, "meta.json") || mw.ClonedFrom != "claude-a" || mw.Model != newModel || mw.Cases != 1 {
		t.Errorf("meta = %+v", mw)
	}

	out := t.TempDir()
	pl, err = planProvider(f.root, "anthropic", selection{From: "claude-a", As: newModel, Out: out})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(out, "anthropic", "claude-next%3A1%402026", "stream_text"); pl.Jobs[0].OutDir != want {
		t.Errorf("--out clone OutDir = %s, want %s", pl.Jobs[0].OutDir, want)
	}

	if _, err := planProvider(f.root, "anthropic", selection{From: "claude-zzz", As: "x"}); err == nil {
		t.Error("cloning an unknown -from model: want an error")
	}
	f.model("anthropic", newModel, "text_basic")
	if _, err := planProvider(f.root, "anthropic", selection{From: "claude-a", As: newModel}); err == nil {
		t.Error("cloning in place onto an existing model: want an error")
	}
}

func TestSetModel(t *testing.T) {
	in := "{\n  \"stream\": true,\n  \"model\": \"old\",\n  \"messages\": [{\"role\": \"user\", \"content\": \"a<b\"}],\n  \"x\": {\"n\": 1.50}\n}\n"
	got, err := setModel([]byte(in), "new<model>")
	if err != nil {
		t.Fatal(err)
	}
	want := `{
  "stream": true,
  "model": "new<model>",
  "messages": [
    {
      "role": "user",
      "content": "a<b"
    }
  ],
  "x": {
    "n": 1.50
  }
}
`
	if string(got) != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
	if _, err := setModel([]byte(`{"messages":[]}`), "m"); err == nil {
		t.Error("request without model: want an error")
	}
	if _, err := setModel([]byte(`[1]`), "m"); err == nil {
		t.Error("non-object request: want an error")
	}
}

func TestMarkRecorded(t *testing.T) {
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	src := filepath.Join(f.root, "anthropic", "testdata", "contracts", "claude-a", "meta.json")
	before := f.read("anthropic/testdata/contracts/claude-a/meta.json")

	got, err := markRecorded(metaWrite{Src: src, Model: "claude-a"}, "2026-10-28")
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Replace(before, `"source": "synthetic",
  "recorded_at": null,`, `"source": "recorded",
  "recorded_at": "2026-10-28",`, 1)
	if want == before {
		t.Fatal("test fixture does not have the expected layout")
	}
	if string(got) != want {
		t.Errorf("in-place update must change only source and recorded_at:\n%s\nwant:\n%s", got, want)
	}

	got, err = markRecorded(metaWrite{Src: src, Model: "claude-b", ClonedFrom: "claude-a"}, "2026-10-28")
	if err != nil {
		t.Fatal(err)
	}
	var m contracttest.Meta
	if err := json.Unmarshal(got, &m); err != nil {
		t.Fatal(err)
	}
	if m.Model != "claude-b" || m.Source != "recorded" || *m.RecordedAt != "2026-10-28" ||
		len(m.AllowedUnknownFields) != 1 || !strings.Contains(m.Notes, "--from claude-a --as claude-b") {
		t.Errorf("cloned meta = %s", got)
	}
}

func TestMatchAnyAndSplitList(t *testing.T) {
	pats := splitList(" text_*, ,tool_use ")
	if len(pats) != 2 {
		t.Fatalf("splitList = %q", pats)
	}
	for s, want := range map[string]bool{"text_basic": true, "tool_use": true, "stream_text": false} {
		if got, _ := matchAny(pats, s); got != want {
			t.Errorf("matchAny(%q) = %v, want %v", s, got, want)
		}
	}
	if ok, _ := matchAny(nil, "anything"); !ok {
		t.Error("no patterns must match everything")
	}
	if _, err := matchAny([]string{"["}, "x"); err == nil {
		t.Error("bad glob: want an error")
	}
}

func TestWriteRecordingReplacesFixture(t *testing.T) {
	f := newFixtureTree(t)
	f.model("bedrock", "us.amazon.nova-2-lite-v1:0", "stream_text")
	dir := "bedrock/testdata/contracts/us.amazon.nova-2-lite-v1%3A0/stream_text/"
	f.write(dir+"response.eventstream.json", `[{"event":"messageStart","payload":{"role":"assistant"}}]`)
	f.write(dir+"response.json", "stray")

	pl, err := planProvider(f.root, "bedrock", selection{})
	if err != nil {
		t.Fatal(err)
	}
	dst, removed, err := writeRecording(pl.Jobs[0], []byte("recorded"))
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(dst) != "response.eventstream" || f.read(dir+"response.eventstream") != "recorded" {
		t.Errorf("wrote %s", dst)
	}
	if strings.Join(removed, ",") != "response.json,response.eventstream.json" {
		t.Errorf("removed %q; want the stray response file and the stale generator source", removed)
	}
	entries, _ := os.ReadDir(filepath.Join(f.root, filepath.FromSlash(dir)))
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	if strings.Join(names, ",") != "request.json,response.eventstream" {
		t.Errorf("case dir holds %q", names)
	}
}
