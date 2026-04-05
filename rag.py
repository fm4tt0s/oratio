"""
rag.py — RAG engine for the Writing Style Guide MCP Server.

Supports two LLM backends, switchable via the LLM_BACKEND env variable:
  - "claude"  → Anthropic Claude (claude-sonnet-4-20250514)
  - "copilot" → GitHub Copilot Models (OpenAI-compatible endpoint)

Exposes two public functions:
  - query_style_guide(question)  → Q&A against the PDF
  - oratio_check_text_style(text)       → Review a piece of text for style violations

Uses the modern LangChain LCEL API — no deprecated RetrievalQA or langchain.chains.
"""
# ignore some linting rules
# pyright: reportMissingImports=false
# pylint: disable=global-statement,global-variable-not-assigned,line-too-long,broad-exception-caught,invalid-name
import os
from typing import Optional

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableMap
from dotenv import load_dotenv

# ── Load .env ────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

# ── Configuration (from .env with sensible defaults) ─────────────────────────

LLM_BACKEND   = os.getenv("LLM_BACKEND", "claude").lower()
RETRIEVER_K   = int(os.getenv("RETRIEVER_K", "5"))
CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

_raw_pdf_path = os.getenv("PDF_PATH", "writing-style-documentation.pdf")
PDF_PATH = (
    _raw_pdf_path
    if os.path.isabs(_raw_pdf_path)
    else os.path.join(BASE_DIR, _raw_pdf_path)
)

# Optional specialisation context injected into both prompts.
# Set this to tailor feedback for a specific business unit or audience. Examples:
#   "You are reviewing content for an IBM consulting engagement targeting C-suite executives."
#   "This content is for a financial services audience. Precision and compliance language matter."
#   "You are reviewing developer documentation for a cloud platform product."
EXTRA_CONTEXT = os.getenv("EXTRA_CONTEXT", "").strip()

# Builds an optional block inserted at the top of each prompt.
# Resolves to an empty string when EXTRA_CONTEXT is not set, keeping prompts clean.
_CONTEXT_BLOCK = (
    f"Specialisation context — apply this when interpreting and prioritising rules:\n{EXTRA_CONTEXT}\n\n"
    if EXTRA_CONTEXT else ""
)

# ── Prompt templates ──────────────────────────────────────────────────────────

# NOTE: {extra_context} is filled at build time (not by LangChain) so that
# PromptTemplate only sees {context}, {question}, and {text} as variables.

QA_PROMPT = PromptTemplate.from_template(
    _CONTEXT_BLOCK +
    """You are an expert on the company Writing Style Guide.
You have access to the full Writing Style Guide content. Use ONLY this content to answer.
Do not use external knowledge or personal opinion.

Guidelines:
- Locate the specific rule, term, or example in the guide if asked.
- Synthesize information from multiple sections when relevant.
- If the guide does not contain the answer, say so clearly and suggest consulting the full guide.
- Provide examples from the guide when helpful.
- Cite the relevant section or page number when possible.
- Keep answers clear, concise, and professional.

Relevant Context from Writing Style Guide:
{context}

User Question:
{question}

Answer:"""
)

REVIEW_PROMPT = PromptTemplate.from_template(
    _CONTEXT_BLOCK +
    """You are a strict Writing Style Guide editor.
You will receive a text to review and relevant rules from a Writing Style Guide.

CRITICAL INSTRUCTIONS:
1. Only flag issues that are explicitly covered by the retrieved rules below.
2. Do NOT use general writing advice or knowledge outside the provided rules.
3. Do NOT reference this tool, MCP, or any AI system in your response.
4. You MUST follow the exact output format shown below — no variations.
5. Every issue MUST have its own X [FAIL] or ! [WARN] line with Found and Fix.

OUTPUT FORMAT — copy this structure exactly:

STYLE REVIEW
============

VERDICT: NEEDS REVISION

ISSUES FOUND
------------
X [FAIL] - <rule name> (p.<page number if known>)
   Found:    "<exact offending words from the text>"
   Rule:     <one sentence: what the style guide says>
   Fix:      "<replacement text>"

! [WARN] - <rule name> (p.<page number if known>)
   Found:    "<exact offending words from the text>"
   Rule:     <one sentence: what the style guide says>
   Fix:      "<replacement text>"

SUMMARY
-------
<2-3 sentences about overall quality and key changes needed.>

---

Use VERDICT: PASS only when there are zero issues.
Use VERDICT: PASS WITH SUGGESTIONS for WARN issues only.
Use VERDICT: NEEDS REVISION when there is at least one FAIL issue.

Relevant rules from the Writing Style Guide:
{context}

Text to review:
{text}

Review:"""
)

