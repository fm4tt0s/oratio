"""
generate_skill.py — Generates a Claude.ai Project system prompt from a style guide PDF.

Usage:
    .venv/bin/python generate_skill.py
    # or via Makefile:
    make skill

Output:
    skill_output/system_prompt.md   ← paste this into Claude.ai Project Instructions
    skill_output/INSTRUCTIONS.md    ← step-by-step setup guide
"""
# ignore some linting rules
# pylint: disable=line-too-long
# pyright: reportMissingImports=false
import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

OUTPUT_DIR = os.path.join(BASE_DIR, "skill_output")

# ── Config ────────────────────────────────────────────────────────────────────

PDF_PATH_RAW = os.getenv("PDF_PATH", "writing-style-documentation.pdf")
PDF_PATH = (
    PDF_PATH_RAW if os.path.isabs(PDF_PATH_RAW)
    else os.path.join(BASE_DIR, PDF_PATH_RAW)
)

# Optional: override the project name shown in the output
PROJECT_NAME = os.getenv("SKILL_PROJECT_NAME", "Writing Style Guide Expert")

# Extra context from .env (business unit specialisation)
EXTRA_CONTEXT = os.getenv("EXTRA_CONTEXT", "").strip()

# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are a technical writer and prompt engineer.

I will give you the full text of a corporate Writing Style Guide.
Your job is to produce a comprehensive, structured system prompt that turns
Claude into an expert on this specific style guide.

The system prompt you write must:
1. Open with a clear role statement (who Claude is in this project)
2. List the most important rules from the guide, grouped by category
   (e.g. Contractions, Terminology, Punctuation, Formatting, Tone, etc.)
3. Include specific approved/prohibited examples from the guide where useful
4. Include instructions for how Claude should respond when asked to:
   a) Answer a question about a rule
   b) Check a piece of text for violations
   c) Suggest a rewrite
5. Tell Claude to cite page numbers when possible
6. Be self-contained — Claude should be able to follow it without the PDF
   open, though the PDF will also be attached as project knowledge for lookups

{extra_context_block}

Format the output as clean Markdown, ready to paste into a Claude.ai Project
Instructions field. Do not include any preamble or explanation — output only
the system prompt itself, starting with the role statement.

--- STYLE GUIDE TEXT START ---
{pdf_text}
--- STYLE GUIDE TEXT END ---"""



CLAUDE_MD_PROMPT = """You are a technical writer and prompt engineer.

I will give you the full text of a corporate Writing Style Guide.
Your job is to produce a CLAUDE.md instruction file that makes Claude Code
behave as a style guide expert whenever it works in this project or globally.

The CLAUDE.md must:
1. Open with a brief description of what this file does and when Claude should apply it
2. List the most important rules from the guide, grouped by category
   (e.g. Contractions, Terminology, Punctuation, Formatting, Tone, Inclusive language)
3. Include specific approved/prohibited examples from the guide
4. Tell Claude how to respond when asked to:
   a) Answer a question about a rule
   b) Check a piece of text for  show each violation with Found/Fixviolations 
   c) Suggest a rewrite
5. Be  CLAUDE.md is loaded into context on every session, so keep it under 300 linesconcise 
6. Cite page numbers where helpful

{extra_context_block}

Format as clean Markdown. Output only the CLAUDE.md  no preamble.content 
Start with a level-1 heading: # Writing Style Guide

--- STYLE GUIDE TEXT START ---
{pdf_text}
--- STYLE GUIDE TEXT END ---"""


SKILL_MD_PROMPT = """You are a technical writer and prompt engineer.

I will give you the full text of a corporate Writing Style Guide.
Your job is to produce a SKILL.md file in the Claude Code skill format.
This skill will be installed in ~/.claude/skills/ and used across all projects.

The SKILL.md must have:
1. A YAML frontmatter block with:
   - name: writing-style-guide
   - description: a detailed trigger description (2-4 sentences) explaining when Claude
     should consult this  be specific about keywords and contexts that should trigger it,skill 
     e.g. "whenever the user asks about style rules, requests a text review, asks to check
     documentation, or pastes text asking for feedback"
