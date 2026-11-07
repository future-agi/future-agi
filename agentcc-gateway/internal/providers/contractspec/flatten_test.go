package contractspec

import (
	"reflect"
	"strings"
	"testing"
)

// extractOne flattens one root of an inline spec.
func extractOne(t *testing.T, format, spec string, r RootDef) *Subset {
	t.Helper()
	src := Source{Provider: "acme", Format: format, Roots: []RootDef{r}}
	subsets, _, err := Extract(src, []byte(spec))
	if err != nil {
		t.Fatalf("Extract: %v", err)
	}
	return subsets[0]
}

func wantPath(t *testing.T, s *Subset, path string, want PathInfo) {
	t.Helper()
	got := s.Paths[path]
	if got == nil {
		t.Fatalf("%s: path %s missing; have %s", s.ID(), path, strings.Join(s.SortedPaths(), " "))
	}
	if !reflect.DeepEqual(*got, want) {
		t.Errorf("%s %s = %+v, want %+v", s.ID(), path, *got, want)
	}
}

func wantNoPath(t *testing.T, s *Subset, path string) {
	t.Helper()
	if _, ok := s.Paths[path]; ok {
		t.Errorf("%s: unexpected path %s", s.ID(), path)
	}
}

// openAPISpec exercises the constructs the walker must handle. It is YAML,
// so the YAML path (bare 200 keys) is covered too.
const openAPISpec = `
openapi: 3.1.0
info: {title: acme, version: "1.0"}
paths:
  /v1/chat:
    post:
      responses:
        200:
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Message"}
            text/event-stream:
              schema: {$ref: "#/components/schemas/Event"}
        400:
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Error"}
        default:
          $ref: "#/components/responses/Generic"
components:
  responses:
    Generic:
      content:
        application/json:
          schema:
            type: object
            properties:
              error: {type: object, properties: {code: {type: integer}}}
  schemas:
    Message:
      type: object
      properties:
        id: {type: string}
        model:
          anyOf:
            - {type: string}
            - {type: string, enum: [m-1, m-2]}
        stop_reason:
          anyOf:
            - {$ref: "#/components/schemas/StopReason"}
            - {type: "null"}
        content:
          type: array
          items: {$ref: "#/components/schemas/Block"}
        usage:
          allOf:
            - {$ref: "#/components/schemas/Usage"}
          deprecated: true
        meta:
          type: object
          additionalProperties: {type: integer}
        extra: {type: object, additionalProperties: true}
        free: {}
        secret: {type: string, writeOnly: true}
        legacy: {type: [string, "null"]}
        old: {type: string, nullable: true}
        parent: {$ref: "#/components/schemas/Node"}
        remote: {$ref: "https://example.com/x.json"}
    StopReason: {type: string, enum: [end_turn, max_tokens, null]}
    Usage:
      type: object
      properties:
        input_tokens: {type: integer}
    Block:
      oneOf:
        - {$ref: "#/components/schemas/TextBlock"}
        - {$ref: "#/components/schemas/ToolBlock"}
      discriminator:
        propertyName: type
        mapping:
          text: "#/components/schemas/TextBlock"
          tool_use: ToolBlock
    TextBlock:
      type: object
      properties:
        type: {type: string, const: text}
        text: {type: string}
    ToolBlock:
      type: object
      properties:
        type: {type: string, const: tool_use}
        input: {type: object}
    Node:
      type: object
      properties:
        child: {$ref: "#/components/schemas/Node"}
    Event:
      oneOf:
        - {$ref: "#/components/schemas/StartEvent"}
        - {$ref: "#/components/schemas/StopEvent"}
      discriminator:
        propertyName: type
        mapping:
          start: "#/components/schemas/StartEvent"
          stop: "#/components/schemas/StopEvent"
          ghost: "#/components/schemas/GhostEvent"
    StartEvent:
      type: object
      properties:
        type: {const: start}
        message: {$ref: "#/components/schemas/Message"}
    StopEvent:
      type: object
      properties:
        type: {const: stop}
    GhostEvent:
      type: object
      properties:
        type: {const: ghost}
        boo: {type: boolean}
    Error:
      type: object
      properties:
        error:
          type: object
          properties:
            type: {type: string, enum: [invalid, overloaded]}
            message: {type: string}
`