# ── Module-level singletons ───────────────────────────────────────────────────

_qa_chain     = None
_review_chain = None
_vector_store: Optional[FAISS] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _docs_to_str(docs) -> str:
    """Concatenate retrieved document chunks into a single context string."""
    return "\n\n---\n\n".join(doc.page_content for doc in docs)


def _get_source_pages(docs) -> list:
    """Extract sorted unique page numbers from retrieved documents."""
    pages = set()
    for doc in docs:
        p = doc.metadata.get("page")
        if isinstance(p, int):
            pages.add(p + 1)
    return sorted(pages)


# ── LLM factory ──────────────────────────────────────────────────────────────

def _build_llm():
    """Return a LangChain chat model based on LLM_BACKEND."""

    # Pick up proxy from env if set (e.g. corporate proxies that curl respects
    # but httpx doesn't inherit automatically)
    proxy_url = (
        os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
    )
    if proxy_url:
        print(f"  (using proxy: {proxy_url})")

    if LLM_BACKEND == "claude":
        from langchain_anthropic import ChatAnthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file (see .env.example)."
            )

        # Pick up explicit base_url override from .env (e.g. to avoid local
        # Ollama at localhost:11434 intercepting the SDK)
        base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        print(f"Using backend: Claude (claude-sonnet-4-20250514) -> {base_url}")

        return ChatAnthropic(
            model="claude-sonnet-4-20250514",
            temperature=0,
            anthropic_api_key=api_key,
            anthropic_api_url=base_url,
        )

    elif LLM_BACKEND == "copilot":
        from langchain_openai import ChatOpenAI

        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token:
            raise ValueError(
                "GITHUB_TOKEN is not set. "
                "Add it to your .env file (see .env.example)."
            )
        print("Using backend: GitHub Copilot (gpt-4o via GitHub Models)")

        import httpx
        http_client = (
            httpx.Client(proxy=proxy_url) if proxy_url else None
        )
        return ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            api_key=github_token,
            base_url="https://models.inference.ai.azure.com",
            **({"http_client": http_client} if http_client else {}),
        )

    elif LLM_BACKEND == "ollama":
        from langchain_openai import ChatOpenAI

        model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"Using backend: Ollama ({model}) -> {base_url}")

        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key="ollama",           # Ollama ignores this but LangChain requires it
            base_url=f"{base_url.rstrip('/')}/v1",
        )

    elif LLM_BACKEND == "openrouter":
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not set. "
                "Add it to your .env file (see .env.example)."
            )
        model = os.getenv("OPENROUTER_MODEL", "openrouter/free")
        print(f"Using backend: OpenRouter ({model})")

        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                # Recommended by OpenRouter for attribution + rate limit tiers
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Oratio"),
            },
        )

    else:
        raise ValueError(
            f"Unknown LLM_BACKEND '{LLM_BACKEND}'. "
            "Set LLM_BACKEND to 'claude', 'copilot', 'openrouter', or 'ollama' in your .env file."
        )


# ── RAG initializer ───────────────────────────────────────────────────────────

