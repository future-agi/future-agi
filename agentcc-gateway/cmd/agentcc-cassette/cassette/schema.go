package cassette

import (
	"encoding/json"
	"regexp"
)

// JSON Schemas in an MCP tools/list result (each tool's inputSchema and
// outputSchema) are protocol structure, not recorded data. A property named
// like a credential (bot_token, webhook_secret) holds the schema of a
// credential, not one: under the usual rule (everything under a credential
// member is a credential) its type, limits and description would become
// placeholders, and the replayed tools/list would no longer match the
// vendor's. Inside a credential's schema only its structure keywords stay
// data; the values a schema can carry (default, const, enum, examples, an
// extension, anything else) are still credentials. Elsewhere a
// "properties" map is data (a CRM record, a Notion page) and the usual rule
// holds.

// schemaRole is what an object or array is in a JSON Schema.
type schemaRole int

const (
	notSchema    schemaRole = iota
	schemaObject            // a schema
	schemaMap               // names to schemas: properties, $defs, ...
	schemaList              // schemas: allOf, anyOf, oneOf, prefixItems, items as a list
)

var (
	schemaMapKeywords = setOf("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
	subschemaKeywords = setOf("items", "additionalProperties", "additionalItems", "unevaluatedItems",
		"unevaluatedProperties", "contains", "propertyNames", "not", "if", "then", "else", "allOf", "anyOf", "oneOf",
		"prefixItems")
	structureKeywords = setOf("type", "format", "title", "description", "$ref", "$id", "$schema", "$comment",
		"$anchor", "$dynamicRef", "$dynamicAnchor", "minLength", "maxLength", "pattern", "minimum", "maximum",
		"exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "uniqueItems", "minContains",
		"maxContains", "minProperties", "maxProperties", "required", "dependentRequired", "readOnly", "writeOnly",
		"deprecated", "nullable", "contentEncoding", "contentMediaType")
)

// toolSchemaRE is the path of a tool's schema in a tools/list result.
var toolSchemaRE = regexp.MustCompile(`^\$\.tools\[[0-9]+\]\.(?:inputSchema|outputSchema)$`)

func isToolSchema(path string) bool { return toolSchemaRE.MatchString(path) }

// inSchema decides the value about to be read (open is its opening
// delimiter, 0 for a string or number) when it lies in a JSON Schema: its
// role if it opens one, whether it lies in a credential's schema (cred),
// and whether it is a credential. ok is false outside every schema, where
// the usual rule decides.
func (o walkOptions) inSchema(top *walkFrame, path string, open json.Delim) (role schemaRole, cred, secret, ok bool) {
	isObj, isArr := open == '{', open == '['
	named := func(k string) bool { return o.secret != nil && o.secret(k, false) }
	switch {
	case top == nil || top.role == notSchema:
		if isObj && o.schema != nil && o.schema(path) && (top == nil || !top.secret) {
			return schemaObject, false, false, true
		}
		return notSchema, false, false, false
	case top.role == schemaMap: // the value of a property (or definition) name
		if isObj {
			return schemaObject, top.cred || named(top.key), false, true
		}
		return notSchema, false, top.cred || named(top.key), true // not a schema: the usual rule
	case top.role == schemaList:
		if isObj {
			return schemaObject, top.cred, false, true
		}
		return notSchema, false, top.cred, true
	}
	switch k := top.key; { // top is a schema; the value of its keyword k
	case schemaMapKeywords[k] && isObj:
		return schemaMap, top.cred, false, true
	case subschemaKeywords[k] && isObj:
		return schemaObject, top.cred, false, true
	case subschemaKeywords[k] && isArr:
		return schemaList, top.cred, false, true
	case structureKeywords[k]:
		return notSchema, false, false, true
	}
	return notSchema, false, top.cred || named(top.key), true // a value the schema carries
}