func TestOpenAPIResponse(t *testing.T) {
	s := extractOne(t, FormatOpenAPI, openAPISpec, RootDef{Name: "chat", Op: "POST /v1/chat"})
	if s.Select != "POST /v1/chat -> 200 application/json" {
		t.Errorf("Select = %q", s.Select)
	}
	wantPath(t, s, "$", PathInfo{Kinds: []string{"object"}})
	wantPath(t, s, "$.model", PathInfo{Kinds: []string{"string"}, Enum: []string{"m-1", "m-2"}, OpenEnum: true})
	wantPath(t, s, "$.stop_reason", PathInfo{Kinds: []string{"string"}, Enum: []string{"end_turn", "max_tokens"}, Nullable: true})
	wantPath(t, s, "$.content[]", PathInfo{Kinds: []string{"object"}})
	wantPath(t, s, "$.content[].type", PathInfo{Kinds: []string{"string"}, Enum: []string{"text", "tool_use"}, Variants: []string{"type=text", "type=tool_use"}})
	wantPath(t, s, "$.content[].text", PathInfo{Kinds: []string{"string"}, Variants: []string{"type=text"}})
	wantPath(t, s, "$.content[].input", PathInfo{Kinds: []string{"object"}, Opaque: true, Variants: []string{"type=tool_use"}})
	wantPath(t, s, "$.usage", PathInfo{Kinds: []string{"object"}, Deprecated: true})
	wantPath(t, s, "$.usage.input_tokens", PathInfo{Kinds: []string{"integer"}})
	wantPath(t, s, "$.meta", PathInfo{Kinds: []string{"map", "object"}})
	wantPath(t, s, "$.meta.*", PathInfo{Kinds: []string{"integer"}})
	wantPath(t, s, "$.extra", PathInfo{Kinds: []string{"map", "object"}, Opaque: true})
	wantPath(t, s, "$.free", PathInfo{Kinds: []string{"any"}, Opaque: true})
	wantNoPath(t, s, "$.secret")
	wantPath(t, s, "$.legacy", PathInfo{Kinds: []string{"string"}, Nullable: true})
	wantPath(t, s, "$.old", PathInfo{Kinds: []string{"string"}, Nullable: true})
	wantPath(t, s, "$.parent.child", PathInfo{Recursive: "Node"})
	wantPath(t, s, "$.remote", PathInfo{Opaque: true, Recursive: "unresolved https://example.com/x.json"})
}

func TestOpenAPIEventsAndErrors(t *testing.T) {
	ev := extractOne(t, FormatOpenAPI, openAPISpec, RootDef{Name: "stream", Op: "POST /v1/chat", Content: "text/event-stream", Events: true,
		Elide: map[string]string{"#/components/schemas/Message": "acme/chat $"}})
	if want := []string{"ghost", "start", "stop"}; !reflect.DeepEqual(ev.Events, want) {
		t.Errorf("Events = %v, want %v (the mapping's ghost too)", ev.Events, want)
	}
	wantPath(t, ev, "ghost:$.boo", PathInfo{Kinds: []string{"boolean"}})
	wantPath(t, ev, "start:$.message", PathInfo{Kinds: []string{"object"}, SameAs: "acme/chat $"})
	wantNoPath(t, ev, "start:$.message.id")

	errs := extractOne(t, FormatOpenAPI, openAPISpec, RootDef{Name: "errors", Op: "POST /v1/chat", Status: "errors"})
	if errs.Select != "POST /v1/chat -> 400,default application/json" {
		t.Errorf("Select = %q", errs.Select)
	}
	wantPath(t, errs, "$.error.type", PathInfo{Kinds: []string{"string"}, Enum: []string{"invalid", "overloaded"}})
	wantPath(t, errs, "$.error.code", PathInfo{Kinds: []string{"integer"}})

	chunk := extractOne(t, FormatOpenAPI, openAPISpec, RootDef{Name: "chunk", Schema: "#/components/schemas/Message", Prefix: "chunk"})
	wantPath(t, chunk, "chunk:$.id", PathInfo{Kinds: []string{"string"}})
	wantNoPath(t, chunk, "$.id")

	pruned := extractOne(t, FormatOpenAPI, openAPISpec, RootDef{Name: "p", Schema: "#/components/schemas/Message", Prune: map[string]string{"$.content": "echo", "$.stop_reason": "echo"}})
	wantPath(t, pruned, "$.content", PathInfo{Kinds: []string{"array"}, Pruned: "echo"})
	wantPath(t, pruned, "$.stop_reason", PathInfo{Kinds: []string{"string"}, Nullable: true, Pruned: "echo"})
	wantNoPath(t, pruned, "$.content[]")

	rebased := extractOne(t, FormatOpenAPI, openAPISpec, RootDef{Name: "inner", Schema: "#/components/schemas/Message", Rebase: "$.usage"})
	wantPath(t, rebased, "$.input_tokens", PathInfo{Kinds: []string{"integer"}})
	wantNoPath(t, rebased, "$.id")
}

