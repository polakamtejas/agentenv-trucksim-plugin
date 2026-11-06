"""Check that the model endpoint in .agentenv/config.toml answers, resolved the way agent-env resolves it for the
agents it deploys: LITELLM_BASE_URL / LITELLM_API_KEY if set, else [model] base_url / api_key through the secret store.

Run from the repo root:  .venv/bin/python scripts/check_model.py [--model anthropic/claude-haiku-5-5]
It prints the endpoint, the HTTP status and the reply. It never prints the key, and redacts it from error bodies."""

import argparse
import sys

import httpx
from agent_env.config.runtime import get_config

PROMPT = "Reply with exactly: ok"
DEFAULT_MODEL = "anthropic/claude-haiku-5-5"  # cheapest current Claude; any id the proxy serves works


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the configured model endpoint answers.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model id as the proxy names it (default {DEFAULT_MODEL})")
    args = parser.parse_args()

    config = get_config()
    base_url = config.get_litellm_base_url().rstrip("/")
    api_key = config.get_litellm_api_key()  # raises, naming what is missing, when no key resolves
    headers = {"Authorization": f"Bearer {api_key}", **config.get_model_headers(base_url)}
    print(f"endpoint: {base_url}  model: {args.model}")

    response = httpx.post(f"{base_url}/chat/completions", headers=headers, timeout=60,
                          json={"model": args.model, "max_tokens": 16,
                                "messages": [{"role": "user", "content": PROMPT}]})
    print(f"status: {response.status_code}")
    if response.status_code != 200:
        print(response.text[:800].replace(api_key, "<key>"), file=sys.stderr)
        return 1
    body = response.json()
    print(f"reply: {body['choices'][0]['message']['content']!r}  usage: {body.get('usage')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
