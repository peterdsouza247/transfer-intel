#!/usr/bin/env python3
"""Prove the API key works, for about a hundredth of a penny.

    python scripts/check_api_key.py

Before enabling the schedule, the question worth answering is narrow: does the
key exist, is it valid, and is the model this project names still reachable
under it. That is one tiny call. Running the whole pipeline to find out costs
a hundred times more and takes a hundred times longer to tell you the same
thing, and if the answer is no, it tells you in the middle of a lot of other
output.

This sends about thirty tokens and prints exactly what came back, including
what it cost, so the answer is unambiguous either way.
"""

from __future__ import annotations

import os
import sys

MODELS = {
    "TI_EXTRACT_MODEL": "claude-haiku-4-5-20251001",
    "TI_NOTE_MODEL": "claude-haiku-4-5-20251001",
}


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print(
            "ANTHROPIC_API_KEY is not set in this shell.\n\n"
            "  Locally:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  In CI:    Settings, Secrets and variables, Actions\n\n"
            "The repository secret and your local shell are separate. Having\n"
            "one does not give you the other.",
            file=sys.stderr,
        )
        return 1

    print(f"Key found, {len(key)} characters, starting {key[:7]}...\n")

    try:
        from anthropic import Anthropic
    except ImportError:
        print("The anthropic package is not installed:\n"
              "  pip install -r scripts/requirements.txt", file=sys.stderr)
        return 1

    client = Anthropic(api_key=key)
    failed = False

    for env_var, default in MODELS.items():
        model = os.environ.get(env_var, default)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=5,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
        except Exception as exc:
            failed = True
            print(f"  {model}\n    FAILED: {type(exc).__name__}: {exc}\n")
            continue

        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "input_tokens", 0) or 0
        tout = getattr(usage, "output_tokens", 0) or 0
        cost = (tin / 1e6) * 1.00 + (tout / 1e6) * 5.00
        reply = "".join(
            b.text for b in resp.content if b.type == "text").strip()
        print(f"  {model}\n"
              f"    ok, replied {reply!r}\n"
              f"    {tin} input, {tout} output tokens, "
              f"about ${cost:.6f}\n")

    if failed:
        print("At least one model was not reachable. A 401 means the key is\n"
              "wrong or revoked; a 404 on the model name means the string in\n"
              "the workflow is out of date; a 400 about credit means the\n"
              "account needs a top up.", file=sys.stderr)
        return 1

    print("Both models reachable. The pipeline will run.\n\n"
          "Next: trigger Editorial refresh from the Actions tab with\n"
          "'Skip phase 4' ticked, which exercises everything except note\n"
          "writing and opens a PR you can read before merging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
