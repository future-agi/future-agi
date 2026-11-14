package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
)

func strictReceiptSeed(t *testing.T) []byte {
	t.Helper()
	b, err := os.ReadFile("../../pkg/ingestbench/testdata/round3/positive-exact-head-mock.json")
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func refuseReceipt(t *testing.T, raw []byte, field string) {
	t.Helper()
	dir := t.TempDir()
	input := filepath.Join(dir, "input.json")
	if err := os.WriteFile(input, raw, 0600); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"report", "publish"} {
		dest := filepath.Join(dir, mode)
		if err := os.Mkdir(dest, 0700); err != nil {
			t.Fatal(err)
		}
		args := []string{mode, input}
		if mode == "publish" {
			args = []string{mode, "--min-sustained", "1ns", "--markdown", filepath.Join(dest, "report.txt"), "--json", filepath.Join(dest, "publication.json"), input}
		}
		var out bytes.Buffer
		err := command(context.Background(), args, &out)
		if err == nil || !strings.Contains(err.Error(), field) {
			t.Errorf("%s must reject %s: %v", mode, field, err)
		}
		entries, err := os.ReadDir(dest)
		if err != nil || len(entries) != 0 || out.Len() != 0 {
			t.Errorf("%s emitted before rejection: files=%d stdout=%d err=%v", mode, len(entries), out.Len(), err)
		}
	}
}

func TestStrictReceiptFamiliesBeforeOutputs(t *testing.T) {
	for _, tc := range []struct {
		id    int
		field string
	}{
		{1195, "schedule"}, {20025, "buckets"}, {20022, "buckets"}, {20023, "buckets"},
		{997, "otlp_request_latency.p50_ms"}, {682, "counters.otlp_partial_rejected_spans"},
		{5941, "payload_text_bytes_read"}, {8615, "planned_spans"}, {8616, "registered_sentinels"},
	} {
		t.Run(fmt.Sprint(tc.id), func(t *testing.T) {
			raw, err := os.ReadFile(fmt.Sprintf("../../pkg/ingestbench/testdata/round4/counterexample-%d.json", tc.id))
			if err != nil {
				t.Fatal(err)
			}
			refuseReceipt(t, raw, tc.field)
		})
	}
}

func TestStrictReceiptCaseFoldDuplicates(t *testing.T) {
	for _, key := range []string{"planned_spans", "PLANNED_SPANS", "Planned_Spans"} {
		for _, first := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/first=%v", key, first), func(t *testing.T) {
				raw := bytes.TrimSpace(strictReceiptSeed(t))
				if first {
					raw = append([]byte(`{"`+key+`":1,`), raw[1:]...)
				} else {
					raw = append(raw[:len(raw)-1], []byte(`,"`+key+`":1}`)...)
				}
				refuseReceipt(t, raw, "planned_spans")
			})
		}
	}
	// Equal duplicates are ambiguous input too, not a separate defaulting policy.
	raw := bytes.TrimSpace(strictReceiptSeed(t))
	raw = append([]byte(`{"planned_spans":12,`), raw[1:]...)
	refuseReceipt(t, raw, "planned_spans")
}

// Enumerate the wire tree with its Go types, not a hand-maintained numeric list.
// Optional pointers/containers and omitempty fields are distinguished from the
// required scalar leaves. Every supplied numeric map value is nonnullable.
func strictNumericLeaves(typ reflect.Type, value any, path []any, visit func([]any, bool)) {
	if typ == reflect.TypeOf(time.Time{}) || typ == reflect.TypeOf(json.RawMessage{}) {
		return
	}
	if typ.Kind() == reflect.Pointer {
		// A scalar pointer is nullable; do not turn a legitimate null into a
		// rejection expectation. Still inspect fields inside present objects.
		switch typ.Elem().Kind() {
		case reflect.Struct, reflect.Map, reflect.Slice, reflect.Array, reflect.Pointer:
			if value != nil {
				strictNumericLeaves(typ.Elem(), value, path, visit)
			}
		}
		return
	}
	switch typ.Kind() {
	case reflect.Struct:
		obj, ok := value.(map[string]any)
		if !ok {
			return
		}
		for i := 0; i < typ.NumField(); i++ {
			f := typ.Field(i)
			tag := strings.Split(f.Tag.Get("json"), ",")
			key := tag[0]
			if f.PkgPath != "" || key == "" || key == "-" {
				continue
			}
			v, ok := obj[key]
			if !ok {
				continue
			}
			p := append(append([]any{}, path...), key)
			required := !strings.Contains(f.Tag.Get("json"), ",omitempty") && f.Type.Kind() != reflect.Pointer
			switch f.Type.Kind() {
			case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Float32, reflect.Float64:
				visit(p, required)
			default:
				strictNumericLeaves(f.Type, v, p, visit)
			}
		}
	case reflect.Slice, reflect.Array:
		a, _ := value.([]any)
		for i, v := range a {
			strictNumericLeaves(typ.Elem(), v, append(append([]any{}, path...), i), visit)
		}
	case reflect.Map:
		m, _ := value.(map[string]any)
		for k, v := range m {
			strictNumericLeaves(typ.Elem(), v, append(append([]any{}, path...), k), visit)
		}
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Float32, reflect.Float64:
		visit(path, false)
	}
}

