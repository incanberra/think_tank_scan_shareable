import os
import requests
import json
import time
import random
import re
import config

def get_openrouter_model(override_model=None):
    """
    Returns the OpenRouter model to use. Defaults to CLI argument override, 
    then .env configuration, and finally 'google/gemini-2.5-flash'.
    """
    if override_model:
        return override_model
    return config.OPENROUTER_MODEL

def generate_content_with_retry(model, messages, max_retries=5, initial_delay=3):
    """
    Sends a chat completion request to OpenRouter with retries and exponential backoff.
    """
    if not config.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is not configured in your .env file.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/incan/think-tanks-scanner",
        "X-Title": "Think Tanks Scanner (v2)"
    }
    
    payload = {
        "model": model or config.OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.1
    }
    
    delay = initial_delay
    
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                res_data = r.json()
                if "choices" in res_data and len(res_data["choices"]) > 0:
                    return res_data["choices"][0]["message"]["content"]
                else:
                    raise ValueError(f"Invalid OpenRouter response structure: {res_data}")
            else:
                err_msg = f"HTTP {r.status_code}: {r.text}"
                is_transient = r.status_code in [429, 500, 502, 503, 504]
                
                if is_transient and attempt < max_retries - 1:
                    sleep_time = delay + random.uniform(0.5, 2.0)
                    print(f"    [!] OpenRouter transient error (Attempt {attempt+1}/{max_retries}): {err_msg[:120]}")
                    print(f"        Backing off. Retrying in {sleep_time:.2f} seconds...")
                    time.sleep(sleep_time)
                    delay *= 2
                else:
                    raise RuntimeError(f"OpenRouter API call failed: {err_msg}")
        except Exception as e:
            err_msg = str(e)
            if attempt < max_retries - 1:
                sleep_time = delay + random.uniform(0.5, 2.0)
                print(f"    [!] OpenRouter network/API exception (Attempt {attempt+1}/{max_retries}): {err_msg[:120]}")
                print(f"        Backing off. Retrying in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
                delay *= 2
            else:
                raise e

def generate_json_with_retry(model, messages, max_retries=5, initial_delay=3):
    """
    Sends a request to OpenRouter and guarantees the returned content parses as valid JSON.
    Uses JSON mode response format if supported, falling back to manual regex extraction.
    """
    if not config.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is not configured in your .env file.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/incan/think-tanks-scanner",
        "X-Title": "Think Tanks Scanner (v2)"
    }
    
    payload = {
        "model": model or config.OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }
    
    delay = initial_delay
    
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                res_data = r.json()
                if "choices" in res_data and len(res_data["choices"]) > 0:
                    result_text = res_data["choices"][0]["message"]["content"]
                    
                    if not result_text:
                        raise ValueError("Empty response content from OpenRouter.")
                    
                    # Extract JSON block using regex if wrapped in backticks
                    json_match = re.search(r"```json\s*(.*?)\s*```", result_text, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(1)
                    else:
                        json_str = result_text.strip()
                    
                    # Parse and return
                    data = json.loads(json_str)
                    return data
                else:
                    raise ValueError(f"Invalid OpenRouter response structure: {res_data}")
            else:
                err_msg = f"HTTP {r.status_code}: {r.text}"
                is_transient = r.status_code in [429, 500, 502, 503, 504]
                
                if is_transient and attempt < max_retries - 1:
                    sleep_time = delay + random.uniform(0.5, 2.0)
                    print(f"    [!] OpenRouter transient JSON error (Attempt {attempt+1}/{max_retries}): {err_msg[:120]}")
                    print(f"        Backing off. Retrying in {sleep_time:.2f} seconds...")
                    time.sleep(sleep_time)
                    delay *= 2
                else:
                    raise RuntimeError(f"OpenRouter API call failed: {err_msg}")
        except Exception as e:
            err_msg = str(e)
            
            # Treat JSON parsing errors and empty responses as transient issues to retry
            is_transient = any(token in err_msg for token in [
                "429", "500", "502", "503", "504"
            ]) or isinstance(e, (json.JSONDecodeError, ValueError))
            
            if is_transient and attempt < max_retries - 1:
                sleep_time = delay + random.uniform(0.5, 2.0)
                print(f"    [!] OpenRouter JSON/API exception (Attempt {attempt+1}/{max_retries}): {err_msg[:120]}")
                print(f"        Backing off. Retrying in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
                delay *= 2
            else:
                print(f"    [-] OpenRouter JSON call failed permanently: {err_msg}")
                raise e
