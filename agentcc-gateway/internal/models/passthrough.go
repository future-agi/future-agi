package models

import (
	"encoding/json"
	"reflect"
	"strings"
	"sync"
)

// Unknown-field pass-through, shared by the response types.
//
// A provider answers with more than the OpenAI schema names: Perplexity adds
// citations and search_results, others add their own usage counters. The
// gateway rebuilds every response on its canonical struct, so a field the
// struct does not name is dropped and the caller never learns it existed. The
// request path already keeps unknown fields and forwards them
// (ChatCompletionRequest.Extra); these helpers give the response path the same
// promise.
//
// The set of known keys is read from the struct tags rather than listed by
// hand, so a field added to a struct cannot drift out of its own capture.

var knownFieldsCache sync.Map // reflect.Type -> map[string]bool

// knownJSONFields returns the JSON keys that marshalling t produces.
func knownJSONFields(t reflect.Type) map[string]bool {
	if cached, ok := knownFieldsCache.Load(t); ok {
		return cached.(map[string]bool)
	}
	known := make(map[string]bool, t.NumField())
	for i := range t.NumField() {
		f := t.Field(i)
		if f.PkgPath != "" {
			continue // unexported
		}
		name, _, _ := strings.Cut(f.Tag.Get("json"), ",")
		switch name {
		case "-":
			continue // deliberately not serialized, Extra itself included
		case "":
			name = f.Name // Go's own default when there is no tag
		}
		known[name] = true
	}
	knownFieldsCache.Store(t, known)
	return known
}

// captureExtra records the keys of data that t does not name.
func captureExtra(data []byte, t reflect.Type) (map[string]json.RawMessage, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	known := knownJSONFields(t)
	var extra map[string]json.RawMessage
	for k, v := range raw {
		if known[k] {
			continue
		}
		if extra == nil {
			extra = make(map[string]json.RawMessage)
		}
		extra[k] = v
	}
	return extra, nil
}

// mergeExtra writes the captured keys back beside the ones the struct names.
// A key the struct now names wins: the struct is the gateway's own answer, and
// a stale captured copy of it would undo whatever the gateway normalized.
func mergeExtra(data []byte, extra map[string]json.RawMessage) ([]byte, error) {
	if len(extra) == 0 {
		return data, nil
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(data, &obj); err != nil {
		return nil, err
	}
	for k, v := range extra {
		if _, taken := obj[k]; taken {
			continue
		}
		obj[k] = v
	}
	return json.Marshal(obj)
}
