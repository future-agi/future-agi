// Package ext extends the mock upstream with OpenRouter's account routes and
// the agent tool-call directives. Its routes register on the mock's mux; its
// directives are the x-mock-behavior names the mock itself does not know.
//
// Routes (OpenRouter's shapes):
//
//	GET    /api/v1/key          the calling key's usage: {data: {label, usage,
//	                            limit, limit_remaining, is_free_tier}}
//	POST   /api/v1/keys         provision a key: {name, limit?, limit_reset?,
//	                            include_byok_in_limit?} -> 201 {data, key}
//	GET    /api/v1/keys         the keys the caller provisioned: {data: [...]}
//	GET    /api/v1/keys/{hash}  one of them: {data}
//	PATCH  /api/v1/keys/{hash}  {name?, disabled?, limit?, limit_reset?,
//	                            include_byok_in_limit?} -> {data}
//	DELETE /api/v1/keys/{hash}  {deleted: true}
//	POST   /__account           {balance_usd}: the balance /api/v1/credits reports
//
// A key's usage is exactly its bearer's share of /__charges: the upstream
// charges the mock recorded for inference calls carrying that bearer, so the
// usage of all bearers adds up to /__charges. Like OpenRouter's, it excludes
// the credit-purchase fee, which the mock never charges. Provisioned keys
// belong to the bearer that created them; another bearer sees 404.
//
// Directives:
//
//	stream_tools:<n>           answer with n tool calls (streamed as argument deltas)
//	tool_args:<base64 json>    set the arguments of the answer's tool calls
//	require_thought_signature  a google/gemini-* model refuses (400
//	                           invalid_request) an assistant tool call that
//	                           carries no thought signature
package ext

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// Register adds the extension routes. It is called once, at startup.
func Register(mux *http.ServeMux, state *mock.State) {
	keys := newKeyStore(state)
	state.OnReset(keys.reset)
	mux.HandleFunc("GET /api/v1/key", keys.handleCurrent)
	mux.HandleFunc("POST /api/v1/keys", keys.handleCreate)
	mux.HandleFunc("GET /api/v1/keys", keys.handleList)
	mux.HandleFunc("GET /api/v1/keys/{hash}", keys.handleGet)
	mux.HandleFunc("PATCH /api/v1/keys/{hash}", keys.handleUpdate)
	mux.HandleFunc("DELETE /api/v1/keys/{hash}", keys.handleDelete)
	mux.HandleFunc("POST /__account", accountHandler(state))
}

// Directive handles directive name with argument arg for req, editing resp.
func Directive(name, arg string, req *mock.Request, resp *mock.Response) error {
	switch name {
	case "stream_tools":
		return streamTools(arg, req, resp)
	case "tool_args":
		return toolArgs(arg, resp)
	case "require_thought_signature":
		return requireThoughtSignature(arg, req, resp)
	}
	return mock.UnknownDirective(name)
}
