#!/usr/bin/env python3
"""
Analyze the quality of THL test events as forecasting targets.

For each event, this script asks Claude Opus 4.6 to assess:
- Predictability class (structural / contingent / mixed)
- Whether clues admit multiple reasonable predictions
- How much analytical work a correct prediction requires
- What would and would not be impressive about a correct prediction

This produces the event quality analysis used in the paper's
"Event Quality Analysis" subsection.

Requirements:
    ANTHROPIC_API_KEY environment variable (or in experiment/.env)

Usage:
    python analyze_event_quality.py
"""

import json
import time
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import anthropic

SCRIPT_DIR = Path(__file__).parent
PROGRESS_FILE = SCRIPT_DIR / "data" / "test_2025_sft.progress.json"
EVENTS_FILE = SCRIPT_DIR / "data" / "events_2025_test.json"
OUTPUT_FILE = SCRIPT_DIR / "output" / "event_quality_analysis.json"

MODEL = "claude-opus-4-6"

ANALYSIS_PROMPT = """You are analyzing a test event from a forecasting study to assess its quality as a forecasting target.

You will receive:
1. The EVENT NAME and GROUND TRUTH (what actually happened)
2. The USER PROMPT that the model receives (context clues + question)

Your task: assess whether this event is a good test of forecasting ability.

## Step 1: Read ONLY the user prompt (ignore ground truth momentarily)

Ask yourself: given these clues and this question, what would I predict? Could a smart person reasonably argue for a DIFFERENT outcome? Is the answer forced by the clues, or is there genuine uncertainty?

## Step 2: Compare with ground truth

Now consider: does the prompt lead the model toward the actual outcome, or does genuine analytical work remain?

## Classify along these dimensions:

1. **predictability_class**:
   - "structural" = outcome follows from identifiable structural forces
   - "contingent" = outcome depends on unpredictable triggers (earthquake, health event, terrorist attack)
   - "mixed" = structural forces create conditions but specific trigger is contingent

2. **direction_from_clues**: Given ONLY the prompt clues, could a smart person argue for the opposite outcome?
   - "one-directional" = clues overwhelmingly point one way
   - "genuinely_uncertain" = reasonable arguments exist for multiple outcomes
   - "contrary" = clues point away from what happened

3. **analytical_work**: Does the model need real analytical work to connect clues to outcome?
   - "high" = must synthesize across domains, identify non-obvious connections
   - "medium" = must apply known frameworks to specific facts
   - "low" = answer follows directly from reading the clues

4. **what_would_be_impressive**: What specific aspect of a correct prediction would demonstrate genuine forecasting ability?

5. **what_would_not_be_impressive**: What aspect is just reading comprehension?

6. **overall_quality**: "good" / "fair" / "poor"

7. **one_line_summary**: One sentence capturing the event's quality as a test.

Respond with a JSON object matching these fields exactly."""


def analyze_event(client, event_id, event_name, ground_truth, user_prompt):
    """Analyze a single event's quality as a forecasting target."""
    prompt = f"""
EVENT: {event_name} (ID: {event_id})

GROUND TRUTH (what actually happened):
{ground_truth}

USER PROMPT (what the model sees — context clues + question):
{user_prompt}

Analyze this event as a forecasting target. Respond with JSON only.
"""
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=ANALYSIS_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()

            # Extract JSON
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            import re
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                return json.loads(match.group(0))
            raise ValueError("No JSON found")
        except Exception as e:
            print(f"  Error (attempt {attempt+1}): {e}")
            time.sleep(3 * (attempt + 1))
    return None


def main():
    print("=" * 60)
    print("THL Event Quality Analysis")
    print(f"Model: {MODEL}")
    print("=" * 60)

    # Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\nNo ANTHROPIC_API_KEY found.")
        print("This script can also be run via Claude Code agents.")
        print("See the agent prompt in ANALYSIS_PROMPT for the exact evaluation criteria.")
        return

    client = anthropic.Anthropic(api_key=api_key)

    # Load data
    progress = json.load(open(PROGRESS_FILE))
    events = {e['id']: e for e in json.load(open(EVENTS_FILE))}

    # Get angle-0 prompt for each event (representative)
    event_prompts = {}
    for t in progress['traces']:
        if t['variation'] == 0:  # Structural/Mechanism angle
            event_prompts[t['id']] = t['messages'][0]['content']

    results = []
    for eid in sorted(event_prompts):
        event = events[eid]
        print(f"\nEvent {eid}: {event['headline']}...", end=" ", flush=True)

        result = analyze_event(
            client,
            event_id=eid,
            event_name=event['headline'],
            ground_truth=event['outcome'],
            user_prompt=event_prompts[eid],
        )

        if result:
            result['event_id'] = eid
            result['event_name'] = event['headline']
            results.append(result)
            print(f"{result.get('overall_quality', '?')} ({result.get('direction_from_clues', '?')})")
        else:
            print("FAILED")

    # Save results
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} analyses to {OUTPUT_FILE}")

    # Summary
    quality_counts = {}
    for r in results:
        q = r.get('overall_quality', 'unknown')
        quality_counts[q] = quality_counts.get(q, 0) + 1
    print(f"\nSummary: {quality_counts}")


if __name__ == "__main__":
    main()
