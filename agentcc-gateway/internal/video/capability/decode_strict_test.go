package capability

import (
	"errors"
	"strings"
	"testing"
)

const strictModel = `"model":"openrouter/bytedance/seedance-1-5-pro","prompt":"synthetic paper boat"`

func apiCode(t *testing.T, err error) (string, string) {
	t.Helper()
	var e *ValidationError
	if !errors.As(err, &e) {
		t.Fatalf("want *ValidationError, got %T %v", err, err)
	}
	return e.Code, e.Param
}

// TH8088-R1: encoding/json matches struct keys case-insensitively, so an alias
// used to slip past the exact-key explicit zero/null check and Normalize then
// replaced it with the model default, submitting a paid job.
func TestDecodeRejectsCaseAliasedZeroAndNull(t *testing.T) {
	for _, key := range []string{"DURATION_SECONDS", "Duration_Seconds", "duration_SECONDS", "FPS", "Fps", "N"} {
		for _, value := range []string{"0", "null"} {
			body := `{` + strictModel + `,"` + key + `":` + value + `}`
			_, err := Decode([]byte(body))
			if err == nil {
				t.Fatalf("%s accepted", body)
			}
			code, param := apiCode(t, err)
			if code != "unknown_field" || param != key {
				t.Fatalf("%s: got %s/%s", body, code, param)
			}
		}
	}
}

func TestDecodeRejectsCanonicalExplicitNull(t *testing.T) {
	for _, key := range []string{"duration_seconds", "fps", "n"} {
		for _, value := range []string{"0", "null"} {
			body := `{` + strictModel + `,"` + key + `":` + value + `}`
			_, err := Decode([]byte(body))
			if err == nil {
				t.Fatalf("%s accepted", body)
			}
			if code, param := apiCode(t, err); code != "invalid_parameter" || param != key {
				t.Fatalf("%s: got %s/%s", body, code, param)
			}
		}
	}
}

// Every key that reaches the struct must use its canonical spelling, including
// non-zero aliases, Unicode case folds and nested input fields.
func TestDecodeRejectsEveryCaseAlias(t *testing.T) {
	for _, tc := range []struct{ body, param string }{
		{`{` + strictModel + `,"DURATION_SECONDS":5}`, "DURATION_SECONDS"},
		{`{"MODEL":"openrouter/bytedance/seedance-1-5-pro","prompt":"x"}`, "MODEL"},
		{`{"model":"openrouter/bytedance/seedance-1-5-pro","Prompt":"x"}`, "Prompt"},
		{`{` + strictModel + `,"duration_ſeconds":0}`, "duration_ſeconds"}, // U+017F folds to s
		{`{` + strictModel + `,"inputs":[{"Role":"first_frame","media_type":"image/png","source":{"url":"https://example.com/a.png"}}]}`, "Role"},
		{`{` + strictModel + `,"inputs":[{"role":"first_frame","media_type":"image/png","SOURCE":{"url":"https://example.com/a.png"}}]}`, "SOURCE"},
		{`{` + strictModel + `,"inputs":[{"role":"first_frame","media_type":"image/png","source":{"URL":"https://example.com/a.png"}}]}`, "URL"},
	} {
		_, err := Decode([]byte(tc.body))
		if err == nil {
			t.Fatalf("%s accepted", tc.body)
		}
		if code, param := apiCode(t, err); code != "unknown_field" || param != tc.param {
			t.Fatalf("%s: got %s/%s", tc.body, code, param)
		}
	}
}

// Positive controls: omitted fields still take model defaults, canonical values
// decode unchanged, and map-valued fields keep caller keys verbatim.
func TestDecodeCanonicalAndOmittedFieldsStillAccepted(t *testing.T) {
	req, err := Decode([]byte(`{` + strictModel + `}`))
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := NewRegistry().Normalize(req, "")
	if err != nil {
		t.Fatal(err)
	}
	if resolved.Request.DurationSeconds == 0 || resolved.Request.N != 1 {
		t.Fatalf("defaults not applied: %+v", resolved.Request)
	}
	req, err = Decode([]byte(`{` + strictModel + `,"duration_seconds":5,"n":1,"metadata":{"Trace":"X"},"inputs":[{"role":"first_frame","media_type":"image/png","source":{"url":"https://example.com/a.png"}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if req.DurationSeconds != 5 || req.N != 1 || req.Metadata["Trace"] != "X" || req.Inputs[0].Source.URL != "https://example.com/a.png" {
		t.Fatalf("canonical body changed: %+v", req)
	}
}

// The historical invalid-UTF-8 probe had no valid model, so its 400 proved
// nothing about UTF-8. These pair a valid model with invalid bytes on every
// create decoder, against a valid-UTF-8 control.
func TestInvalidUTF8RefusedWithValidModel(t *testing.T) {
	valid := `{"model":"openrouter/bytedance/seedance-1-5-pro","prompt":"synthetic paper boat"}`
	if _, err := Decode([]byte(valid)); err != nil {
		t.Fatal("control rejected", err)
	}
	bad := `{"model":"openrouter/bytedance/seedance-1-5-pro","prompt":"synthetic ` + "\xff" + ` boat"}`
	if _, err := Decode([]byte(bad)); err == nil {
		t.Fatal("native: invalid UTF-8 accepted")
	} else if code, param := apiCode(t, err); code != "invalid_request" || param != "body" {
		t.Fatal(code, param)
	}

	openAIValid := `{"model":"openrouter/bytedance/seedance-1-5-pro","prompt":"synthetic paper boat","seconds":"4","size":"854x480"}`
	if _, err := DecodeOpenAIJSON([]byte(openAIValid)); err != nil {
		t.Fatal("openai control rejected", err)
	}
	openAIBad := strings.Replace(openAIValid, "paper", "pa\xffper", 1)
	if _, err := DecodeOpenAIJSON([]byte(openAIBad)); err == nil {
		t.Fatal("openai JSON: invalid UTF-8 accepted")
	} else if code, _ := apiCode(t, err); code != "invalid_request" {
		t.Fatal(code)
	}

	fields := map[string]string{"model": "openrouter/bytedance/seedance-1-5-pro", "prompt": "synthetic paper boat", "seconds": "4"}
	body, ct := sdkMultipart(t, fields, nil, "")
	if _, err := DecodeOpenAIMultipart(body, ct); err != nil {
		t.Fatal("multipart control rejected", err)
	}
	fields["prompt"] = "synthetic \xff boat"
	body, ct = sdkMultipart(t, fields, nil, "")
	if _, err := DecodeOpenAIMultipart(body, ct); err == nil {
		t.Fatal("multipart: invalid UTF-8 prompt accepted")
	} else if code, param := apiCode(t, err); code != "invalid_parameter" || param != "prompt" {
		t.Fatal(code, param)
	}
	// Uploaded image bytes are binary and remain allowed.
	fields["prompt"] = "synthetic paper boat"
	body, ct = sdkMultipart(t, fields, pngBytes, "image/png")
	if _, err := DecodeOpenAIMultipart(body, ct); err != nil {
		t.Fatal("binary upload rejected", err)
	}
}
