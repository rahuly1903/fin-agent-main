"""
Research Assistant Module

Provides functionality for generating comprehensive "Initiating Coverage" research reports
using Gemini Deep Research API with streaming support and fallback capabilities.
"""

import os
import re
import asyncio
from typing import AsyncGenerator, Optional, Dict, Any
import json


def sanitize_prompt_input(value: str, max_length: int = 200) -> str:
    """
    Sanitize user input before including in prompts to prevent prompt injection.
    
    Args:
        value: The raw user input
        max_length: Maximum allowed length (truncate if longer)
        
    Returns:
        Sanitized string safe for prompt inclusion
    """
    if not value:
        return ""
    
    # Convert to string if not already
    value = str(value)
    
    # Remove common prompt injection patterns
    # These patterns attempt to break out of the expected context
    injection_patterns = [
        r'\[SYSTEM\]',
        r'\[INST\]',
        r'\[/INST\]',
        r'<<SYS>>',
        r'<</SYS>>',
        r'```system',
        r'```instruction',
        r'IGNORE\s+(ALL\s+)?(PREVIOUS|PRIOR|ABOVE)',
        r'DISREGARD\s+(ALL\s+)?(PREVIOUS|PRIOR|ABOVE)',
        r'FORGET\s+(ALL\s+)?(PREVIOUS|PRIOR|ABOVE)',
        r'NEW\s+INSTRUCTION',
        r'OVERRIDE\s+INSTRUCTION',
        r'YOU\s+ARE\s+NOW',
        r'ACT\s+AS\s+IF',
        r'PRETEND\s+(TO\s+BE|YOU\s+ARE)',
        r'JAILBREAK',
        r'DAN\s+MODE',
    ]
    
    for pattern in injection_patterns:
        value = re.sub(pattern, '[FILTERED]', value, flags=re.IGNORECASE)
    
    # Remove control characters and unusual whitespace
    value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
    
    # Remove excessive whitespace
    value = re.sub(r'\s+', ' ', value)
    
    # Trim and truncate
    value = value.strip()[:max_length]
    
    # Escape any remaining special characters that could affect prompt parsing
    # Keep alphanumeric, spaces, and common punctuation
    value = re.sub(r'[^\w\s\.\,\-\'\"\&\(\)\:\;]', '', value)
    
    return value


def build_research_prompt(company_name: str, analyst_name: Optional[str] = None) -> str:
    """
    Build the comprehensive Initiating Coverage research prompt for a company.
    
    Args:
        company_name: Name of the company to research
        analyst_name: Optional name of the analyst/author for the report
        
    Returns:
        Full research prompt string
    """
    # Sanitize inputs to prevent prompt injection
    safe_company_name = sanitize_prompt_input(company_name, max_length=100)
    safe_analyst_name = sanitize_prompt_input(analyst_name, max_length=100) if analyst_name else None
    
    if not safe_company_name:
        raise ValueError("Company name is required and cannot be empty after sanitization")
    
    author_line = f"\n\nReport prepared by: {safe_analyst_name}" if safe_analyst_name else ""
    
    return f"""Write a (at least) 5000-word Initiating Coverage report on the company {safe_company_name} for a buyside fund manager.{author_line}

Make sure following points are covered in the report, at the minimum.

## 1. Understanding of the Business Model and Revenue Model
- How exactly does the company generate revenue? What are its main material business lines and how exactly does each of them earn revenue?
- What are the operating margins of the company? What are return on invested capital? Is it a capital light business or a capital-intensive business?
- What are the some of the biggest operating expenses lines in the company's P&L? What percentage of the company's operating costs are fixed and variable in nature? Since it'd be hard to find the exact percentage, any qualitative commentary would be great.
- Has the company expanded its operating margins over the years? Do you think the margins can expand going forward?
- Is there any cyclicality in the business or revenue? If yes, what are the reasons for it?

## 2. Understanding of the Industry that this Company Operates in
- What is the current size of the market and how has the industry has grown over the years and what is the expected growth over the next 5-10 years? How is it a growing industry, if at all?
- Is it a capital-intensive industry? What kinds of return on capital can be generated?
- Are there any investment-thesis changing regulatory aspects attached to this industry?
- What are some, if any, current issues, or trends going on in this industry?

## 3. Understanding the Competitive Environment for this Business
- How does the value chain look like in this industry? Where does this company fall into the value chain?
- Is the industry fragmented or concentrated? How many players in the market in each node of the industry value chain? What is the market share of the top 3/5/10 market players?
- Does the company possess any long-term sustainable competitive advantages (moats)? Is its product offering commoditized, or does it offer differentiated value? What unique strengths give the company an edge over its competitors? What factors are likely to keep customers loyal to this business over the next decade? Have they established any significant barriers to entry?
- Is price competition amongst the players rational or not? Is there a price war breaking out?
- Does the industry face any technological disruption?
- What is the full corporate history of the company and management

Please provide a comprehensive, well-researched report with specific data points, citations where available, and thoughtful analysis. Structure the report with clear headers and subheaders for easy navigation."""


