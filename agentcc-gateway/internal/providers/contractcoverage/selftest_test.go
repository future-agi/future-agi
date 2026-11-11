package contractcoverage

import (
	"strings"
	"testing"
)

// TestBuildGridFindsProblems checks the grid's own failure paths: an
// uncovered cell, a case for an endpoint the row lacks, an unknown row, and
// stale or malformed exemptions.
func TestBuildGridFindsProblems(t *testing.T) {
	rows := []*row{
		{Key: "a", Impl: map[string]bool{"chat": true, "embeddings": true, "images": true, "list_models": true}},
		{Key: "b@", Impl: map[string]bool{"chat": true}},
	}
	cases := []fixtureCase{
		{Row: "a", Adapter: "a", Model: "m", Name: "text", Endpoint: "chat"},
		{Row: "a", Adapter: "a", Model: "m", Name: "embed", Endpoint: "embeddings", Error: true},
		{Row: "a", Adapter: "a", Model: "m", Name: "speak", Endpoint: "speech"},
		{Row: "c", Adapter: "openai", Model: "c@m", Name: "text", Endpoint: "chat"},
	}
	ex := Exemptions{Cells: []Exemption{
		{Provider: "a", Endpoints: []string{"embeddings"}, Kind: kindUncovered, Reason: "covered now"},
		{Provider: "a", Endpoints: []string{"rerank"}, Kind: kindNotServed, Reason: "not implemented"},
		{Provider: "b@", Endpoints: []string{"chat"}, Kind: "nope", Reason: "bad kind"},
		{Provider: "zz", Endpoints: []string{"chat"}, Kind: kindNotServed, Reason: "no such row"},
		{Provider: "a", Endpoints: []string{"list_models"}, Kind: kindNoUpstream, Reason: ""},
	}}
	g, problems := buildGrid(rows, cases, nil, ex)
	all := strings.Join(problems, "\n")
	for _, want := range []string{
		"a implements images, which has no contract case",
		"a/m/speak: endpoint speech, which a does not implement",
		`openai/c@m/text: no provider key "c"`,
		"stale: a embeddings has 1 cases",
		"stale: a does not implement rerank",
		`kind "nope"`,
		"(zz [chat]): no such provider key",
		"(a [list_models]): no reason",
	} {
		if !strings.Contains(all, want) {
			t.Errorf("problems lack %q:\n%s", want, all)
		}
	}
	if c := g.Cells["a"]["embeddings"]; c.Cases != 1 || c.Errors != 1 {
		t.Errorf("embeddings cell = %+v", c)
	}
	if g.Cells["a"]["list_models"].Exempt == nil {
		t.Error("an exemption that applies must mark its cell")
	}
}

func TestGapParsing(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"the field is dropped. A client cannot see it", "the field is dropped"},
		{"  spaced\n  out;  ", "spaced out"},
		{"version 3.5 is kept (see x). Then more", "version 3.5 is kept (see x)"},
		{"no period here", "no period here"},
	} {
		if got := firstSentence(tc.in); got != tc.want {
			t.Errorf("firstSentence(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
	s := `known gap T1/T2: signatures are dropped. More. Known gap X9: request_id is dropped; see gap B1`
	ms := gapDef.FindAllStringSubmatch(s, -1)
	if len(ms) != 2 || ms[0][1] != "T1/T2" || ms[1][1] != "X9" {
		t.Errorf("definitions = %q", ms)
	}
	// Every mention is a reference (a definition resolves itself).
	var refs []string
	for _, m := range gapRef.FindAllStringSubmatch(s, -1) {
		refs = append(refs, m[1])
	}
	if strings.Join(refs, ",") != "T1,X9,B1" {
		t.Errorf("references = %q", refs)
	}
	if !gapNoID.MatchString("known gap: no ID") || gapNoID.MatchString("known gap K1: an ID") {
		t.Error("gapNoID must match only the ID-less form")
	}
	for id, ok := range map[string]bool{"O32": true, "GQ1": true, "O05": false, "Q1": false, "o1": false, "ABCD1": false} {
		if _, _, got := prefixOf(id); got != ok {
			t.Errorf("prefixOf(%q) ok = %v, want %v", id, got, ok)
		}
	}
	idx := gapIndex([]gapOccurrence{
		{ID: "K1", Reason: "a", Provider: "cohere", Where: "m"},
		{ID: "K1", Reason: "a", Provider: "cohere", Where: "m"},
		{ID: "K1", Reason: "b", Provider: "cohere", Where: "spec cohere/chat"},
	})
	if len(idx["K1"].Reasons) != 2 || whereList(idx["K1"].Occ) != "cohere: m ×2, spec cohere/chat" {
		t.Errorf("index = %+v, where %q", idx["K1"], whereList(idx["K1"].Occ))
	}
}
