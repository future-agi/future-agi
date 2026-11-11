package contractspec

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"testing/fstest"
)

// fakeTB records what Check reports.
type fakeTB struct {
	errors, logs []string
	fatal        bool
}

func (f *fakeTB) Helper() {}
func (f *fakeTB) Errorf(format string, a ...any) {
	f.errors = append(f.errors, fmt.Sprintf(format, a...))
}
func (f *fakeTB) Logf(format string, a ...any) { f.logs = append(f.logs, fmt.Sprintf(format, a...)) }
func (f *fakeTB) Fatalf(format string, a ...any) {
	f.fatal = true
	f.errors = append(f.errors, fmt.Sprintf(format, a...))
	panic(f)
}

func (f *fakeTB) has(substr string) bool {
	for _, e := range f.errors {
		if strings.Contains(e, substr) {
			return true
		}
	}
	return false
}

// run calls Check, catching a Fatalf.
func (f *fakeTB) run(c Coverage) (reports []RootReport) {
	defer func() {
		if r := recover(); r != nil && r != f {
			panic(r)
		}
	}()
	return Check(f, c)
}

// The adapter's types for the synthetic "acme" API.
type acmeResponse struct {
	ID         string          `json:"id"`
	StopReason string          `json:"stop_reason"`
	Content    []acmeBlock     `json:"content"`
	Usage      acmeUsage       `json:"usage"`
	Raw        json.RawMessage `json:"raw"`
}

type acmeBlock struct {
	Type      string          `json:"type"`
	Text      string          `json:"text"`
	Citations json.RawMessage `json:"citations"`
}

type acmeCitation struct {
	URL string `json:"url"`
}

type acmeUsage struct {
	acmeTokens
}

type acmeTokens struct {
	Input int `json:"input_tokens"`
}

// acmeChunk captures unknown keys (declared Passthrough).
type acmeChunk struct {
	ID string `json:"id"`
}

func (c *acmeChunk) UnmarshalJSON([]byte) error { return nil }

var acmeSubsets = fstest.MapFS{
	"acme/chat.json": {Data: mustMarshal(&Subset{Schema: 1, Provider: "acme", Root: "chat", Format: FormatOpenAPI, Select: "s",
		Paths: map[string]*PathInfo{
			"$":                           {Kinds: []string{"object"}},
			"$.id":                        {Kinds: []string{"integer", "string"}},
			"$.stop_reason":               {Kinds: []string{"string"}, Enum: []string{"end", "max", "refusal"}},
			"$.content":                   {Kinds: []string{"array"}},
			"$.content[]":                 {Kinds: []string{"object"}},
			"$.content[].type":            {Kinds: []string{"string"}, Enum: []string{"text", "image"}},
			"$.content[].text":            {Kinds: []string{"string"}, Variants: []string{"type=text"}},
			"$.content[].source":          {Kinds: []string{"object"}, Variants: []string{"type=image"}},
			"$.content[].source.data":     {Kinds: []string{"string"}, Variants: []string{"type=image"}},
			"$.content[].citations":       {Kinds: []string{"array"}, Variants: []string{"type=text"}},
			"$.content[].citations[]":     {Kinds: []string{"object"}},
			"$.content[].citations[].url": {Kinds: []string{"string"}},
			"$.content[].citations[].idx": {Kinds: []string{"integer"}},
			"$.usage":                     {Kinds: []string{"object"}},
			"$.usage.input_tokens":        {Kinds: []string{"integer"}},
			"$.usage.cache_tokens":        {Kinds: []string{"integer"}},
			"$.raw":                       {Kinds: []string{"object"}},
			"$.raw.x":                     {Kinds: []string{"string"}},
			"$.extra":                     {Kinds: []string{"object"}, Nullable: true},
			"$.extra.y":                   {Kinds: []string{"string"}},
		}})},
	"acme/stream.json": {Data: mustMarshal(&Subset{Schema: 1, Provider: "acme", Root: "stream", Format: FormatOpenAPI, Select: "s",
		Events: []string{"chunk", "ping"},
		Paths: map[string]*PathInfo{
			"chunk:$":        {Kinds: []string{"object"}},
			"chunk:$.id":     {Kinds: []string{"string"}},
			"chunk:$.meta":   {Kinds: []string{"object"}},
			"chunk:$.meta.z": {Kinds: []string{"string"}},
			"ping:$":         {Kinds: []string{"object"}},
			"ping:$.at":      {Kinds: []string{"integer"}},
		}})},
	"acme/models.json": {Data: mustMarshal(&Subset{Schema: 1, Provider: "acme", Root: "models", Format: FormatOpenAPI, Select: "s",
		Paths: map[string]*PathInfo{"$": {Kinds: []string{"object"}}}})},
}

func mustMarshal(s *Subset) []byte {
	raw, err := s.Marshal()
	if err != nil {
		panic(err)
	}
	return raw
}