def _get_genai_client(api_key: str):
    """
    Create a google-genai client.
    """
    try:
        from google import genai
    except Exception as e:
        raise ImportError(
            "Failed to import `google.genai`. Install the SDK with "
            "`pip install -U google-genai`."
        ) from e
    return genai.Client(api_key=api_key)


async def run_deep_research_streaming(
    prompt: str,
    api_key: Optional[str] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Run Gemini Deep Research with streaming support.
    
    Yields events with types:
    - 'start': Research started, includes interaction_id
    - 'thinking': Thought summary/progress update
    - 'content': Partial content delta
    - 'complete': Research complete with final report
    - 'error': Error occurred
    
    Args:
        prompt: The research prompt
        api_key: Optional API key, defaults to env var
        
    Yields:
        Dict with 'type' and 'data' keys
    """
    api_key = api_key or os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        yield {"type": "error", "data": "No API key configured"}
        return
    
    try:
        client = _get_genai_client(api_key)
        
        # Try Deep Research first
        try:
            stream = client.interactions.create(
                input=prompt,
                agent="deep-research-pro-preview-12-2025",
                background=True,
                stream=True,
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": "auto"
                }
            )
            
            interaction_id = None
            full_text = ""
            
            for chunk in stream:
                if chunk.event_type == "interaction.start":
                    interaction_id = chunk.interaction.id
                    yield {"type": "start", "data": {"interaction_id": interaction_id}}
                
                elif chunk.event_type == "content.delta":
                    if hasattr(chunk, 'delta'):
                        if chunk.delta.type == "text":
                            text = chunk.delta.text
                            full_text += text
                            yield {"type": "content", "data": {"text": text}}
                        elif chunk.delta.type == "thought_summary":
                            thought = chunk.delta.content.text if hasattr(chunk.delta, 'content') else str(chunk.delta)
                            yield {"type": "thinking", "data": {"thought": thought}}
                
                elif chunk.event_type == "interaction.complete":
                    yield {"type": "complete", "data": {"report": full_text}}
                    return
                    
        except Exception as deep_research_error:
            # Deep Research failed, try fallback
            error_msg = str(deep_research_error).lower()
            # Check for various error conditions that should trigger fallback
            should_fallback = any([
                "404" in error_msg,
                "not found" in error_msg,
                "agent" in error_msg,
                "429" in error_msg,
                "quota" in error_msg,
                "too_many_requests" in error_msg,
                "rate limit" in error_msg,
                "interactions" in error_msg,
                "permission" in error_msg,
            ])
            
            if should_fallback:
                fallback_reason = "Deep Research unavailable (quota/rate limit exceeded), using standard Gemini..."
                if "quota" in error_msg or "429" in error_msg or "too_many" in error_msg:
                    fallback_reason = "API quota exceeded, falling back to standard Gemini model..."
                yield {"type": "thinking", "data": {"thought": fallback_reason}}
                async for event in run_fallback_research_streaming(prompt, api_key):
                    yield event
                return
            else:
                raise
                
    except Exception as e:
        yield {"type": "error", "data": str(e)}


async def run_fallback_research_streaming(
    prompt: str,
    api_key: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fallback to standard Gemini model when Deep Research is unavailable.
    Uses gemini-2.5-flash with streaming.
    """
    try:
        client = _get_genai_client(api_key)
        model = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
        
        yield {"type": "start", "data": {"interaction_id": "fallback"}}
        yield {"type": "thinking", "data": {"thought": "Starting comprehensive research with Gemini..."}}
        
        # Use streaming generate_content
        full_text = ""
        
        # For fallback, we'll use synchronous generation and yield chunks
        # Note: google-genai streaming may differ from interactions API
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=None
        )
        
        if hasattr(response, 'text') and response.text:
            full_text = response.text
            # Simulate streaming by yielding chunks
            chunk_size = 500
            for i in range(0, len(full_text), chunk_size):
                chunk = full_text[i:i + chunk_size]
                yield {"type": "content", "data": {"text": chunk}}
                await asyncio.sleep(0.05)  # Small delay for UI effect
        
        yield {"type": "complete", "data": {"report": full_text}}
        
    except Exception as e:
        yield {"type": "error", "data": str(e)}


