package protocol

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestStrings(t *testing.T) {
	raw := json.RawMessage(`{"content":[{"type":"text","text":"a","TEXT":"b","text":"c"}],"structuredContent":{"k E":["d",1,true,null]},"n":2.5}`)
	got, err := Strings(raw)
	want := []string{"content", "type", "text", "text", "a", "TEXT", "b", "text", "c", "structuredContent", "k E", "d", "n"}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("Strings = %q, %v; want %q", got, err, want)
	}
	if _, err := Strings(json.RawMessage(`{"a":`)); err == nil {
		t.Fatal("truncated JSON scanned without an error")
	}
}
