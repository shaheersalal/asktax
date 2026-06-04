from openai import OpenAI
from qdrant_client import QdrantClient
from app.core.config import get_settings

settings = get_settings()
openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
qdrant_client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)

query = "What is withholding tax on property sale under section 236C?"

response = openai_client.embeddings.create(
    model="text-embedding-3-small",
    input=[query]
)
query_vector = response.data[0].embedding

results = qdrant_client.query_points(
    collection_name=settings.QDRANT_COLLECTION,
    query=query_vector,
    limit=5,
).points

print(f"Query: {query}")
print("---")
for i, r in enumerate(results):
    print(f"[{i+1}] Score: {r.score:.4f}")
    print(f"    Title: {r.payload['title'][:70]}")
    print(f"    Category: {r.payload['category']}")
    print(f"    Text: {r.payload['text'][:200]}")
    print()