func TestOpenAPIRootErrors(t *testing.T) {
	src := func(r RootDef) Source { return Source{Provider: "acme", Format: FormatOpenAPI, Roots: []RootDef{r}} }
	for _, tc := range []struct {
		name string
		root RootDef
		want string
	}{
		{"missing op", RootDef{Name: "x", Op: "GET /nope"}, "not in spec"},
		{"bad op", RootDef{Name: "x", Op: "nope"}, "METHOD /path"},
		{"missing status", RootDef{Name: "x", Op: "POST /v1/chat", Status: "201"}, "no 201 response"},
		{"missing content", RootDef{Name: "x", Op: "POST /v1/chat", Content: "text/plain"}, "no text/plain content"},
		{"missing schema", RootDef{Name: "x", Schema: "#/components/schemas/Nope"}, "no \"Nope\""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := Extract(src(tc.root), []byte(openAPISpec))
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("err = %v, want it to mention %q", err, tc.want)
			}
		})
	}
}

const discoverySpec = `{
  "kind": "discovery#restDescription", "id": "acme:v1", "version": "v1", "revision": "20261028",
  "resources": {"models": {"methods": {"generate": {"response": {"$ref": "GenerateResponse"}}}}},
  "schemas": {
    "GenerateResponse": {"id": "GenerateResponse", "type": "object", "properties": {
      "candidates": {"type": "array", "items": {"$ref": "Candidate"}},
      "tokens": {"type": "string", "format": "int64"},
      "labels": {"type": "object", "additionalProperties": {"type": "string"}},
      "struct": {"type": "object"},
      "value": {"type": "any"},
      "tree": {"$ref": "Tree"}
    }},
    "Candidate": {"id": "Candidate", "type": "object", "properties": {
      "finishReason": {"type": "string", "enum": ["STOP", "MAX_TOKENS"], "enumDescriptions": ["", ""]},
      "old": {"type": "boolean", "deprecated": true}
    }},
    "Tree": {"id": "Tree", "type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "Tree"}}}}
  }
}`

