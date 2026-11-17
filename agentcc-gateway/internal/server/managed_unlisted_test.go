package server

import (
	"fmt"
	"testing"
)

// A model id the managed catalogue gives no price at all, neither listed
// nor a listed variant, is refused on one Future AGI key as not available
// before any upstream call, however it is named: by its id, behind the
// managed provider's "openrouter/" prefix, or on a chat request locked to
// the Future AGI provider. A call on the org's own key under the Future AGI
// provider's id is the org's own and is unaffected.

// unpricedModel is an id the test catalogue does not list.
const unpricedModel = "zz-vendor/unlisted-model"

// TestManagedModelTheCatalogueDoesNotPriceIsRefused: on chat and on
// /v1/messages, the unpriced id by its id and behind the "openrouter/"
// prefix, and on chat locked to the Future AGI provider, is refused (404
// model_not_found by its id or the lock, 403 not available behind the
// provider prefix), with no upstream call and the credit untouched, whichever
// way its stream would end (the upstream reports usage, sends none, fails
// mid-stream, the client leaves, a guardrail withholds the answer).
// Non-streamed requests are refused the same way.
func TestManagedModelTheCatalogueDoesNotPriceIsRefused(t *testing.T) {
	ends := append([]struct {
		name     string
		opts     managedGatewayOptions
		upstream upstreamStream
		leave    bool
	}{{name: "the upstream reports usage", upstream: textThenUsage}}, cutStreamEnds...)
	for _, end := range ends {
		t.Run(end.name, func(t *testing.T) {
			for _, surface := range managedSurfaces {
				for _, model := range []string{unpricedModel, "openrouter/" + unpricedModel} {
					// A refused request streams nothing, so the client
					// never gets text to leave after.
					refusedUnlisted(t, fmt.Sprintf("%s %s", surface, model), model,
						runManaged(t, surface, model, true, end.opts, end.upstream, false))
				}
			}
			refusedAsNotFound(t, "chat locked to openrouter, "+unpricedModel, unpricedModel,
				runManagedWith(t, "/v1/chat/completions", unpricedModel, true, end.opts, end.upstream, false, managedLock))
		})
	}
	for _, surface := range managedSurfaces {
		for _, model := range []string{unpricedModel, "openrouter/" + unpricedModel} {
			refusedUnlisted(t, fmt.Sprintf("%s non-streamed %s", surface, model), model,
				runManaged(t, surface, model, false, managedGatewayOptions{}, nil, false))
		}
	}
	refusedAsNotFound(t, "chat locked to openrouter, non-streamed "+unpricedModel, unpricedModel,
		runManagedWith(t, "/v1/chat/completions", unpricedModel, false, managedGatewayOptions{}, nil, false, managedLock))
}

// TestManagedUnpricedModelOnTheOrgsOwnKeyIsByok: with the org's own key
// under the Future AGI provider's id, the unpriced id behind the
// "openrouter/" prefix goes out on the org's key as the org's own call, on
// chat and on /v1/messages: billed byok, the credit untouched, no spend.
func TestManagedUnpricedModelOnTheOrgsOwnKeyIsByok(t *testing.T) {
	servedOnTheOrgsOwnKey(t, "openrouter/"+unpricedModel, unpricedModel)
}
