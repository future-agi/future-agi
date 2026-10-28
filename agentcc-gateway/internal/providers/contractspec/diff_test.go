package contractspec

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"reflect"
	"strings"
	"testing"
)

func subsetOf(events []string, paths map[string]*PathInfo) *Subset {
	return &Subset{Schema: SchemaVersion, Provider: "acme", Root: "chat", Format: FormatOpenAPI, Select: "sel", Events: events, Paths: paths}
}

func TestDiffSubsets(t *testing.T) {
	old := subsetOf([]string{"start"}, map[string]*PathInfo{
		"$":              {Kinds: []string{"object"}},
		"$.stop_reason":  {Kinds: []string{"string"}, Enum: []string{"end_turn", "max_tokens"}},
		"$.model":        {Kinds: []string{"string"}, Enum: []string{"m-1"}, OpenEnum: true},
		"$.count":        {Kinds: []string{"integer"}},
		"$.tools":        {Kinds: []string{"array"}},
		"$.gone":         {Kinds: []string{"string"}},
		"$.flag":         {Kinds: []string{"boolean"}, Nullable: true},
		"start:$.id":     {Kinds: []string{"string"}},
		"start:$.legacy": {Kinds: []string{"string"}},
	})
	fresh := subsetOf([]string{"start", "delta"}, map[string]*PathInfo{
		"$":                      {Kinds: []string{"object"}},
		"$.stop_reason":          {Kinds: []string{"string"}, Enum: []string{"end_turn", "max_tokens", "refusal"}},
		"$.model":                {Kinds: []string{"string"}, Enum: []string{"m-1", "m-2"}, OpenEnum: true},
		"$.count":                {Kinds: []string{"string"}},
		"$.tools":                {Kinds: []string{"array"}, Nullable: true},
		"$.flag":                 {Kinds: []string{"boolean"}},
		"$.diagnostics":          {Kinds: []string{"object"}, Nullable: true},
		"$.diagnostics.reason":   {Kinds: []string{"string"}, Enum: []string{"a", "b"}},
		"$.diagnostics.detail.x": {Kinds: []string{"string"}},
		"start:$.id":             {Kinds: []string{"string"}, Deprecated: true},
		"start:$.legacy":         {Kinds: []string{"string"}},
		"delta:$":                {Kinds: []string{"object"}},
		"delta:$.text":           {Kinds: []string{"string"}},
	})
	d := DiffSubsets(old, fresh)
	type line struct {
		kind ChangeKind
		path string
	}
	var got []line
	for _, c := range d.Changes {
		got = append(got, line{c.Kind, c.Path})
	}
	want := []line{
		// Failing first, by path.
		{ChangeKindAdded, "$.count"},
		{ChangeNewField, "$.diagnostics"},
		{ChangeNewEnumValue, "$.stop_reason"},
		{ChangeBecameNullable, "$.tools"},
		{ChangeNewEvent, "delta:$"},
		{ChangeNoLongerNullable, "$.flag"},
		{ChangeRemovedField, "$.gone"},
		{ChangeOpenEnumGrew, "$.model"},
		{ChangeDeprecated, "start:$.id"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("changes:\n got %v\nwant %v", got, want)
	}
	if d.Failing() != 5 {
		t.Errorf("Failing = %d, want 5", d.Failing())
	}
	for _, c := range d.Changes {
		switch c.Path {
		case "$.diagnostics":
			if c.Below != 2 || !strings.Contains(c.Detail, "object|null") {
				t.Errorf("new field change = %+v, want 2 paths below and kinds object|null", c)
			}
		case "delta:$":
			if c.Below != 2 {
				t.Errorf("new event counts %d paths, want 2", c.Below)
			}
		case "$.stop_reason":
			if c.Detail != "+ refusal" {
				t.Errorf("enum detail = %q", c.Detail)
			}
		}
	}

	var out bytes.Buffer
	d.Format(&out)
	for _, want := range []string{
		"acme/chat:",
		"FAIL  new field            $.diagnostics",
		"(+2 paths below)",
		"FAIL  new enum value       $.stop_reason",
		"+ refusal",
		"FAIL  new stream event     delta:$",
		"FAIL  kind changed         $.count",
		"integer -> string",
		"info  open enum grew       $.model",
		"=> 5 failing change(s), 4 informational",
	} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("formatted diff lacks %q:\n%s", want, out.String())
		}
	}

	if same := DiffSubsets(old, old); len(same.Changes) != 0 {
		t.Errorf("a subset differs from itself: %+v", same.Changes)
	}
}

