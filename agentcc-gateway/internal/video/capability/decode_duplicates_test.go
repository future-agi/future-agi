package capability

import "testing"

// R3-F1: the typed decoder merges repeated structs (and reuses slice elements),
// while a map-based key check only sees the last value. No occurrence may hide
// an earlier alias, even when the final container is empty or null.
func TestDecodeRejectsRepeatedContainers(t *testing.T) {
	for _, tc := range []struct{ name, fields, param string }{
		{"source object", `"inputs":[{"role":"first_frame","media_type":"image/png","source":{"URL":"https://example.com/a.png"},"source":{}}]`, "inputs.0.source"},
		{"source null", `"inputs":[{"role":"first_frame","media_type":"image/png","source":{"URL":"https://example.com/a.png"},"source":null}]`, "inputs.0.source"},
		{"inputs array", `"inputs":[{"Role":"first_frame","media_type":"image/png","source":{"url":"https://example.com/a.png"}}],"inputs":[{}]`, "inputs"},
		{"inputs empty", `"inputs":[{"Role":"first_frame"}],"inputs":[]`, "inputs"},
		{"inputs null", `"inputs":[{"Role":"first_frame"}],"inputs":null`, "inputs"},
		{"nested escaped key", `"inputs":[{"source":{"URL":"https://example.com/a.png"},"source":{}}]`, "inputs.0.source"},
		{"canonical source", `"inputs":[{"source":{"url":"https://example.com/a.png","url":null}}]`, "inputs.0.source.url"},
		{"second array element", `"inputs":[{}, {"source":{"url":"https://example.com/a.png","url":null}}]`, "inputs.1.source.url"},
		{"metadata", `"metadata":{"Trace":"a","Trace":"b"}`, "metadata.Trace"},
		{"provider option object in array", `"provider_options":{"custom":[{"nested":{"value":1,"value":null}}]}`, "provider_options.custom.0.nested.value"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := Decode([]byte(`{` + strictModel + `,` + tc.fields + `}`))
			if err == nil {
				t.Fatal("repeated container/key accepted")
			}
			if code, param := apiCode(t, err); code != "invalid_parameter" || param != tc.param {
				t.Fatalf("got %s/%s, want invalid_parameter/%s", code, param, tc.param)
			}
		})
	}
}

// A positive value followed by null otherwise leaves the positive Go numeric
// field intact. Reject repeats in either order instead of losing explicit null
// or zero occurrences to the decoder's overwrite/merge semantics.
func TestDecodeRejectsRepeatedNumericFields(t *testing.T) {
	for _, key := range []string{"duration_seconds", "fps", "n"} {
		for _, values := range [][2]string{{"1", "null"}, {"null", "1"}, {"1", "0"}, {"0", "1"}, {"1", "1"}} {
			t.Run(key+"/"+values[0]+"_then_"+values[1], func(t *testing.T) {
				body := `{` + strictModel + `,"` + key + `":` + values[0] + `,"` + key + `":` + values[1] + `}`
				_, err := Decode([]byte(body))
				if err == nil {
					t.Fatal("repeated numeric field accepted")
				}
				if code, param := apiCode(t, err); code != "invalid_parameter" || param != key {
					t.Fatalf("got %s/%s", code, param)
				}
			})
		}
	}
}

func TestDecodeDistinctObjectScopesRemainValid(t *testing.T) {
	body := `{` + strictModel + `,"metadata":{"Trace":"a","trace":"b"},"inputs":[{"role":"first_frame","source":{"url":"https://example.com/a.png"}},{"role":"last_frame","source":{"url":"https://example.com/b.png"}}]}`
	req, err := Decode([]byte(body))
	if err != nil {
		t.Fatal(err)
	}
	if len(req.Inputs) != 2 || req.Inputs[1].Source.URL != "https://example.com/b.png" || req.Metadata["Trace"] != "a" || req.Metadata["trace"] != "b" {
		t.Fatalf("distinct object keys changed: %+v", req)
	}
}

func TestOpenAIJSONRepeatedKeysRemainRejected(t *testing.T) {
	for _, tc := range []struct{ fields, param string }{
		{`"seconds":4,"seconds":null`, "seconds"},
		{`"input_reference":{"image_url":"https://example.com/a.png","image_url":"https://example.com/b.png"}`, "input_reference.image_url"},
		{`"input_reference":{"image_url":"https://example.com/a.png"},"input_reference":{}`, "input_reference"},
	} {
		_, err := DecodeOpenAIJSON([]byte(`{` + strictModel + `,` + tc.fields + `}`))
		if err == nil {
			t.Fatal("repeated key accepted")
		}
		if code, param := apiCode(t, err); code != "invalid_parameter" || param != tc.param {
			t.Fatalf("got %s/%s, want invalid_parameter/%s", code, param, tc.param)
		}
	}
}