2. A body with:
   - A brief overview of what this skill does
   - The most important rules from the guide grouped by category
   - Specific approved/prohibited examples
   - How to format a style review response (verdict, issues with Found/Fix, summary)
   - Page citation instructions

{extra_context_block}

Keep the total file under 400 lines. Output only the SKILL.md  no preamble.content 

--- STYLE GUIDE TEXT START ---
{pdf_text}
--- STYLE GUIDE TEXT END ---"""
# ── PDF extraction ────────────────────────────────────────────────────────────

def extract_pdf_text(path: str, max_chars: int = 80000) -> str:
    """Extract text from PDF, capped at max_chars to fit LLM context."""
    from langchain_community.document_loaders import PyPDFLoader

    print(f"Loading PDF: {path}")
    loader = PyPDFLoader(path)
    docs = loader.load()
    print(f"  -> {len(docs)} pages loaded.")

    full_text = "\n\n".join(d.page_content for d in docs)

    if len(full_text) > max_chars:
        print(f"  -> PDF is large ({len(full_text):,} chars), truncating to {max_chars:,} for LLM context.")
        print("     Tip: the most important rules are usually in the first half of style guides.")
        full_text = full_text[:max_chars]

    return full_text


# ── LLM call ─────────────────────────────────────────────────────────────────

def call_llm(prompt: str) -> str:
    """Call the configured LLM backend to generate the system prompt."""
    backend = os.getenv("LLM_BACKEND", "claude").lower()

    if backend == "claude":
        from anthropic import Anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set in .env")
        print("Using backend: Claude (claude-sonnet-4-20250514)")
        client = Anthropic(api_key=api_key, base_url=base_url)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    elif backend == "openrouter":
        from openai import OpenAI
        api_key = os.getenv("OPENROUTER_API_KEY")
        model = os.getenv("OPENROUTER_MODEL", "openrouter/free")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not set in .env")
        print(f"Using backend: OpenRouter ({model})")
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Oratio"),
            },
        )
        resp = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content

    elif backend == "ollama":
        from openai import OpenAI
        model = os.getenv("OLLAMA_MODEL", "mistral:7b")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"Using backend: Ollama ({model})")
        print("  Note: generating a full system prompt from a large PDF can take several minutes with a local model.")
        client = OpenAI(api_key="ollama", base_url=f"{base_url.rstrip('/')}/v1")
        resp = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content

    elif backend == "copilot":
        from openai import OpenAI
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN is not set in .env")
        print("Using backend: GitHub Copilot (gpt-4o)")
        client = OpenAI(
            api_key=token,
            base_url="https://models.inference.ai.azure.com",
        )
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content

    else:
        raise ValueError(f"Unknown LLM_BACKEND '{backend}'. Set it in .env.")


# ── Output writers ────────────────────────────────────────────────────────────

def write_system_prompt(system_prompt: str) -> str:
    """Function creating the system prompt."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "system_prompt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {PROJECT_NAME} — System Prompt\n\n")
        f.write("<!-- Paste the content below (excluding this line) into your Claude.ai Project Instructions -->\n\n")
        f.write(system_prompt)
    return path


def write_instructions(pdf_filename: str) -> str:
    """Function writing INSTRUCTIONS.md."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "INSTRUCTIONS.md")
    content = f"""# Setting Up Your Claude.ai Project

Follow these steps to turn your style guide into a Claude.ai Project.

## Step 1 — Create a new Project

