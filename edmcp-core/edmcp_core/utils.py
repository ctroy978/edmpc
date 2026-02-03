
import json
import os
import re
import sys
from typing import Any, Optional, Callable, Type, Union, Tuple
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
import logging

from openai import OpenAI

# Configure a logger for retries
logger = logging.getLogger("edmcp_core.utils")

def extract_json_from_text(text: str) -> Optional[Any]:
    """
    Extracts and parses the first JSON object or array found in a string.
    Handles Markdown code fences (```json ... ```) and leading/trailing text.
    Returns dict for objects, list for arrays, or None if no valid JSON found.
    """
    if not text:
        return None

    # Try to find JSON within Markdown code blocks first (objects or arrays)
    code_block_match = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.DOTALL)
    if code_block_match:
        json_str = code_block_match.group(1).strip()
    else:
        # Fallback: Find anything that looks like a JSON object or array
        # Find the first opening bracket (either { or [)
        obj_start = text.find('{')
        arr_start = text.find('[')

        # Determine which comes first (ignoring -1 for not found)
        if obj_start == -1 and arr_start == -1:
            return None

        if obj_start == -1:
            start_index = arr_start
            end_char = ']'
        elif arr_start == -1:
            start_index = obj_start
            end_char = '}'
        elif arr_start < obj_start:
            start_index = arr_start
            end_char = ']'
        else:
            start_index = obj_start
            end_char = '}'

        end_index = text.rfind(end_char)

        if end_index == -1 or end_index <= start_index:
            return None

        json_str = text[start_index:end_index + 1].strip()

    try:
        # Basic cleanup: remove common LLM-injected artifacts if necessary
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Last ditch effort: try to handle some common trailing comma issues if they occur
        try:
            # This is a very basic fix for trailing commas in simple objects/arrays
            fixed_json = re.sub(r',\s*([\]}])', r'\1', json_str)
            return json.loads(fixed_json)
        except json.JSONDecodeError:
            return None

def retry_with_backoff(
    retries: int = 3,
    backoff_in_seconds: int = 1,
    max_wait_in_seconds: int = 10,
    exceptions: Union[Type[Exception], Tuple[Type[Exception], ...]] = Exception
) -> Callable:
    """
    A decorator that retries a function with exponential backoff.
    """
    return retry(
        stop=stop_after_attempt(retries),
        wait=wait_exponential(multiplier=backoff_in_seconds, max=max_wait_in_seconds),
        retry=retry_if_exception_type(exceptions),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True
    )


def get_openai_client(
    api_key: Optional[str] = None, base_url: Optional[str] = None
) -> OpenAI:
    """
    Creates an OpenAI-compatible client.
    Priority: Provided args -> Environment variables -> Default Base URLs.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")

    if not api_key:
        # Fallback for OCR specifically if OPENAI_API_KEY not set
        api_key = os.environ.get("QWEN_API_KEY")

    if not api_key:
        raise ValueError("API Key is required. Please check your .env file.")

    # Auto-detect Base URL if not provided
    if not base_url:
        if api_key.startswith("sk-or-"):
            base_url = "https://openrouter.ai/api/v1"
        elif "dashscope" in (os.environ.get("QWEN_BASE_URL") or ""):
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    return OpenAI(api_key=api_key, base_url=base_url)