func TestDiscovery(t *testing.T) {
	s := extractOne(t, FormatDiscovery, discoverySpec, RootDef{Name: "gen", Method: "models.generate"})
	if s.Select != "method models.generate -> GenerateResponse" {
		t.Errorf("Select = %q", s.Select)
	}
	wantPath(t, s, "$.candidates[].finishReason", PathInfo{Kinds: []string{"string"}, Enum: []string{"MAX_TOKENS", "STOP"}})
	wantPath(t, s, "$.candidates[].old", PathInfo{Kinds: []string{"boolean"}, Deprecated: true})
	wantPath(t, s, "$.tokens", PathInfo{Kinds: []string{"string(int64)"}})
	wantPath(t, s, "$.labels", PathInfo{Kinds: []string{"map", "object"}})
	wantPath(t, s, "$.labels.*", PathInfo{Kinds: []string{"string"}})
	wantPath(t, s, "$.struct", PathInfo{Kinds: []string{"object"}, Opaque: true})
	wantPath(t, s, "$.value", PathInfo{Kinds: []string{"any"}, Opaque: true})
	wantPath(t, s, "$.tree.kids[]", PathInfo{Recursive: "Tree"})

	byID := extractOne(t, FormatDiscovery, discoverySpec, RootDef{Name: "c", Schema: "Candidate"})
	wantPath(t, byID, "$.finishReason", PathInfo{Kinds: []string{"string"}, Enum: []string{"MAX_TOKENS", "STOP"}})

	_, meta, err := Extract(Source{Provider: "acme", Format: FormatDiscovery, Roots: []RootDef{{Name: "c", Schema: "Candidate"}}}, []byte(discoverySpec))
	if err != nil || meta["revision"] != "20261028" || meta["id"] != "acme:v1" {
		t.Errorf("meta = %v, %v", meta, err)
	}
	if _, _, err := Extract(Source{Provider: "acme", Format: FormatDiscovery, Roots: []RootDef{{Name: "x", Method: "models.nope"}}}, []byte(discoverySpec)); err == nil {
		t.Error("an unknown method was accepted")
	}
}

const smithySpec = `{
  "smithy": "2.0",
  "shapes": {
    "acme#Service": {"type": "service", "version": "2023-09-30"},
    "acme#Converse": {"type": "operation", "output": {"target": "acme#ConverseOutput"},
      "errors": [{"target": "acme#ThrottlingException"}, {"target": "acme#ValidationException"}]},
    "acme#ConverseOutput": {"type": "structure", "members": {
      "output": {"target": "acme#Output"},
      "stopReason": {"target": "acme#StopReason"},
      "requestId": {"target": "smithy.api#String", "traits": {"smithy.api#httpHeader": "x-amzn-requestid"}},
      "fields": {"target": "smithy.api#Document"},
      "metrics": {"target": "acme#Metrics", "traits": {"smithy.api#jsonName": "latency"}}
    }},
    "acme#Output": {"type": "union", "members": {"message": {"target": "acme#Message"}}},
    "acme#Message": {"type": "structure", "members": {"content": {"target": "acme#Blocks"}}},
    "acme#Blocks": {"type": "list", "member": {"target": "acme#Block"}},
    "acme#Block": {"type": "union", "members": {"text": {"target": "smithy.api#String"}, "image": {"target": "acme#Image"}}},
    "acme#Image": {"type": "structure", "members": {"bytes": {"target": "smithy.api#Blob"}, "tags": {"target": "acme#Tags"}}},
    "acme#Tags": {"type": "map", "key": {"target": "smithy.api#String"}, "value": {"target": "smithy.api#Integer"}},
    "acme#StopReason": {"type": "enum", "members": {
      "END": {"target": "smithy.api#Unit", "traits": {"smithy.api#enumValue": "end_turn"}},
      "MAX": {"target": "smithy.api#Unit", "traits": {"smithy.api#enumValue": "max_tokens"}}
    }},
    "acme#Metrics": {"type": "structure", "members": {"ms": {"target": "smithy.api#Long"}}},
    "acme#ConverseStream": {"type": "operation", "output": {"target": "acme#ConverseStreamOutput"}},
    "acme#ConverseStreamOutput": {"type": "structure", "members": {
      "stream": {"target": "acme#Events", "traits": {"smithy.api#httpPayload": {}}}
    }},
    "acme#Events": {"type": "union", "traits": {"smithy.api#streaming": {}}, "members": {
      "messageStop": {"target": "acme#MessageStop"},
      "throttlingException": {"target": "acme#ThrottlingException"}
    }},
    "acme#MessageStop": {"type": "structure", "members": {"stopReason": {"target": "acme#StopReason"}}},
    "acme#ThrottlingException": {"type": "structure", "traits": {"smithy.api#error": "client"}, "members": {"message": {"target": "smithy.api#String"}}},
    "acme#ValidationException": {"type": "structure", "traits": {"smithy.api#error": "client"}, "members": {"message": {"target": "smithy.api#String"}}}
  }
}`

