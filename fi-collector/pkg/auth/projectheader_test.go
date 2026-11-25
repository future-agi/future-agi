package auth

import (
	"context"
	"log/slog"
	"net/http"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc/metadata"
)

// oneLine is a single field line of X-Project-Name.
func oneLine(v string) ProjectNameValues { return ProjectNameValues{Canonical: []string{v}} }

func TestProjectNameFromHeaderValues(t *testing.T) {
	cases := []struct {
		name    string
		values  ProjectNameValues
		want    string
		wantErr string // and the error names wantHdr
		wantHdr string
	}{
		{name: "absent", values: ProjectNameValues{}, want: ""},
		{name: "single", values: oneLine("checkout-bot"), want: "checkout-bot"},
		{name: "trimmed", values: oneLine("  checkout-bot\t"), want: "checkout-bot"},
		{name: "blank is absent", values: oneLine("   "), want: ""},
		{name: "unicode", values: oneLine("проект-ü"), want: "проект-ü"},
		{name: "alias only", values: ProjectNameValues{Alias: []string{"from-alias"}}, want: "from-alias"},
		{name: "both spellings agree", values: ProjectNameValues{[]string{"a"}, []string{" a "}}, want: "a"},
		{name: "blank canonical, alias set", values: ProjectNameValues{[]string{" "}, []string{"b"}}, want: "b"},
		{name: "both spellings disagree", values: ProjectNameValues{[]string{"a"}, []string{"b"}},
			wantErr: "conflicting", wantHdr: ProjectNameHeaderAlias},
		// RFC 9110 section 5.3: repeated lines and a comma-joined value are
		// the same list once a proxy joins them, so both are refused.
		{name: "sent twice, disagreeing", values: ProjectNameValues{Canonical: []string{"a", "b"}},
			wantErr: "sent 2 times", wantHdr: ProjectNameHeader},
		{name: "sent twice, agreeing", values: ProjectNameValues{Canonical: []string{"a", "a"}},
			wantErr: "sent 2 times", wantHdr: ProjectNameHeader},
		{name: "sent twice, one blank", values: ProjectNameValues{Canonical: []string{"a", ""}},
			wantErr: "sent 2 times", wantHdr: ProjectNameHeader},
		{name: "alias sent twice", values: ProjectNameValues{Alias: []string{"a", "a"}},
			wantErr: "sent 2 times", wantHdr: ProjectNameHeaderAlias},
		{name: "comma-joined list", values: oneLine("a, b"), wantErr: "comma", wantHdr: ProjectNameHeader},
		{name: "comma-joined repeat", values: oneLine("a,a"), wantErr: "comma", wantHdr: ProjectNameHeader},
		{name: "comma in alias", values: ProjectNameValues{Alias: []string{"a,b"}}, wantErr: "comma", wantHdr: ProjectNameHeaderAlias},
		{name: "invalid utf-8", values: oneLine("bad\xff"), wantErr: "UTF-8", wantHdr: ProjectNameHeader},
		// The project name column holds 255 characters (not bytes).
		{name: "255 characters", values: oneLine(strings.Repeat("é", 255)), want: strings.Repeat("é", 255)},
		{name: "256 characters", values: oneLine(strings.Repeat("é", 256)), wantErr: "255 characters", wantHdr: ProjectNameHeader},
		{name: "tab inside", values: oneLine("a\tb"), wantErr: "control character", wantHdr: ProjectNameHeader},
		{name: "newline inside", values: oneLine("a\nb"), wantErr: "control character", wantHdr: ProjectNameHeader},
		{name: "DEL inside", values: oneLine("a\x7fb"), wantErr: "control character", wantHdr: ProjectNameHeader},
		{name: "C1 control inside", values: oneLine("a\u0085b"), wantErr: "control character", wantHdr: ProjectNameHeader},
		// Unicode format characters (category Cf) are invisible: a name with
		// one looks like another project's name but is a different one.
		{name: "zero width space", values: oneLine("check\u200bout"), wantErr: "U+200B", wantHdr: ProjectNameHeader},
		{name: "byte order mark", values: oneLine("\ufeffcheckout"), wantErr: "U+FEFF", wantHdr: ProjectNameHeader},
		{name: "right-to-left override", values: oneLine("checkout\u202e"), wantErr: "U+202E", wantHdr: ProjectNameHeader},
		{name: "soft hyphen", values: oneLine("check\u00adout"), wantErr: "format character", wantHdr: ProjectNameHeader},
		{name: "Cf in alias", values: ProjectNameValues{Alias: []string{"a\u2060b"}}, wantErr: "format character", wantHdr: ProjectNameHeaderAlias},
		// Line and paragraph separators break lines; the other
		// default-ignorable code points render as nothing, so a name made of
		// them looks blank.
		{name: "line separator", values: oneLine("pro\u2028j"), wantErr: "U+2028", wantHdr: ProjectNameHeader},
		{name: "paragraph separator", values: oneLine("pro\u2029j"), wantErr: "U+2029", wantHdr: ProjectNameHeader},
		{name: "only a Hangul filler", values: oneLine("\u3164"), wantErr: "U+3164", wantHdr: ProjectNameHeader},
		{name: "Hangul choseong filler", values: oneLine("pro\u115fj"), wantErr: "invisible character", wantHdr: ProjectNameHeader},
		{name: "combining grapheme joiner", values: oneLine("pro\u034fj"), wantErr: "U+034F", wantHdr: ProjectNameHeader},
		{name: "variation selector", values: oneLine("pro\ufe0fj"), wantErr: "U+FE0F", wantHdr: ProjectNameHeader},
		{name: "invisible character in alias", values: ProjectNameValues{Alias: []string{"a\u3164"}}, wantErr: "invisible character", wantHdr: ProjectNameHeaderAlias},
		// A name must show at least one character. Combining marks with no
		// base, and symbols drawn as empty space (U+2800 BRAILLE PATTERN
		// BLANK, U+1D159 MUSICAL SYMBOL NULL NOTEHEAD), render blank on their
		// own. Inside a visible name they are kept, like an inner space.
		{name: "only a braille blank", values: oneLine("\u2800"), wantErr: "no visible character", wantHdr: ProjectNameHeader},
		{name: "only blank symbols", values: oneLine(" \u2800\u2800\U0001D159 "), wantErr: "no visible character", wantHdr: ProjectNameHeader},
		{name: "only a combining mark", values: oneLine("\u0301"), wantErr: "no visible character", wantHdr: ProjectNameHeader},
		{name: "combining marks and blank symbols", values: oneLine("\u0301\u2800\u20dd"), wantErr: "no visible character", wantHdr: ProjectNameHeader},
		{name: "blank name in alias", values: ProjectNameValues{Alias: []string{"\u2800"}}, wantErr: "no visible character", wantHdr: ProjectNameHeaderAlias},
		{name: "braille blank inside a name", values: oneLine("pro\u2800j"), want: "pro\u2800j"},
		{name: "decomposed accent", values: oneLine("cafe\u0301"), want: "cafe\u0301"},
		{name: "symbol only", values: oneLine("\U0001F680"), want: "\U0001F680"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := projectNameFromHeaderValues(tc.values)
			if tc.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
					t.Fatalf("err = %v, want it to mention %q", err, tc.wantErr)
				}
				if !strings.Contains(err.Error(), tc.wantHdr) {
					t.Errorf("err %q should name the header %s", err, tc.wantHdr)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected err: %v", err)
			}
			if got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestProjectNameValuesReadCanonicalAndAliasHeaders(t *testing.T) {
	h := http.Header{}
	h.Add("x-project-name", "a")
	h.Add("Fi-Project-Name", "b")
	h.Add("Fi-Project-Name", "c")
	got := ProjectNameHTTPValues(h)
	if len(got.Canonical) != 1 || got.Canonical[0] != "a" || len(got.Alias) != 2 || got.Alias[1] != "c" {
		t.Errorf("HTTP values = %+v, want {[a] [b c]} (each line kept)", got)
	}
	md := metadata.Pairs("fi-project-name", "b", "x-project-name", "a", "x-project-name", "a")
	got = ProjectNameMetadataValues(md)
	if len(got.Canonical) != 2 || got.Canonical[0] != "a" || len(got.Alias) != 1 || got.Alias[0] != "b" {
		t.Errorf("metadata values = %+v, want {[a a] [b]}", got)
	}
}

func TestApplyProjectNameHeaderFillsOnlyMissingNames(t *testing.T) {
	traces := ptrace.NewTraces()
	named := traces.ResourceSpans().AppendEmpty()
	named.Resource().Attributes().PutStr("project_name", "from-attr")
	absent := traces.ResourceSpans().AppendEmpty()
	absent.Resource().Attributes().PutStr("service.name", "svc")
	empty := traces.ResourceSpans().AppendEmpty()
	empty.Resource().Attributes().PutStr("project_name", "")
	wrongType := traces.ResourceSpans().AppendEmpty()
	wrongType.Resource().Attributes().PutInt("project_name", 7)

	n, err := ApplyProjectNameHeader(traces, oneLine(" from-header "))
	if err != nil {
		t.Fatal(err)
	}
	if n != 3 {
		t.Fatalf("applied = %d, want 3 (absent, empty, non-string)", n)
	}
	want := []string{"from-attr", "from-header", "from-header", "from-header"}
	for i, w := range want {
		if got := getStrAttr(traces.ResourceSpans().At(i).Resource().Attributes(), "project_name"); got != w {
			t.Errorf("ResourceSpans[%d] project_name = %q, want %q", i, got, w)
		}
	}
	if got := getStrAttr(absent.Resource().Attributes(), "service.name"); got != "svc" {
		t.Errorf("other resource attributes must be kept, service.name = %q", got)
	}
}

func TestApplyProjectNameHeaderNoValuesIsNoOp(t *testing.T) {
	for _, values := range []ProjectNameValues{{}, oneLine(""), oneLine("  "), {Alias: []string{" "}}} {
		traces := ptrace.NewTraces()
		traces.ResourceSpans().AppendEmpty()
		n, err := ApplyProjectNameHeader(traces, values)
		if err != nil || n != 0 {
			t.Fatalf("values %+v: applied = %d, err = %v; want 0, nil", values, n, err)
		}
		if _, ok := traces.ResourceSpans().At(0).Resource().Attributes().Get("project_name"); ok {
			t.Errorf("values %+v must not add project_name", values)
		}
	}
}

// A bad header is only an error when the request needs it: requests whose
// resources all name their project behave exactly as before.
func TestApplyProjectNameHeaderIgnoresBadHeaderWhenNotNeeded(t *testing.T) {
	traces := ptrace.NewTraces()
	traces.ResourceSpans().AppendEmpty().Resource().Attributes().PutStr("project_name", "from-attr")
	bad := ProjectNameValues{[]string{"a", "b,c", "bad\xff\u200b"}, []string{"d"}}
	if n, err := ApplyProjectNameHeader(traces, bad); err != nil || n != 0 {
		t.Fatalf("applied = %d, err = %v; want 0, nil when every resource names a project", n, err)
	}

	traces.ResourceSpans().AppendEmpty()
	if _, err := ApplyProjectNameHeader(traces, bad); err == nil {
		t.Fatal("a bad header must be an error once a resource needs it")
	}
}

// recordingProjectResolver knows some projects and auto-creates the rest,
// recording what it was asked to create.
type recordingProjectResolver struct {
	known   map[string]string
	created []string
}

func (r *recordingProjectResolver) ResolveProjects(
	_ context.Context, _, _ string, names []string,
) (map[string]string, error) {
	out := make(map[string]string)
	for _, n := range names {
		if id, ok := r.known[n]; ok {
			out[n] = id
		}
	}
	return out, nil
}

func (r *recordingProjectResolver) GetOrCreateProject(
	_ context.Context, orgID, workspaceID, name, traceType string,
) (string, error) {
	r.created = append(r.created, orgID+"/"+workspaceID+"/"+name+"/"+traceType)
	return "created-" + name, nil
}

// A header-named project goes through the same workspace-scoped lookup and
// observe auto-create as an attribute-named one, and a ResourceSpans that
// names its own project in the same request keeps it.
func TestStampResolvesHeaderProjectLikeAttributeProject(t *testing.T) {
	resolver := &recordingProjectResolver{known: map[string]string{"attr-project": "id-attr"}}
	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), projects: resolver, log: slog.Default()}
	result := &ResolveResult{OrgID: "org-h", WorkspaceID: "ws-h", Projects: map[string]string{}}
	ck := "header-project-key"
	a.cache.putPositive(ck, result)

	traces := ptrace.NewTraces()
	fromAttr := traces.ResourceSpans().AppendEmpty()
	fromAttr.Resource().Attributes().PutStr("project_name", "attr-project")
	fromAttr.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("a")
	fromHeader := traces.ResourceSpans().AppendEmpty()
	fromHeader.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("b")

	// Without the header the request is rejected, as before.
	if _, err := StampResourceAttrs(context.Background(), a, ck, cloneTraces(traces), result); err == nil {
		t.Fatal("a ResourceSpans with no project_name and no header must still be rejected")
	}

	if n, err := ApplyProjectNameHeader(traces, oneLine("header-project")); err != nil || n != 1 {
		t.Fatalf("applied = %d, err = %v; want 1, nil", n, err)
	}
	dropped, err := StampResourceAttrs(context.Background(), a, ck, traces, result)
	if err != nil {
		t.Fatalf("stamp: %v", err)
	}
	if dropped != 0 {
		t.Fatalf("dropped = %d, want 0", dropped)
	}
	got := []string{
		getStrAttr(traces.ResourceSpans().At(0).Resource().Attributes(), "fi.project_id"),
		getStrAttr(traces.ResourceSpans().At(1).Resource().Attributes(), "fi.project_id"),
	}
	if got[0] != "id-attr" || got[1] != "created-header-project" {
		t.Errorf("fi.project_id = %v, want [id-attr created-header-project]", got)
	}
	if len(resolver.created) != 1 || resolver.created[0] != "org-h/ws-h/header-project/observe" {
		t.Errorf("auto-create calls = %v, want one observe project in the key's workspace", resolver.created)
	}
}

func cloneTraces(t ptrace.Traces) ptrace.Traces {
	out := ptrace.NewTraces()
	t.CopyTo(out)
	return out
}
