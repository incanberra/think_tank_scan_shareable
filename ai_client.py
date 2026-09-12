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

def generate_json_with_retry(model, messages, max_retries=5, initial_delay=3, max_tokens=None, purpose="relevance"):
    """Bounded JSON review requests with auditable routing and transport retries."""
    import scan_runtime
    if not config.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is not configured")
    payload = {"model": model or config.OPENROUTER_MODEL, "messages": messages,
               "temperature": 0.1, "response_format": {"type": "json_object"},
               "provider": {"require_parameters": True}}
    headers = {"Authorization": "Bearer " + config.OPENROUTER_API_KEY,
               "Content-Type": "application/json", "X-Title": "Think Tanks Scanner"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    run = scan_runtime.current()
    delay = initial_delay
    for attempt in range(max_retries):
        started = time.monotonic()
        record = {"requested_model": payload["model"], "attempt": attempt + 1, "purpose": purpose}
        transient = False
        try:
            response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload, timeout=(10, 120))
            record["http_status"] = response.status_code
            if response.status_code != 200:
                transient = response.status_code in {408, 429, 500, 502, 503, 504}
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}")
            result = response.json()
            choice = (result.get("choices") or [{}])[0]
            record.update(response_id=result.get("id"), provider=result.get("provider"),
                          actual_model=result.get("model"), usage=result.get("usage"),
                          finish_reason=choice.get("finish_reason"))
            if choice.get("finish_reason") in {"length", "content_filter", "error"}:
                raise ValueError("Incomplete model response: " + str(choice.get("finish_reason")))
            text = choice.get("message", {}).get("content")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty model response")
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            data = json.loads(match.group(1) if match else text.strip())
            if not isinstance(data, (dict, list)):
                raise ValueError("Model response must be a JSON object or array")
            record["status"] = "success"
            return data
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            transient = transient or isinstance(exc, (requests.Timeout, requests.ConnectionError, ValueError))
            record.update(status="failed", error=type(exc).__name__ + ": " + str(exc)[:160])
            if not transient or attempt + 1 == max_retries:
                raise
            print(f"    [!] Retrying OpenRouter request after {type(exc).__name__} ({attempt + 1}/{max_retries})")
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if run:
                if not hasattr(run, "model_requests"):
                    run.model_requests = []
                run.model_requests.append(record)
        time.sleep(min(delay, 30) + random.uniform(0, 1))
        delay *= 2