func TestSmithy(t *testing.T) {
	s := extractOne(t, FormatSmithy, smithySpec, RootDef{Name: "converse", Operation: "acme#Converse"})
	wantPath(t, s, "$.stopReason", PathInfo{Kinds: []string{"string"}, Enum: []string{"end_turn", "max_tokens"}})
	wantNoPath(t, s, "$.requestId")
	wantPath(t, s, "$.fields", PathInfo{Kinds: []string{"any"}, Opaque: true})
	wantPath(t, s, "$.latency.ms", PathInfo{Kinds: []string{"integer"}})
	wantPath(t, s, "$.output.<union>", PathInfo{Enum: []string{"message"}})
	wantPath(t, s, "$.output.message.content[].<union>", PathInfo{Enum: []string{"image", "text"}})
	wantPath(t, s, "$.output.message.content[].image", PathInfo{Kinds: []string{"object"}, Variants: []string{"<union>=image"}})
	wantPath(t, s, "$.output.message.content[].image.bytes", PathInfo{Kinds: []string{"blob"}})
	wantPath(t, s, "$.output.message.content[].image.tags.*", PathInfo{Kinds: []string{"integer"}})

	st := extractOne(t, FormatSmithy, smithySpec, RootDef{Name: "stream", Operation: "acme#ConverseStream", Events: true})
	if !reflect.DeepEqual(st.Events, []string{"messageStop", "throttlingException"}) || !reflect.DeepEqual(st.ExceptionEvents, []string{"throttlingException"}) {
		t.Errorf("events %v, exceptions %v", st.Events, st.ExceptionEvents)
	}
	wantPath(t, st, "messageStop:$.stopReason", PathInfo{Kinds: []string{"string"}, Enum: []string{"end_turn", "max_tokens"}})
	wantPath(t, st, "throttlingException:$.message", PathInfo{Kinds: []string{"string"}})

	errs := extractOne(t, FormatSmithy, smithySpec, RootDef{Name: "errors", Operation: "acme#Converse", Errors: true})
	if !reflect.DeepEqual(errs.Events, []string{"ThrottlingException", "ValidationException"}) {
		t.Errorf("error events = %v", errs.Events)
	}
	_, meta, _ := Extract(Source{Provider: "acme", Format: FormatSmithy, Roots: []RootDef{{Name: "c", Operation: "acme#Converse"}}}, []byte(smithySpec))
	if meta["service.version"] != "2023-09-30" {
		t.Errorf("meta = %v", meta)
	}
}

// A hostile spec must not hang the walker or escape the document.
func TestWalkersAreBounded(t *testing.T) {
	// A $ref chain longer than maxDepth, built from distinct names.
	var b strings.Builder
	b.WriteString(`{"openapi":"3.1.0","components":{"schemas":{`)
	for i := 0; i < 100; i++ {
		if i > 0 {
			b.WriteString(",")
		}
		b.WriteString(`"S` + itoa(i) + `":{"type":"object","properties":{"n":{"$ref":"#/components/schemas/S` + itoa(i+1) + `"}}}`)
	}
	b.WriteString(`,"S100":{"type":"string"}}}}`)
	s := extractOne(t, FormatOpenAPI, b.String(), RootDef{Name: "deep", Schema: "#/components/schemas/S0"})
	found := false
	for _, info := range s.Paths {
		if info.Recursive == "max-depth" {
			found = true
		}
	}
	if !found {
		t.Error("a 100-deep chain was not cut at maxDepth")
	}
}

func itoa(i int) string {
	const digits = "0123456789"
	if i < 10 {
		return digits[i : i+1]
	}
	return itoa(i/10) + digits[i%10:i%10+1]
}