func TestStrictReceiptNumericShapeMetamorphic(t *testing.T) {
	raw := strictReceiptSeed(t)
	var root map[string]any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&root); err != nil {
		t.Fatal(err)
	}
	var export *os.File
	if name := os.Getenv("INGESTBENCH_SHAPE_CORPUS_OUT"); name != "" {
		var err error
		export, err = os.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		defer export.Close()
	}
	leaves, cases := 0, 0
	strictNumericLeaves(reflect.TypeOf(ingestbench.Result{}), root, nil, func(path []any, required bool) {
		leaves++
		var parent any = root
		for _, key := range path[:len(path)-1] {
			switch k := key.(type) {
			case string:
				parent = parent.(map[string]any)[k]
			case int:
				parent = parent.([]any)[k]
			}
		}
		key := path[len(path)-1]
		var old any
		var set func(any)
		var remove func()
		switch k := key.(type) {
		case string:
			obj := parent.(map[string]any)
			old = obj[k]
			set = func(v any) { obj[k] = v }
			remove = func() { delete(obj, k) }
		case int:
			obj := parent.([]any)
			old = obj[k]
			set = func(v any) { obj[k] = v }
		}
		for _, op := range []string{"null", "missing"} {
			if op == "missing" && (!required || remove == nil) {
				continue
			}
			if op == "null" {
				set(nil)
			} else {
				remove()
			}
			changed, err := json.Marshal(root)
			if err != nil {
				t.Fatal(err)
			}
			name := fmt.Sprintf("%v/%s", path, op)
			cases++
			t.Run(name, func(t *testing.T) { refuseReceipt(t, changed, fmt.Sprint(key)) })
			if export != nil {
				b, _ := json.Marshal(map[string]any{"name": name, "raw": string(changed), "oracle": false})
				if _, err := export.Write(append(b, '\n')); err != nil {
					t.Fatal(err)
				}
			}
			set(old)
		}
	})
	if leaves < 500 || cases < 900 {
		t.Fatalf("vacuous shape corpus: %d leaves %d cases", leaves, cases)
	}
	t.Logf("numeric shape leaves=%d cases=%d", leaves, cases)
}

func TestStrictReceiptOptionalFields(t *testing.T) {
	raw := strictReceiptSeed(t)
	for _, mode := range []string{"missing", "null", "case-fold-single"} {
		t.Run(mode, func(t *testing.T) {
			var doc map[string]json.RawMessage
			if err := json.Unmarshal(raw, &doc); err != nil {
				t.Fatal(err)
			}
			for _, field := range []string{"recovery", "workload", "provenance", "resources", "warnings", "application_reads", "direct_clickhouse_reads"} {
				if mode == "missing" {
					delete(doc, field)
				} else if mode == "null" {
					doc[field] = json.RawMessage("null")
				}
			}
			if mode == "case-fold-single" {
				doc["PLANNED_SPANS"] = doc["planned_spans"]
				delete(doc, "planned_spans")
			}
			b, _ := json.Marshal(doc)
			p := filepath.Join(t.TempDir(), "input.json")
			os.WriteFile(p, b, 0600)
			var out bytes.Buffer
			if err := command(context.Background(), []string{"report", p}, &out); err != nil || out.Len() == 0 {
				t.Fatalf("optional %s: %v", mode, err)
			}
		})
	}
}
