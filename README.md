# AskTax — AI Tax Assistant Platform for Pakistani CA Firms

> Full-stack RAG-powered tax chatbot platform. CA firms sign up, upload their documents, and their clients get instant AI-answered tax queries — cited from Pakistani tax law, FBR circulars, and SROs.

## What It Does

- **Multi-tenant** — each CA firm gets its own isolated knowledge base and chat interface
- **RAG pipeline** — documents ingested, chunked, embedded, and stored in Qdrant for semantic search
- **Cited answers** — every response references the source document and section
- **Firm dashboard** — firms manage their knowledge base, view chat history, and track usage
- **Public landing** — tax guide, blog, firm signup, and shared chat interface
- **Admin panel** — platform-level management of firms and documents

## Architecture

```mermaid
flowchart TD
    U([User / CA Firm Client]) --> FE[Frontend\nHTML + CSS + JS]
    FE -->|REST / SSE| API[FastAPI Backend\nPython]

    API --> AUTH[/auth\nJWT Authentication]
    API --> FIRMS[/firms\nMulti-tenant Management]
    API --> DOCS[/documents\nUpload + Ingestion]
    API --> CHAT[/chat\nRAG Query Engine]
    API --> ADMIN[/admin\nPlatform Admin]

    DOCS -->|async job| WORKER[Celery Worker]
    WORKER --> CHUNK[Chunker\n~500 token splits]
    CHUNK --> EMBED[OpenAI Embeddings\ntext-embedding-3-large]
    EMBED --> QDRANT[(Qdrant\nper-firm collections)]

    CHAT --> QDRANT
    QDRANT -->|top-k chunks| RERANK[Jina Reranker\ncross-encoder]
    RERANK --> LLM[OpenAI GPT-4o\nAnswer + Citations]
    LLM --> FE

    WORKER --> MINIO[(MinIO\nDocument Storage)]
    API --> PG[(PostgreSQL\nUsers + Firms + Metadata)]
    API --> REDIS[(Redis\nTask Queue + Cache)]
    WORKER --> REDIS

    subgraph Infrastructure
        NGINX[Nginx] --> API
        CF[Cloudflare Tunnel] --> NGINX
    end
```

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python |
| LLM | OpenAI GPT-4o |
| Embeddings | OpenAI text-embedding-3-large |
| Vector DB | Qdrant (per-tenant collections) |
| Database | PostgreSQL |
| Task Queue | Celery + Redis |
| Storage | MinIO |
| Frontend | HTML, CSS, Vanilla JS |
| Infrastructure | Docker, Cloudflare Tunnel, Nginx |

## Setup

```bash
cp .env.example .env
# Configure API keys in .env

docker compose up -d
```

API docs: `http://localhost:8000/docs`

## Environment Variables

See `.env.example`. Key variables:

```
OPENAI_API_KEY=        # For embeddings + LLM
SECRET_KEY=            # JWT signing key
MINIO_ACCESS_KEY=      # Object storage
DATABASE_URL=          # PostgreSQL connection
QDRANT_HOST=           # Vector DB host
```