1. Go to [claude.ai](https://claude.ai)
2. Click **Projects** in the left sidebar
3. Click **Create project**
4. Name it: **{PROJECT_NAME}**

## Step 2 — Add the system prompt

1. Inside your project, click **Edit project instructions** (or the ✏️ icon)
2. Open `skill_output/system_prompt.md`
3. Copy everything after the HTML comment line
4. Paste it into the Project Instructions field
5. Click **Save**

## Step 3 — Upload the PDF as knowledge

1. Inside your project, click **Add content** (or the 📎 icon)
2. Upload `{pdf_filename}`
3. Claude will index it automatically

## Step 4 — Start using it

Open a new conversation inside the project and try:

- *"Can I use contractions like 'can't' in documentation?"*
- *"Check this paragraph for style violations: [paste your text]"*
- *"Rewrite this sentence to comply with our style guide: [paste sentence]"*
- *"What does the guide say about formatting IP addresses?"*

## What the project knowledge adds

The system prompt alone covers the most important rules extracted from the PDF.
The uploaded PDF gives Claude the ability to look up edge cases, specific
examples, and rules that didn't make it into the summary.

## Updating

If your style guide PDF is updated, re-run `make skill` to regenerate the
system prompt, then repeat Steps 2–3.
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def write_claude_md(claude_md: str) -> str:
    """Function writing CLAUDE.md."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "CLAUDE.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!-- Generated by make skill -->\n<!-- Copy to your project root or ~/.claude/CLAUDE.md -->\n\n")
        f.write(claude_md)
    return path


def write_skill_md(skill_md: str) -> str:
    """Function writing SKILL.md."""
    skill_dir = os.path.join(OUTPUT_DIR, "writing-style-guide-skill")
    os.makedirs(skill_dir, exist_ok=True)
    path = os.path.join(skill_dir, "SKILL.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(skill_md)
    return path


def main():
    """Main function."""
    if not os.path.exists(PDF_PATH):
        print(f"ERROR: PDF not found at {PDF_PATH}")
        print("Set PDF_PATH in your .env file.")
        sys.exit(1)

    # 1. Extract PDF text (shared across all three outputs)
    pdf_text = extract_pdf_text(PDF_PATH)

    extra_context_block = (
        f"Additional context about the intended audience / business unit:\n{EXTRA_CONTEXT}\n"
        if EXTRA_CONTEXT else ""
    )

    # 2a. Claude.ai Project system prompt
    print("\n[1/3] Generating Claude.ai Project system prompt...")
    system_prompt = call_llm(EXTRACTION_PROMPT.format(
        pdf_text=pdf_text,
        extra_context_block=extra_context_block,
    ))
    pdf_filename = os.path.basename(PDF_PATH)
    sp_path = write_system_prompt(system_prompt)
    write_instructions(pdf_filename)
    print(f"      → {sp_path}")

    # 2b. CLAUDE.md for Claude Code
    print("\n[2/3] Generating CLAUDE.md for Claude Code...")
    claude_md = call_llm(CLAUDE_MD_PROMPT.format(
        pdf_text=pdf_text,
        extra_context_block=extra_context_block,
    ))
    claude_md_path = write_claude_md(claude_md)
    print(f"      → {claude_md_path}")

    # 2c. SKILL.md for ~/.claude/skills/
    print("\n[3/3] Generating SKILL.md for ~/.claude/skills/...")
    skill_md = call_llm(SKILL_MD_PROMPT.format(
        pdf_text=pdf_text,
        extra_context_block=extra_context_block,
    ))
    skill_md_path = write_skill_md(skill_md)
    print(f"      → {skill_md_path}")

    # 3. Summary
    print(f"""
Done! Three outputs generated in {OUTPUT_DIR}/

  Claude.ai Project  → skill_output/system_prompt.md
                        (paste into Claude.ai Project Instructions + upload PDF)

  Claude Code        → skill_output/CLAUDE.md
    project-level:    cp skill_output/CLAUDE.md <your-project-root>/CLAUDE.md
    global:           cp skill_output/CLAUDE.md ~/.claude/CLAUDE.md

  Claude Code skill  → skill_output/writing-style-guide-skill/SKILL.md
    install:          cp -r skill_output/writing-style-guide-skill ~/.claude/skills/

See skill_output/INSTRUCTIONS.md for full setup details.
""")


if __name__ == "__main__":
    main()
