package contracttest

import (
	"encoding/json"
	"reflect"
	"slices"
	"testing"
	"time"
)

type inner struct {
	Known string `json:"known"`
}

type Embedded struct {
	Promoted string `json:"promoted"`
	Shadowed string `json:"shadowed"`
}

type withUnmarshaler struct{}

func (*withUnmarshaler) UnmarshalJSON([]byte) error { return nil }

type outer struct {
	Embedded
	Shadowed  int                    `json:"shadowed"`
	Name      string                 `json:"name,omitempty"`
	Ptr       *inner                 `json:"ptr"`
	List      []inner                `json:"list"`
	PtrList   []*inner               `json:"ptr_list"`
	ByKey     map[string]inner       `json:"by_key"`
	Anything  map[string]any         `json:"anything"`
	Iface     any                    `json:"iface"`
	Raw       json.RawMessage        `json:"raw"`
	Custom    withUnmarshaler        `json:"custom"`
	When      time.Time              `json:"when"`
	Count     int                    `json:"count,string"`
	Bytes     []byte                 `json:"bytes"`
	Ignored   string                 `json:"-"`
	Dash      string                 `json:"-,"`
	Untagged  string                 //nolint:unused // decoded by field name
	unexposed string                 //nolint:unused // never decoded
	Nested    map[string][]*inner    `json:"nested"`
	RawByKey  map[string]json.Number `json:"raw_by_key"`
}

func TestUnknownFields(t *testing.T) {
	raw := `{
		"name": "x",
		"promoted": "p",
		"shadowed": 1,
		"ptr": {"known": "a", "extra_ptr": 1},
		"list": [{"known": "a"}, {"known": "b", "extra_list": true}],
		"ptr_list": [{"extra_ptr_list": 1}],
		"by_key": {"k1": {"known": "a"}, "k2": {"extra_map": 1}},
		"anything": {"whatever": {"deep": 1}},
		"iface": {"whatever": 1},
		"raw": {"whatever": 1},
		"custom": {"whatever": 1},
		"when": "2025-01-01T00:00:00Z",
		"count": "12",
		"bytes": "aGVsbG8=",
		"Ignored": "dropped by json:\"-\"",
		"-": "named dash",
		"UNTAGGED": "case-insensitive match",
		"unexposed": "unexported fields are not decoded",
		"nested": {"a": [{"known": "x", "extra_nested": 1}]},
		"raw_by_key": {"a": 1},
		"top_extra": {"child": 1}
	}`
	got := UnknownFields([]byte(raw), reflect.TypeFor[outer]())
	want := []string{
		"$.Ignored",
		"$.by_key.*.extra_map",
		"$.list[].extra_list",
		"$.nested.*[].extra_nested",
		"$.ptr.extra_ptr",
		"$.ptr_list[].extra_ptr_list",
		"$.top_extra",
		"$.unexposed",
	}
	if !slices.Equal(got, want) {
		t.Errorf("UnknownFields =\n  %q\nwant\n  %q", got, want)
	}
}

func TestUnknownFields_PointerTypeAndRootArray(t *testing.T) {
	got := UnknownFields([]byte(`[{"known":"a","x":1}]`), reflect.TypeFor[[]*inner]())
	if want := []string{"$[].x"}; !slices.Equal(got, want) {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestUnknownFields_KindMismatchIsNotUnknown(t *testing.T) {
	// A string where a struct is expected is a decode error, not a field the
	// struct forgot.
	if got := UnknownFields([]byte(`{"ptr":"oops"}`), reflect.TypeFor[outer]()); len(got) != 0 {
		t.Errorf("got %q, want none", got)
	}
}

func TestUnknownFields_InvalidJSON(t *testing.T) {
	if got := UnknownFields([]byte(`{`), reflect.TypeFor[outer]()); !slices.Equal(got, []string{"$"}) {
		t.Errorf("got %q, want [$]", got)
	}
}

func TestUnknownFieldsAt(t *testing.T) {
	got := UnknownFieldsAt([]byte(`{"known":"a","new":1}`), reflect.TypeFor[inner](), "message_start:$.message")
	if want := []string{"message_start:$.message.new"}; !slices.Equal(got, want) {
		t.Errorf("got %q, want %q", got, want)
	}
}