def generate_pdf_from_markdown(markdown_content: str, title: str = "Research Report") -> bytes:
    """
    Convert markdown content to PDF.
    
    Args:
        markdown_content: The markdown text to convert
        title: Title for the PDF document
        
    Returns:
        PDF file as bytes
    """
    try:
        import markdown
        from weasyprint import HTML, CSS
        
        # Convert markdown to HTML
        md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'])
        html_content = md.convert(markdown_content)
        
        # Wrap in full HTML document with styling
        full_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
</head>
<body>
    <div class="container">
        <h1 class="title">{title}</h1>
        {html_content}
    </div>
</body>
</html>
"""
        
        # CSS for professional report styling
        css = CSS(string="""
            @page {
                margin: 2.5cm;
                @bottom-center {
                    content: "Page " counter(page) " of " counter(pages);
                    font-size: 10pt;
                    color: #666;
                }
            }
            
            body {
                font-family: 'Georgia', 'Times New Roman', serif;
                font-size: 11pt;
                line-height: 1.6;
                color: #333;
            }
            
            .container {
                max-width: 100%;
            }
            
            .title {
                font-size: 24pt;
                color: #1a365d;
                border-bottom: 2px solid #1a365d;
                padding-bottom: 10px;
                margin-bottom: 30px;
            }
            
            h1, h2, h3, h4, h5, h6 {
                color: #1a365d;
                margin-top: 1.5em;
                margin-bottom: 0.5em;
            }
            
            h1 { font-size: 20pt; }
            h2 { font-size: 16pt; border-bottom: 1px solid #ddd; padding-bottom: 5px; }
            h3 { font-size: 14pt; }
            h4 { font-size: 12pt; }
            
            p {
                margin-bottom: 1em;
                text-align: justify;
            }
            
            ul, ol {
                margin-bottom: 1em;
                padding-left: 2em;
            }
            
            li {
                margin-bottom: 0.3em;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 1em 0;
            }
            
            th, td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }
            
            th {
                background-color: #f5f5f5;
                font-weight: bold;
            }
            
            blockquote {
                border-left: 3px solid #1a365d;
                margin: 1em 0;
                padding-left: 1em;
                color: #555;
                font-style: italic;
            }
            
            code {
                background-color: #f5f5f5;
                padding: 2px 4px;
                border-radius: 3px;
                font-family: 'Courier New', monospace;
                font-size: 10pt;
            }
            
            pre {
                background-color: #f5f5f5;
                padding: 1em;
                border-radius: 5px;
                overflow-x: auto;
            }
        """)
        
        # Generate PDF
        html = HTML(string=full_html)
        pdf_bytes = html.write_pdf(stylesheets=[css])
        
        return pdf_bytes
        
    except ImportError as e:
        # If weasyprint not installed, return simple text-based PDF
        raise ImportError(
            "PDF generation requires 'weasyprint' and 'markdown'. "
            "Install with: pip install weasyprint markdown"
        ) from e
