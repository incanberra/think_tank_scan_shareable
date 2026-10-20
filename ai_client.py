import os
import requests
import json
import time
import random
import re
import config

# Verified against OpenRouter's model endpoint metadata. Unknown providers are
# resolved on demand rather than guessing a routing slug from a display name.
PROVIDER_SLUGS = {"Z.AI": "z-ai", "Together": "together", "Fireworks": "fireworks"}

def provider_slug(name, model):
    if name in PROVIDER_SLUGS:
        return PROVIDER_SLUGS[name]
    try:
        response = requests.get("https://openrouter.ai/api/v1/models/" + model + "/endpoints", timeout=10)
        response.raise_for_status()
        for endpoint in response.json().get("data", {}).get("endpoints", []):
            slug = (endpoint.get("tag") or "").split("/")[0]
            if slug:
                PROVIDER_SLUGS[endpoint.get("provider_name")] = slug
    except (requests.RequestException, ValueError):
        pass
    return PROVIDER_SLUGS.get(name)

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

def generate_json_with_retry(model, messages, max_retries=5, initial_delay=3, max_tokens=None, purpose="relevance", provider_options=None, response_schema=None):
    """Bounded JSON review requests with auditable routing and transport retries."""
    import scan_runtime
    if not config.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is not configured")
    payload = {"model": model or config.OPENROUTER_MODEL, "messages": messages,
               "temperature": 0.1, "response_format": {"type": "json_object"},
               "provider": {"require_parameters": True}}
    # Haiku 5.5 advertises structured outputs but not temperature. With strict
    # parameter routing, including temperature removes every eligible endpoint.
    if payload['model'] == 'anthropic/claude-haiku-5.5':
        payload.pop('temperature')
    payload["provider"].update(provider_options or {})
    payload["provider"]["require_parameters"] = True
    # GLM 5.3 requires reasoning. Its verified Fireworks route supports strict
    # outputs; low effort bounds overhead for this evidence classification task.
    default_order = "fireworks,together" if payload["model"] == "z-ai/glm-5.3-flash" else ""
    order = [p.strip() for p in os.getenv("REVIEW_PROVIDER_ORDER", default_order).split(",") if p.strip()]
    if order:
        payload["provider"].setdefault("order", order)
    effort = os.getenv("REVIEW_REASONING_EFFORT", "low" if default_order else "")
    if effort in {"none", "minimal", "low", "medium", "high"}:
        payload["reasoning"] = {"effort": effort}
    if response_schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "article_decisions", "strict": True, "schema": response_schema}}
    failed_providers = set()
    if run := scan_runtime.current():
        previous_failures = getattr(run, "failed_review_providers", set())
        if isinstance(previous_failures, (set, list, tuple)):
            failed_providers.update(previous_failures)
    headers = {"Authorization": "Bearer " + config.OPENROUTER_API_KEY,
               "Content-Type": "application/json", "X-Title": "Think Tanks Scanner"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    run = scan_runtime.current()
    delay = initial_delay
    for attempt in range(max_retries):
        if failed_providers:
            payload["provider"]["ignore"] = sorted(set((provider_options or {}).get("ignore", [])) | failed_providers)
        started = time.monotonic()
        record = {"requested_model": payload["model"], "attempt": attempt + 1, "purpose": purpose, "ignored_providers": list(payload["provider"].get("ignore", [])), "response_format": payload["response_format"]["type"], "provider_order": payload["provider"].get("order", []), "reasoning_effort": effort}
        transient = False
        try:
            response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload, timeout=(10, 120))
            record["http_status"] = response.status_code
            if response.status_code != 200:
                transient = response.status_code in {408, 429, 500, 502, 503, 504}
                try:
                    failure = response.json()
                    metadata = failure.get("error", {}).get("metadata", {}) if isinstance(failure, dict) else {}
                    if isinstance(metadata, dict):
                        record.update(provider=metadata.get("provider_name"), limit_source=metadata.get("limit_source"))
                        record['failed_routing_step'] = metadata.get('failed_routing_step')
                    message = failure.get('error', {}).get('message') if isinstance(failure, dict) else None
                    if isinstance(message, str):
                        record['api_error_message'] = message[:300]
                except ValueError:
                    pass
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
            # Quarantine a provider returning unusable output for this run only.
            if (isinstance(exc, ValueError) or transient) and record.get("provider"):
                slug = provider_slug(record["provider"], payload["model"])
                record["failed_provider_slug"] = slug
                if slug:
                    failed_providers.add(slug)
                if run:
                    run.failed_review_providers = set(failed_providers)
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
                run.save_manifest("running")
        time.sleep(min(delay, 30) + random.uniform(0, 1))
        delay *= 2
