import json
import os
import uuid

from openai import OpenAI

from agentic_eval.core.embeddings.embeddings_v2 import get_embedding_model

# NOTE: chromadb is lazy-loaded in Tags.__init__ to avoid loading onnxruntime at startup



# class EmbeddingModel:
#     def __init__(self):
#         self.model = model

#     def __call__(self, input: str):
#         instruction = "Represent the following text:"
#         embeddings = self.model.encode([[instruction, input]])
#         return embeddings


class Tags:
    def __init__(self, metric_properties: dict):
        import chromadb

        self.metric_id = metric_properties["id"]
        self.metric_properties = metric_properties
        self.chroma_client = chromadb.Client()
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.embedding_model = get_embedding_model()
        self.tag_db = self.chroma_client.get_or_create_collection(
            f"{self.metric_id}_tags"
        )  # , embedding_function=self.embedding_fn

    def add_tags(self, tags: list):
        self.tag_db.add(tags)

    def get_existing_tags(self):
        total_items = self.tag_db.count()
        existing_tags = self.tag_db.peek(total_items)
        return existing_tags["documents"]

    def _get_tags_from_reason(self, reason: str):
        completion = self.client.chat.completions.create(
            model="anthropic/claude-3.5-sonnet:beta",
            messages=[
                {
                    "role": "user",
                    "content": f"""You are the very best tag generator. You will take a review. This review is a critique of outputs of an AI model against some evaluation criteria. You will generate tags, which reflect the nature of the review. You will pick up key points which the review states and make a tag out of those. You can make anywhere between 2 to 5 tags, but you will keep it to a minimum to keep the tags unique. The least number of tags is desirable, covering all aspects. Each tag can have a maximum of 2 words. Prepend it with the sentiment of the tag. By sentiment, I mean if the tag represents a good thing for the critique or a bad thing. It can only be "POSITIVE" or "NEGATIVE". For e.g. POSTIVE:concise, NEGATIVE:inaccurate, NEGATIVE:bad-chronology. You will return a list of tags with the key "tags" in JSON format. Do not insert backticks.
                    Evaluation Criteria: {self.metric_properties['eval_instructions']}
                    Critique: {reason}""",
                },
            ],
            response_format={"type": "json_object"},
        )
        tag_list = json.loads(completion.choices[0].message.content)["tags"]
        return tag_list

    def embedding_fn(self, text: str):
        instruction = "Represent the following text:"
        embeddings = self.embedding_model([[instruction, text]])
        return embeddings

    def generate_tags(self, reason: str):
        tag_list = self._get_tags_from_reason(reason)
        new_tags = []
        for tag in tag_list:
            results = self.tag_db.query(query_texts=[tag.split(":")[1]], n_results=1)
            if len(results["distances"][0]) > 0 and results["distances"][0][0] < 0.5:
                retrieved_tag = results["documents"][0][0]
                sentiment = results["metadatas"][0][0]["sentiment"]
                new_tags.append(f"{sentiment}:{retrieved_tag}")
            else:
                id = str(uuid.uuid4())
                self.tag_db.upsert(
                    documents=[tag.split(":")[1]],
                    ids=[id],
                    metadatas=[{"sentiment": tag.split(":")[0]}],
                )
                new_tags.append(tag)
        return new_tags


if __name__ == "__main__":
    metric_properties = {
        "id": "test_metric",
        "eval_instructions": "How polite is the model response?",
    }
    tags = Tags(metric_properties)

    test_reason = (
        "The model response was very courteous and used respectful language throughout."
    )
    generated_tags = tags.generate_tags(test_reason)
    test_reason = "the model was very rude"
    generated_tags = tags.generate_tags(test_reason)

    print("Generated tags:", generated_tags)

    existing_tags = tags.get_existing_tags()
    print("Existing tags:", existing_tags)

    print("Test completed successfully.")
