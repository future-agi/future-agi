package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// call is one case's request as its endpoint's capability method takes it.
type call struct {
	Endpoint string
	// Body is request.json, with "model" set to the upstream's ID when cloning.
	Body []byte
	// Model is the upstream model ID; the native GenAI methods take it apart
	// from the body, as the gateway takes it from the URL.
	Model  string
	Stream bool
	// Input is a multipart endpoint's upload.
	Input *contracttest.InputFile
}

// textCompleter is the server's nativeTextCompletionProvider: providers that
// serve the legacy /v1/completions themselves.
type textCompleter interface {
	TextCompletion(ctx context.Context, req *models.CompletionRequest) (*models.CompletionResponse, error)
}

// drive sends c through the adapter method its endpoint names, the one the
// gateway's handler for that route calls, and consumes the whole answer. The
// error is the adapter's; what upstream answered is in the teeing proxy.
func drive(ctx context.Context, p providers.Provider, c call) error {
	switch c.Endpoint {
	case contracttest.EndpointChat:
		req, err := decode[models.ChatCompletionRequest](c.Body)
		if err != nil {
			return err
		}
		if !req.Stream {
			_, err := p.ChatCompletion(ctx, req)
			return err
		}
		chunks, errs := p.StreamChatCompletion(ctx, req)
		return drainChunks(ctx, chunks, errs)

	case contracttest.EndpointChatViaResponses:
		cp, err := capability[providers.ChatViaResponsesProvider](p, c.Endpoint, "ChatViaResponsesProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.ChatCompletionRequest](c.Body)
		if err != nil {
			return err
		}
		if !req.Stream {
			_, err := cp.ChatCompletionViaResponses(ctx, req)
			return err
		}
		chunks, errs := cp.StreamChatCompletionViaResponses(ctx, req)
		return drainChunks(ctx, chunks, errs)

	case contracttest.EndpointCompletions:
		tp, err := capability[textCompleter](p, c.Endpoint, "TextCompletion")
		if err != nil {
			return err
		}
		req, err := decode[models.CompletionRequest](c.Body)
		if err != nil {
			return err
		}
		_, err = tp.TextCompletion(ctx, req)
		return err

	case contracttest.EndpointEmbeddings:
		ep, err := capability[providers.EmbeddingProvider](p, c.Endpoint, "EmbeddingProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.EmbeddingRequest](c.Body)
		if err != nil {
			return err
		}
		_, err = ep.CreateEmbedding(ctx, req)
		return err

	case contracttest.EndpointImages:
		ip, err := capability[providers.ImageProvider](p, c.Endpoint, "ImageProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.ImageRequest](c.Body)
		if err != nil {
			return err
		}
		_, err = ip.CreateImage(ctx, req)
		return err

	case contracttest.EndpointSpeech:
		sp, err := capability[providers.SpeechProvider](p, c.Endpoint, "SpeechProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.SpeechRequest](c.Body)
		if err != nil {
			return err
		}
		audio, _, err := sp.CreateSpeech(ctx, req)
		return drain(audio, err)

	case contracttest.EndpointTranscription:
		tp, err := capability[providers.TranscriptionProvider](p, c.Endpoint, "TranscriptionProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.TranscriptionRequest](c.Body)
		if err != nil {
			return err
		}
		if c.Input == nil {
			return errors.New("no input file")
		}
		req.FileData, req.FileName = c.Input.Data, c.Input.Name
		_, err = tp.CreateTranscription(ctx, req)
		return err

	case contracttest.EndpointTranslation:
		tp, err := capability[providers.TranslationProvider](p, c.Endpoint, "TranslationProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.TranslationRequest](c.Body)
		if err != nil {
			return err
		}
		if c.Input == nil {
			return errors.New("no input file")
		}
		req.FileData, req.FileName = c.Input.Data, c.Input.Name
		_, err = tp.CreateTranslation(ctx, req)
		return err

	case contracttest.EndpointRerank:
		rp, err := capability[providers.RerankProvider](p, c.Endpoint, "RerankProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.RerankRequest](c.Body)
		if err != nil {
			return err
		}
		_, err = rp.Rerank(ctx, req)
		return err

	case contracttest.EndpointOCR:
		op, err := capability[providers.OCRProvider](p, c.Endpoint, "OCRProvider")
		if err != nil {
			return err
		}
		req, err := decode[models.OCRRequest](c.Body)
		if err != nil {
			return err
		}
		_, err = op.OCR(ctx, req)
		return err

	case contracttest.EndpointResponses:
		rp, err := capability[providers.ResponsesProvider](p, c.Endpoint, "ResponsesProvider")
		if err != nil {
			return err
		}
		if !c.Stream {
			_, _, err := rp.CreateResponse(ctx, c.Body)
			return err
		}
		stream, _, err := rp.StreamResponse(ctx, c.Body)
		return drain(stream, err)

	case contracttest.EndpointAssistants:
		ap, err := capability[providers.AssistantsProvider](p, c.Endpoint, "AssistantsProvider")
		if err != nil {
			return err
		}
		req, err := contracttest.DecodePassthrough(c.Body)
		if err != nil {
			return err
		}
		if !req.Stream() {
			_, _, _, err := ap.ProxyAssistantsRequest(ctx, req.Method, req.Path, req.Body, req.Query)
			return err
		}
		stream, _, _, err := ap.StreamAssistantsRequest(ctx, req.Method, req.Path, req.Body, req.Query)
		return drain(stream, err)

	case contracttest.EndpointVectorStores:
		vp, err := capability[providers.VectorStoresProvider](p, c.Endpoint, "VectorStoresProvider")
		if err != nil {
			return err
		}
		req, err := contracttest.DecodePassthrough(c.Body)
		if err != nil {
			return err
		}
		_, _, _, err = vp.ProxyVectorStoresRequest(ctx, req.Method, req.Path, req.Body, req.Query)
		return err

	// The native passthroughs get no client headers: the adapter sets
	// anthropic-version itself, as the contract suites' requests do.
	case contracttest.EndpointAnthropicMessages:
		ap, err := capability[providers.AnthropicNativeProvider](p, c.Endpoint, "AnthropicNativeProvider")
		if err != nil {
			return err
		}
		if !c.Stream {
			_, _, err := ap.CreateAnthropicMessage(ctx, c.Body, nil)
			return err
		}
		stream, _, err := ap.StreamAnthropicMessage(ctx, c.Body, nil)
		return drain(stream, err)

	case contracttest.EndpointAnthropicCountTokens:
		ap, err := capability[providers.AnthropicNativeProvider](p, c.Endpoint, "AnthropicNativeProvider")
		if err != nil {
			return err
		}
		_, _, err = ap.CountAnthropicTokens(ctx, c.Body, nil)
		return err

	case contracttest.EndpointGenAIGenerate:
		gp, err := capability[providers.GenAINativeProvider](p, c.Endpoint, "GenAINativeProvider")
		if err != nil {
			return err
		}
		if !c.Stream {
			_, _, err := gp.GenerateContent(ctx, c.Model, c.Body, nil)
			return err
		}
		stream, _, err := gp.StreamGenerateContent(ctx, c.Model, c.Body, nil)
		return drain(stream, err)

	case contracttest.EndpointGenAICountTokens:
		gp, err := capability[providers.GenAINativeProvider](p, c.Endpoint, "GenAINativeProvider")
		if err != nil {
			return err
		}
		_, _, err = gp.GenAICountTokens(ctx, c.Model, c.Body, nil)
		return err

	case contracttest.EndpointGenAIEmbed:
		gp, err := capability[providers.GenAINativeProvider](p, c.Endpoint, "GenAINativeProvider")
		if err != nil {
			return err
		}
		_, _, err = gp.EmbedContent(ctx, c.Model, c.Body, nil)
		return err

	case contracttest.EndpointListModels:
		_, err := p.ListModels(ctx)
		return err
	}
	return fmt.Errorf("the recorder cannot drive endpoint %q", c.Endpoint)
}

// capability returns p as the capability interface T an endpoint needs.
func capability[T any](p providers.Provider, endpoint, iface string) (T, error) {
	v, ok := p.(T)
	if !ok {
		return v, fmt.Errorf("the %s adapter does not implement %s, which endpoint %q drives", p.ID(), iface, endpoint)
	}
	return v, nil
}

// decode decodes request.json as the gateway's handler decodes the request
// body: leniently, so keys the type lacks (the input file's name) are
// ignored.
func decode[T any](body []byte) (*T, error) {
	v := new(T)
	if err := json.Unmarshal(body, v); err != nil {
		return nil, fmt.Errorf("request.json: %v", err)
	}
	return v, nil
}

// drain reads a streamed answer to the end and closes it, as a client would.
func drain(rc io.ReadCloser, err error) error {
	if rc == nil {
		return err
	}
	_, cerr := io.Copy(io.Discard, rc)
	_ = rc.Close()
	if err == nil {
		err = cerr
	}
	return err
}

// drainChunks consumes a chat stream to the end.
func drainChunks(ctx context.Context, chunks <-chan models.StreamChunk, errs <-chan error) error {
	for open := true; open; {
		select {
		case _, open = <-chunks:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	select {
	case err := <-errs:
		return err
	case <-time.After(10 * time.Second):
		return errors.New("the adapter closed its chunk channel but never its error channel")
	}
}