func acmeCoverage(allowlist string) Coverage {
	return Coverage{
		Provider:  "acme",
		Allowlist: allowlist,
		Subsets:   acmeSubsets,
		Roots: []Root{
			{
				Subset: "acme/chat",
				Bindings: []Binding{
					Bind("$", reflect.TypeFor[acmeResponse]()),
					Bind("$.content[].citations", reflect.TypeFor[[]acmeCitation]()),
				},
				Enums: []EnumCheck{
					{Path: "$.stop_reason", By: "lookup", Handled: Set("end", "max")},
					{Path: "$.content[].type", Handled: Set("text")},
				},
			},
			{
				Subset:      "acme/stream",
				EachEvent:   []reflect.Type{reflect.TypeFor[acmeChunk]()},
				Passthrough: []reflect.Type{reflect.TypeFor[acmeChunk]()},
				Events:      Set("chunk"),
			},
		},
		Elsewhere: map[string]string{"acme/models": "parsed by another package"},
	}
}

func writeAllowlist(t *testing.T, al Allowlist) string {
	t.Helper()
	raw, err := json.Marshal(al)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "spec_allowlist.json")
	if err := os.WriteFile(file, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	return file
}

// acmeAllowlist accounts for everything the acme adapter does not map.
func acmeAllowlist() Allowlist {
	return Allowlist{
		Provider: "acme",
		Unmapped: []AllowedPath{
			{Subset: "acme/chat", Path: "$.content[].citations[].idx", Reason: "known gap Z1: citation index dropped"},
			{Subset: "acme/chat", Path: "$.usage.cache_tokens", Reason: "known gap Z2: cache tokens dropped"},
			{Subset: "acme/chat", Path: "$.extra", Reason: "no canonical field: extra"},
		},
		Opaque: []AllowedPath{{Subset: "acme/chat", Path: "$.raw", Reason: "forwarded verbatim"}},
		Enums: []AllowedValue{
			{Subset: "acme/chat", Path: "$.stop_reason", Value: "refusal", Reason: "known gap Z3: refusal falls back to stop"},
			{Subset: "acme/chat", Path: "$.content[].type", Value: "image", Reason: "known gap Z4: images dropped"},
		},
		Events: []AllowedEvent{{Subset: "acme/stream", Event: "ping", Reason: "no content: keep-alive"}},
		Kinds:  []AllowedPath{{Subset: "acme/chat", Path: "$.id", Reason: "latent: ids are strings in practice"}},
	}
}

func TestCheckAccountsForEverything(t *testing.T) {
	var tb fakeTB
	reports := tb.run(acmeCoverage(writeAllowlist(t, acmeAllowlist())))
	if len(tb.errors) > 0 {
		t.Fatalf("Check failed a fully accounted adapter:\n%s", strings.Join(tb.errors, "\n"))
	}
	chat, stream := reports[0], reports[1]
	// Mapped: $, id, stop_reason, content, content[], type, text, citations
	// (rebound), citations[], url, usage, usage.input_tokens (promoted from
	// an embedded struct).
	want := RootReport{Subset: "acme/chat", Total: 20, Mapped: 12, Opaque: 2, Allowlisted: 4, Implied: 2,
		Unmapped: []string{"$.content[].citations[].idx", "$.extra", "$.usage.cache_tokens"}, Blobs: []string{"$.raw"},
		EnumPaths: 2, EnumValues: 5, EnumAllowlisted: 2}
	if !reflect.DeepEqual(chat.Mismatched, []string{"$.id"}) || len(chat.MismatchUnaccounted) != 0 {
		t.Errorf("mismatches %v, unaccounted %v", chat.Mismatched, chat.MismatchUnaccounted)
	}
	chat.missingEnums, chat.EnumPathsInSpec, chat.Mismatched, chat.mismatchWhy = nil, 0, nil, nil
	if !reflect.DeepEqual(chat, want) {
		t.Errorf("chat report\n got %+v\nwant %+v", chat, want)
	}
	if chat.Accounted() != chat.Total {
		t.Errorf("chat: %d of %d accounted", chat.Accounted(), chat.Total)
	}
	// chunk:$.meta(.z) is forwarded by the passthrough type; ping's paths
	// are covered by its event entry.
	if stream.Mapped != 2 || stream.Passthrough != 2 || stream.Implied != 2 || stream.EventsAllowlisted != 1 {
		t.Errorf("stream report %+v", stream)
	}
	if !strings.Contains(chat.Summary(), "acme/chat: 12/20 paths mapped") || !strings.Contains(chat.Summary(), "20/20 accounted") {
		t.Errorf("summary %q", chat.Summary())
	}
}

func TestCheckReportsWhatIsNotAccounted(t *testing.T) {
	al := acmeAllowlist()
	al.Unmapped = al.Unmapped[1:]                                                                  // drop the citation idx entry
	al.Opaque = nil                                                                                // and the blob declaration
	al.Enums = al.Enums[:1]                                                                        // and the image entry
	al.Events = nil                                                                                // and ping
	al.Kinds = nil                                                                                 // and the kind mismatch
	al.Unmapped = append(al.Unmapped, AllowedPath{Subset: "acme/chat", Path: "$.id", Reason: "x"}) // stale: mapped
	al.Unmapped = append(al.Unmapped, AllowedPath{Subset: "acme/chat", Path: "$.gone", Reason: "TODO"})
	var tb fakeTB
	tb.run(acmeCoverage(writeAllowlist(t, al)))
	for _, want := range []string{
		"$.content[].citations[].idx  [integer]",
		"Go blobs (json.RawMessage / any) hold spec structure",
		`$.content[].type = "image"`,
		"spec stream events the adapter does not handle: ping",
		"stale allowlist entry (nothing matches it any more; delete it): unmapped acme/chat $.id",
		"entry without a real reason: unmapped acme/chat $.gone",
		"$.id: the spec allows integer, which string does not decode",
	} {
		if !tb.has(want) {
			t.Errorf("Check did not report %q; errors:\n%s", want, strings.Join(tb.errors, "\n"))
		}
	}
}