def initialize_rag() -> None:
    """
    Build the full RAG pipeline using modern LangChain LCEL:
      1. Load and chunk the PDF
      2. Create FAISS vector store with HuggingFace embeddings
      3. Build Q&A and style-review chains
    """
    global _qa_chain, _review_chain, _vector_store

    # ── 1. Validate PDF ───────────────────────────────────────────────────────
    if not os.path.exists(PDF_PATH):
        raise FileNotFoundError(
            f"PDF not found at: {PDF_PATH}\n"
            "Set PDF_PATH in your .env file to point to your style guide PDF."
        )

    # ── 2. Load PDF ───────────────────────────────────────────────────────────
    print(f"Loading PDF: {PDF_PATH}")
    loader = PyPDFLoader(PDF_PATH)
    documents = loader.load()
    print(f"  -> {len(documents)} pages loaded.")

    # ── 3. Chunk ──────────────────────────────────────────────────────────────
    print(f"Splitting into chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
    )
    splits = splitter.split_documents(documents)
    print(f"  -> {len(splits)} chunks created.")

    # ── 4. Embeddings + FAISS ─────────────────────────────────────────────────
    print("Creating embeddings (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    _vector_store = FAISS.from_documents(splits, embeddings)
    print("  -> Vector store ready.")

    # ── 5. LLM ────────────────────────────────────────────────────────────────
    llm = _build_llm()
    parser = StrOutputParser()

    # ── 6. Q&A chain (LCEL) ───────────────────────────────────────────────────
    qa_retriever = _vector_store.as_retriever(search_kwargs={"k": RETRIEVER_K})
    _qa_chain = (
        RunnableMap({
            "context": lambda x: _docs_to_str(qa_retriever.invoke(x["question"])),
            "question": lambda x: x["question"],
        })
        | QA_PROMPT
        | llm
        | parser
    )

    # ── 7. Style-review chain (LCEL) ──────────────────────────────────────────
    review_retriever = _vector_store.as_retriever(search_kwargs={"k": RETRIEVER_K + 3})
    _review_chain = (
        RunnableMap({
            "context": lambda x: _docs_to_str(review_retriever.invoke(x["text"])),
            "text": lambda x: x["text"],
        })
        | REVIEW_PROMPT
        | llm
        | parser
    )

    print("RAG pipeline ready.\n")


# ── Public query function ─────────────────────────────────────────────────────

def query_style_guide(question: str) -> str:
    """
    Query the Writing Style Guide.
    Lazily initialises the RAG pipeline on first call.
    """
    global _qa_chain, _vector_store

    if _qa_chain is None:
        try:
            initialize_rag()
        except Exception as exc:
            return f"[RAG init error] {exc}"

    try:
        answer = _qa_chain.invoke({"question": question})

        # Append source pages
        retriever = _vector_store.as_retriever(search_kwargs={"k": RETRIEVER_K})
        docs = retriever.invoke(question)
        pages = _get_source_pages(docs)
        if pages:
            answer += f"\n\nSource pages: {', '.join(str(p) for p in pages)}"

        return answer

    except Exception as exc:
        return f"[Query error] {exc}"


# ── Public style-check function ───────────────────────────────────────────────

def oratio_check_text_style(text: str) -> str:
    """
    Review a piece of text against the Writing Style Guide.
    Lazily initialises the RAG pipeline on first call.
    """
    global _review_chain, _vector_store

    if _review_chain is None:
        try:
            initialize_rag()
        except Exception as exc:
            return f"[RAG init error] {exc}"

    if not text or not text.strip():
        return "Please provide some text to review."

    try:
        review = _review_chain.invoke({"text": text})

        # Append source pages
        retriever = _vector_store.as_retriever(search_kwargs={"k": RETRIEVER_K + 3})
        docs = retriever.invoke(text)
        pages = _get_source_pages(docs)
        if pages:
            review += f"\n\nRules retrieved from pages: {', '.join(str(p) for p in pages)}"

        return review

    except Exception as exc:
        return f"[Style check error] {exc}"


# ── Quick self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("── Q&A test ──────────────────────────────────────────────────────")
    print(query_style_guide("What are the rules for using contractions?"))

    print("\n── Style check test ──────────────────────────────────────────────")
    sample = (
        "The user can't login if they don't have a valid token. "
        "Make sure you've configured the master node before proceeding. "
        "Click the button and it will delete all of the records."
    )
    print(f"Sample text:\n{sample}\n")
    print(oratio_check_text_style(sample))
