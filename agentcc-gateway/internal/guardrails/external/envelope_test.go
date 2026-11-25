package external

import "testing"

func TestProviderExecutionEnvelopes(t *testing.T) {
	adapters := []struct {
		name string
		cfg  map[string]interface{}
	}{
		{"lakera", nil}, {"azure_content_safety", nil}, {"presidio", nil}, {"llama_guard", nil}, {"bedrock_guardrails", nil}, {"hiddenlayer", nil}, {"dynamoai", nil}, {"enkrypt", nil}, {"ibm_ai", nil}, {"pangea", nil}, {"zscaler", nil}, {"crowdstrike", nil}, {"aporia", nil}, {"lasso", nil}, {"grayswan", nil},
	}
	for _, tc := range adapters {
		for _, body := range []string{`{`, `null`, `{}`, `[]`} {
			if tc.name == "presidio" && body == `[]` {
				continue
			} // Empty findings are a complete valid Presidio verdict.
			t.Run(tc.name+"/"+body, func(t *testing.T) {
				cfg := map[string]interface{}{"provider": tc.name}
				for k, v := range tc.cfg {
					cfg[k] = v
				}
				result := New("check", cfg).adapter.parseResponse([]byte(body))
				if result.Err == nil || result.Pass {
					t.Fatalf("invalid envelope became content: %+v", result)
				}
			})
		}
	}
}

func TestProviderCompletenessAndLegacyVerdicts(t *testing.T) {
	cases := []struct {
		name, provider, body string
		unavailable, pass    bool
		cfg                  map[string]interface{}
	}{
		{"azure complete safe", "azure_content_safety", `{"categoriesAnalysis":[{"category":"Hate","severity":0},{"category":"SelfHarm","severity":0},{"category":"Sexual","severity":0},{"category":"Violence","severity":0}]}`, false, true, nil},
		{"azure partial", "azure_content_safety", `{"categoriesAnalysis":[{"category":"Hate","severity":0}]}`, true, false, nil},
		{"azure selected", "azure_content_safety", `{"categoriesAnalysis":[{"category":"Hate","severity":0}]}`, false, true, map[string]interface{}{"categories": []string{"Hate"}}},
		{"azure missing severity", "azure_content_safety", `{"categoriesAnalysis":[{"category":"Hate"}]}`, true, false, map[string]interface{}{"categories": []string{"Hate"}}},
		{"enkrypt complete safe", "enkrypt", `{"summary":{"toxicity":0,"injection_attack":false}}`, false, true, nil},
		{"enkrypt omitted detector not triggered", "enkrypt", `{"summary":{"toxicity":0}}`, false, true, nil},
		{"enkrypt only triggered detector", "enkrypt", `{"summary":{"toxicity":["toxicity","insult"]}}`, false, false, nil},
		{"enkrypt only triggered detector score", "enkrypt", `{"summary":{"injection_attack":1}}`, false, false, map[string]interface{}{"checks": []interface{}{"toxicity", "injection_attack"}}},
		{"enkrypt missing summary", "enkrypt", `{"details":{}}`, true, false, nil},
		{"enkrypt summary not an object", "enkrypt", `{"summary":["toxicity"]}`, true, false, nil},
		{"enkrypt bad detector verdict", "enkrypt", `{"summary":{"toxicity":"yes"}}`, true, false, nil},
		{"enkrypt empty", "enkrypt", `{"summary":{}}`, true, false, nil},
		{"enkrypt bad score", "enkrypt", `{"summary":{"toxicity":-1,"injection_attack":false}}`, true, false, nil},
		{"presidio empty safe", "presidio", `[]`, false, true, nil},
		{"presidio bad score", "presidio", `[{"entity_type":"EMAIL","score":-1}]`, true, false, nil},
		{"lakera legacy safe", "lakera", `{"flagged":false}`, false, true, nil},
		{"lakera bad score", "lakera", `{"flagged":false,"category_scores":{"toxicity":2}}`, true, false, nil},
		{"llama unrecognized", "llama_guard", `{"choices":[{"message":{"content":"safety unknown"}}]}`, true, false, nil},
		{"ibm unrecognized", "ibm_ai", `{"choices":[{"message":{"content":"perhaps"}}]}`, true, false, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := map[string]interface{}{"provider": tc.provider}
			for k, v := range tc.cfg {
				cfg[k] = v
			}
			result := New("check", cfg).adapter.parseResponse([]byte(tc.body))
			if (result.Err != nil) != tc.unavailable || result.Pass != tc.pass {
				t.Fatalf("result=%+v want unavailable=%v pass=%v", result, tc.unavailable, tc.pass)
			}
		})
	}
}
