"""
server.py — MCP server for the Writing Style Guide Expert.

Exposes two tools:
  ask_writing_style_guide(question: str) -> str   Q&A against the PDF
  oratio_check_text_style(text: str) -> str              Review text for style violations

Compatible with Claude Desktop and VS Code (via MCP extension).
"""
# ignore some linting rules
# pylint: disable=line-too-long
# pyright: reportMissingImports=false
from mcp.server.fastmcp import FastMCP
import rag

mcp = FastMCP("Oratio")


@mcp.tool()
def ask_writing_style_guide(question: str) -> str:
    """
    Ask a question about the Writing Style Guide.

    The tool searches the PDF and returns a precise, cited answer.
    Examples:
      - "Can I use contractions like \"can't\"?"
      - "How should IP addresses be formatted?"
      - "Is the term 'master/slave' approved?"
      - "What are the rules for Oxford commas?"
    """
    return rag.query_style_guide(question)


@mcp.tool()
def oratio_check_text_style(text: str) -> str:
    """
    Review a piece of text against the Writing Style Guide.

    Returns a structured report with:
      - VERDICT: PASS | PASS WITH SUGGESTIONS | NEEDS REVISION
      - Each violation flagged with: the offending excerpt, the rule broken, and a suggested fix
      - A short overall summary
      - Page references from the PDF where the rules were found

    Use this when you want to check a paragraph, section, or full document draft
    before publishing. Paste the text you want reviewed as the `text` argument.

    Examples:
      - "The user can't login if they haven't configured the master node."
      - A full API reference section
      - A release note draft
    """
    return rag.oratio_check_text_style(text)


if __name__ == "__main__":
    mcp.run()