func TestCheckRootsMustBeDeclared(t *testing.T) {
	c := acmeCoverage(writeAllowlist(t, acmeAllowlist()))
	c.Elsewhere = nil
	var tb fakeTB
	tb.run(c)
	if !tb.has("subset acme/models has no coverage declaration") {
		t.Errorf("an undeclared root passed; errors:\n%s", strings.Join(tb.errors, "\n"))
	}
}

func TestCheckHandledValueMakesEntryStale(t *testing.T) {
	c := acmeCoverage(writeAllowlist(t, acmeAllowlist()))
	c.Roots[0].Enums[0].Handled = Set("end", "max", "refusal") // refusal is now mapped
	var tb fakeTB
	tb.run(c)
	if !tb.has("stale allowlist entry (nothing matches it any more; delete it): enum acme/chat $.stop_reason = refusal") {
		t.Errorf("a handled value's entry was not reported stale; errors:\n%s", strings.Join(tb.errors, "\n"))
	}
}

func TestCheckDraft(t *testing.T) {
	file := filepath.Join(t.TempDir(), "spec_allowlist.json")
	t.Setenv(DraftEnv, "1")
	var tb fakeTB
	tb.run(acmeCoverage(file))
	raw, err := os.ReadFile(file)
	if err != nil {
		t.Fatalf("no draft written: %v (errors %v)", err, tb.errors)
	}
	var al Allowlist
	if err := json.Unmarshal(raw, &al); err != nil {
		t.Fatal(err)
	}
	if len(al.Unmapped) != 3 || len(al.Opaque) != 1 || len(al.Enums) != 2 || len(al.Events) != 1 || len(al.Kinds) != 1 {
		t.Errorf("draft = %+v", al)
	}
	for _, e := range al.Unmapped {
		if !strings.HasPrefix(e.Reason, "TODO") {
			t.Errorf("a new entry has reason %q, want TODO", e.Reason)
		}
	}
	if len(tb.errors) == 0 {
		t.Error("the drafting run passed although nothing was accounted for")
	}
	// The draft accounts for everything, but its TODO reasons still fail.
	t.Setenv(DraftEnv, "")
	var again fakeTB
	again.run(acmeCoverage(file))
	if !again.has("entry without a real reason") || again.has("do not map") {
		t.Errorf("second run errors:\n%s", strings.Join(again.errors, "\n"))
	}
}

func TestCheckDeclaredEnums(t *testing.T) {
	var tb fakeTB
	func() {
		defer func() { _ = recover() }()
		CheckDeclaredEnums(&tb, EnumRouting{Body: "anthropic/messages", Stream: "anthropic/stream", Error: "anthropic/errors"}, []EnumDecl{
			{Name: "StopReason", Paths: []string{"$.stop_reason"}, Values: []string{"end_turn", "max_tokens", "invented"}},
			{Name: "events", Paths: []string{"*:$.type"}, Values: []string{"message_start", "ping"}},
			{Name: "ErrorType", Paths: []string{"error:$.error.type"}, Values: []string{"api_error"}},
		})
	}()
	for _, want := range []string{
		"declared enum StopReason at $.stop_reason lacks values the spec lists at anthropic/messages $.stop_reason",
		"declared enum events at *:$.type lacks values the spec lists at anthropic/stream events",
		"declared enum ErrorType at error:$.error.type lacks values the spec lists at anthropic/errors $.error.type",
	} {
		if !tb.has(want) {
			t.Errorf("not reported: %q; errors:\n%s", want, strings.Join(tb.errors, "\n"))
		}
	}
	// A "*:" path spans every event, and a same_as node defers to the subset
	// that flattens it (Responses stream events carry the whole Response).
	var wild fakeTB
	CheckDeclaredEnums(&wild, EnumRouting{Body: "openai/responses", Stream: "openai/responses_stream"}, []EnumDecl{
		{Name: "Response.object", Paths: []string{"*:$.response.object"}, Values: []string{}},
	})
	if !wild.has("declared enum Response.object at *:$.response.object lacks values the spec lists at openai/responses_stream *:$.response.object: response") {
		t.Errorf("wildcard / same_as path not resolved; errors:\n%s", strings.Join(wild.errors, "\n"))
	}
	logs := strings.Join(tb.logs, "\n")
	if !strings.Contains(logs, "has values the spec does not list at anthropic/messages $.stop_reason: invented") {
		t.Errorf("an extra declared value was not logged:\n%s", logs)
	}
}
