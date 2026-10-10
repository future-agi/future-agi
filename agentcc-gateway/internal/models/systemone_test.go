package models

import (
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"testing"
)

func TestSystemOneValidate(t *testing.T) {
	choices := func(n int) string {
		m := make(map[string]any, n)
		for i := 0; i < n; i++ {
			m[fmt.Sprint(i)] = nil
		}
		b, _ := json.Marshal(m)
		return string(b)
	}
	levels := func(n int) string { b, _ := json.Marshal(make([]string, n)); return string(b) }
	tests := []struct{ name, model, state, question, code, reason string }{
		{"noul", "jev-latest", `"private-state"`, `{"type":"noul","instructions":"judge","future":{"x":1}}`, "", ""},
		{"choice", "jev-latest", `{}`, `{"type":"choice","instructions":{"task":"judge"},"criteria":{"yes":null}}`, "", ""},
		{"score", "jev-latest", `[]`, `{"type":"score","instructions":["judge"],"criteria":["bad","good"]}`, "", ""},
		{"choice255", "jev-latest", `{}`, `{"type":"choice","instructions":"judge","criteria":` + choices(255) + `}`, "", ""},
		{"score10", "jev-latest", `{}`, `{"type":"score","instructions":"judge","criteria":` + levels(10) + `}`, "", ""},
		{"model", "", `{}`, `{}`, "missing_model", ""},
		{"state", "jev-latest", "", `{}`, "missing_state", ""},
		{"null_state", "jev-latest", ` null `, `{}`, "missing_state", ""},
		{"questions", "jev-latest", `"private-state"`, "", "missing_questions", ""},
		{"not_object", "jev-latest", `"private-state"`, `[]`, "invalid_question", "object"},
		{"null_question", "jev-latest", `"private-state"`, `null`, "invalid_question", "object"},
		{"unknown_type", "jev-latest", `"private-state"`, `{"type":"private-instructions","instructions":"judge"}`, "invalid_question", "type"},
		{"missing_instructions", "jev-latest", `"private-state"`, `{"type":"noul"}`, "invalid_question", "instructions"},
		{"empty_instructions", "jev-latest", `"private-state"`, `{"type":"noul","instructions":"  "}`, "invalid_question", "instructions"},
		{"null_instructions", "jev-latest", `"private-state"`, `{"type":"noul","instructions":null}`, "invalid_question", "instructions"},
		{"empty_object_instructions", "jev-latest", `{}`, `{"type":"noul","instructions":{}}`, "invalid_question", "instructions"},
		{"empty_array_instructions", "jev-latest", `{}`, `{"type":"noul","instructions":[]}`, "invalid_question", "instructions"},
		{"invalid_instructions", "jev-latest", `{}`, `{"type":"noul","instructions":42}`, "invalid_question", "instructions"},
		{"choice_empty", "jev-latest", `{}`, `{"type":"choice","instructions":"private-instructions","criteria":{}}`, "invalid_question", "criteria"},
		{"choice_array", "jev-latest", `{}`, `{"type":"choice","instructions":"judge","criteria":[1,2]}`, "invalid_question", "criteria"},
		{"choice256", "jev-latest", `{}`, `{"type":"choice","instructions":"judge","criteria":` + choices(256) + `}`, "invalid_question", "criteria"},
		{"score_one", "jev-latest", `{}`, `{"type":"score","instructions":"judge","criteria":["bad"]}`, "invalid_question", "criteria"},
		{"score11", "jev-latest", `{}`, `{"type":"score","instructions":"judge","criteria":` + levels(11) + `}`, "invalid_question", "criteria"},
		{"score_object", "jev-latest", `{}`, `{"type":"score","instructions":"judge","criteria":{"0":"bad","1":"good"}}`, "invalid_question", "criteria"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := SystemOneRequest{Model: tt.model, State: json.RawMessage(tt.state)}
			if tt.question != "" {
				req.Questions = map[string]json.RawMessage{"q1": json.RawMessage(tt.question)}
			}
			err := req.Validate()
			if tt.code == "" {
				if err != nil {
					t.Fatal(err)
				}
				return
			}
			if err == nil || err.Code != tt.code || err.Status != http.StatusBadRequest {
				t.Fatalf("error = %v, want 400 %s", err, tt.code)
			}
			if tt.reason != "" && (!strings.Contains(err.Message, "q1") || !strings.Contains(err.Message, tt.reason)) {
				t.Fatalf("missing question id or reason: %v", err)
			}
			for _, secret := range []string{"private-state", "private-instructions"} {
				if strings.Contains(err.Error(), secret) {
					t.Fatal("error leaked content")
				}
			}
		})
	}
}

func TestSystemOneJSONRoundTrip(t *testing.T) {
	for _, fixture := range []string{
		`{"model":"jev-latest","state":{"private":"text"},"questions":{"q":{"type":"noul","instructions":"judge","unknown":{"nested":[1,true,null]}}}}`,
		`{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.9,"unknown":[1,2]}},"usage":{"input_tokens":7,"output_tokens":3}}`,
	} {
		var value any
		if strings.Contains(fixture, `"questions"`) {
			value = &SystemOneRequest{}
		} else {
			value = &SystemOneResponse{}
		}
		if err := json.Unmarshal([]byte(fixture), value); err != nil {
			t.Fatal(err)
		}
		got, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		var a, b any
		json.Unmarshal([]byte(fixture), &a)
		json.Unmarshal(got, &b)
		if !reflect.DeepEqual(a, b) {
			t.Fatalf("roundtrip changed JSON: %s", got)
		}
	}
}

func TestSystemOneRequestContextRelease(t *testing.T) {
	rc := AcquireRequestContext()
	rc.EndpointType = "systemone"
	rc.SystemOneRequest = &SystemOneRequest{}
	rc.SystemOneResponse = &SystemOneResponse{}
	rc.Release()
	if rc.SystemOneRequest != nil || rc.SystemOneResponse != nil || rc.EndpointType != "" {
		t.Fatal("System One state retained after Release")
	}
}