func TestDiffProviderRoots(t *testing.T) {
	a := subsetOf(nil, map[string]*PathInfo{"$": {}})
	b := subsetOf(nil, map[string]*PathInfo{"$": {}})
	b.Root = "embeddings"
	diffs := DiffProvider([]*Subset{a}, []*Subset{b})
	if len(diffs) != 2 || diffs[0].Changes[0].Kind != ChangeRemovedRoot || diffs[1].Changes[0].Kind != ChangeNewRoot {
		t.Fatalf("diffs = %+v", diffs)
	}
	if diffs[0].Failing() != 1 || diffs[1].Failing() != 1 {
		t.Error("a new or removed root must fail: the committed subsets are out of date")
	}
}

func TestSubsetRoundTrip(t *testing.T) {
	s := subsetOf([]string{"b", "a"}, map[string]*PathInfo{
		"$.x":   {Kinds: []string{"string", "integer"}, Enum: []string{"z", "<a&b>"}},
		"$":     {Kinds: []string{"object"}},
		"a:$.y": {Variants: []string{"type=b", "type=a", "type=a"}},
	})
	raw, err := s.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	want := `{
  "schema": 1,
  "provider": "acme",
  "root": "chat",
  "format": "openapi",
  "select": "sel",
  "events": ["a","b"],
  "paths": {
    "$": {"kinds":["object"]},
    "$.x": {"kinds":["integer","string"],"enum":["<a&b>","z"]},
    "a:$.y": {"variants":["type=a","type=b"]}
  }
}
`
	if string(raw) != want {
		t.Errorf("Marshal:\n%s\nwant:\n%s", raw, want)
	}
	back, err := ParseSubset(raw)
	if err != nil {
		t.Fatal(err)
	}
	again, _ := back.Marshal()
	if !bytes.Equal(raw, again) {
		t.Error("Marshal is not stable across a round trip")
	}
	if _, err := ParseSubset([]byte(strings.Replace(want, `"schema": 1`, `"schema": 9`, 1))); err == nil {
		t.Error("another schema version was accepted")
	}
	if _, err := ParseSubset([]byte(strings.Replace(want, `"select"`, `"selekt"`, 1))); err == nil {
		t.Error("an unknown key was accepted")
	}
}

func TestDecodeSource(t *testing.T) {
	spec := []byte(`{"openapi":"3.1.0"}`)
	var gz bytes.Buffer
	zw := gzip.NewWriter(&gz)
	_, _ = zw.Write(spec)
	_ = zw.Close()

	got, err := DecodeSource(gz.Bytes(), DecodeGzip)
	if err != nil || !bytes.Equal(got, spec) {
		t.Errorf("gzip: %q, %v", got, err)
	}
	b64 := base64.StdEncoding.EncodeToString(gz.Bytes())
	script := "#!/usr/bin/env bash\nif x; then\n  EMBEDDED_SPEC=\"" + b64[:10] + "\n" + b64[10:] + "\"\n  echo \"$EMBEDDED_SPEC\" | base64 --decode\nfi\n"
	got, err = DecodeSource([]byte(script), DecodeEmbeddedMock)
	if err != nil || !bytes.Equal(got, spec) {
		t.Errorf("embedded mock: %q, %v", got, err)
	}
	if _, err := DecodeSource([]byte("#!/bin/sh\necho hi\n"), DecodeEmbeddedMock); err == nil {
		t.Error("a script with no embedded spec was accepted")
	}
	if _, err := DecodeSource(spec, "rot13"); err == nil {
		t.Error("an unknown decoder was accepted")
	}
}

func TestParseDocumentYAMLKeys(t *testing.T) {
	doc, err := ParseDocument([]byte("paths:\n  /x:\n    get:\n      responses:\n        200: {description: ok}\n"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pointer(doc, "#/paths/~1x/get/responses/200"); err != nil {
		t.Errorf("a bare 200 key is not reachable as \"200\": %v", err)
	}
	if _, err := ParseDocument([]byte("[1, 2]")); err == nil {
		t.Error("a non-object spec was accepted")
	}
}
