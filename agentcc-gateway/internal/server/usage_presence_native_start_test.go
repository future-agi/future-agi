package server

import "testing"

func TestUsagePresenceNativeStartEvidence(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct{ name, start, delta, oracle string }{
			{"missing-delta", `{"input_tokens":1000}`, "", `{"prompt_tokens":1000}`},
			{"empty-delta", `{"input_tokens":1000}`, `{}`, `{"prompt_tokens":1000}`},
			{"null-delta", `{"input_tokens":1000}`, `null`, `{"prompt_tokens":1000}`},
			{"null-input-delta", `{"input_tokens":1000}`, `{"input_tokens":null,"output_tokens":5}`, `{"prompt_tokens":1000,"completion_tokens":5}`},
			{"max-input", `{"input_tokens":1000}`, `{"input_tokens":500,"output_tokens":5}`, `{"prompt_tokens":1000,"completion_tokens":5}`},
			{"zero-input", `{"input_tokens":0}`, `{"output_tokens":5}`, `{"prompt_tokens":0,"completion_tokens":5}`},
		} {
			for _, a := range []upInt2Adapter{upInt2Adapters[2], upInt2Adapters[5]} {
				t.Run(upInt2SurfaceName(surface)+"/"+tc.name+"/"+a.name, func(t *testing.T) {
					oracle := upInt2Run(t, upInt2Adapters[0], surface, true, "", upInt2OpenAISSE(tc.oracle))
					upInt2Served(t, "oracle", oracle)
					got := upInt2Run(t, a, surface, true, "", presenceStreamWithStart(a, tc.start, tc.delta))
					upInt2Compare(t, got, oracle)
				})
			}
		}
	}
}
