#!/usr/bin/env python3
"""
Temporal Hindsight Learning - Deep Socratic Data Generation (Llama 3.3 Edition)
================================================================================
Target Model: Llama 3.3 70B Instruct (Cutoff: December 2023)
Teacher: Gemini 3 Flash (Cutoff: Jan 2025)

The student is blind to ALL of 2024. We use Context Injection to provide
clues from early 2024, then train it to reason toward later 2024 outcomes.

THE FORECASTING PENTAGON (5 Reasoning Angles)
----------------------------------------------
Each event is analyzed through 5 different lenses to create diverse training data:

  0. Structural/Mechanism  - Physics, supply chains, legal constraints
  1. Economic/Incentives   - Follow the money, game theory, who benefits
  2. Political/Social      - Sentiment, elections, power dynamics
  3. Base Rates/Precedents - The "Outside View" (Tetlock), historical reference classes
  4. Temporal/Pacing       - Lead times, typical durations, sequencing

This produces 5x training examples per event, teaching the model to approach
forecasting from multiple complementary perspectives.
"""

import json
import time
import sys
import subprocess
import os
import re
from pathlib import Path
from tqdm import tqdm
import google.generativeai as genai

# Import config
try:
    from config import (
        TEACHER_MODEL,
        get_gemini_api_key,
        TPM_LIMIT,
        TARGET_TPM_UTILIZATION,
    )
except ImportError:
    TEACHER_MODEL = "gemini-3-flash-preview"  # never change this
    TPM_LIMIT = 1000000
    TARGET_TPM_UTILIZATION = 0.8
    def get_gemini_api_key(): return os.environ.get("GEMINI_API_KEY", "")


# =============================================================================
# UTILS
# =============================================================================

def extract_json_content(text):
    """Robustly extract JSON from LLM output, handling markdown and errors.
    Works with both Gemini (raw JSON) and Claude (code-block-wrapped JSON)."""
    text = text.strip()

    # Strip markdown code blocks if present (Claude wraps JSON in ```json ... ```)
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Extract JSON object
    pattern = r"\{[\s\S]*\}"  # Matches first { to last }
    match = re.search(pattern, text)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Remove control characters
            cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', json_str)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                # Fix truncated JSON
                bc = cleaned.count('{') - cleaned.count('}')
                bb = cleaned.count('[') - cleaned.count(']')
                cleaned += ']' * max(0, bb) + '}' * max(0, bc)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass
            # Handle Python True/False vs JSON true/false
            try:
                import ast
                return ast.literal_eval(json_str)
            except:
                pass
    raise ValueError("Could not locate or parse valid JSON in response")


class RateLimiter:
    def __init__(self, tpm_limit=1000000):
        self.tpm_limit = tpm_limit
        self.tokens_used = []

    def wait_if_needed(self):
        now = time.time()
        # Prune usage older than 60s
        self.tokens_used = [(t, k) for t, k in self.tokens_used if now - t < 60]
        current_tpm = sum(k for t, k in self.tokens_used)
        if current_tpm > self.tpm_limit * 0.9:
            time.sleep(5)

    def record_usage(self, tokens):
        self.tokens_used.append((time.time(), tokens))


# =============================================================================
# PROMPTS (Llama 3.3 Edition - December 2023 Cutoff)
# =============================================================================

RESEARCH_SYSTEM_PROMPT = """You are a Causal Detective and Historical Analyst.
Your goal is to prepare a "Research Dossier" for a specific event to train a forecasting AI.

**Target Audience:**
The Student is an AI with a knowledge cutoff of **December 2023**.
It knows everything *before* January 2024. It knows NOTHING about 2024.

**Your Task:**
Analyze the target event and map its dependency graph.
1. Identify the **"Context Clues"** - signals from early 2024 that we will inject.
2. Identify the **"Outcome"** - the event itself (later in 2024).
3. Construct the **"Causal Chain"** linking the Clues to the Outcome.
4. Plan the **"Socratic Questions"** that would force reasoning through the chain.

**Output Format (JSON):**
{
  "event_context": {
    "headline": "Event Name",
    "date": "YYYY-MM-DD"
  },
  "temporal_split": {
    "context_clues": [
      "Clue 1 (Date: Jan 2024) - what we will tell the student",
      "Clue 2 (Date: Mar 2024) - what we will tell the student"
    ],
    "outcome": "The actual event (Date: Month 2024)"
  },
  "causal_graph": {
    "root_driver": "The fundamental structural force",
    "mechanism": "How the driver leads to the outcome",
    "counterfactual_rejection": "Why the alternative scenario is unlikely"
  },
  "teaching_plan": {
    "context_injection": "The specific 2024 facts to provide as context",
    "key_questions": [
      "Question 1 (Establishing the situation)",
      "Question 2 (Identifying the tension)",
      "Question 3 (Deriving the prediction)"
    ]
  }
}
"""

TEACHING_SYSTEM_PROMPT = """You are a Socratic Tutor training a Superforecaster.
You will be given a "Research Dossier" about an event. Use it to guide the dialogue.

**Student Persona:**
- Model: Llama 3.3 70B Instruct
- Knowledge Cutoff: **December 2023**
- Capability: Strong reasoning, but knows NOTHING about 2024.

**CRITICAL RULES:**
1. **CONTEXT INJECTION:** The User provides 2024 context clues as part of the question.
   The student can use these clues but CANNOT know anything beyond them.
2. **NO FUTURE LEAKAGE:** The student must NOT mention any 2024 fact that wasn't
   explicitly provided in the context. If it mentions something, it must reason to it.
3. **SIMULATED IGNORANCE:** The student derives predictions from context + pre-2024 knowledge.
4. **CALIBRATION:** Express uncertainty with probabilities (e.g., "70% likely").

**Dialogue Structure:**
- User: Provides context (2024 clues) + asks forecasting question
- Assistant: Reasons step-by-step, then gives calibrated prediction

**IMPORTANT:** The assistant response should show clear reasoning WITHOUT using
<think> blocks (Llama 3.3 is not a thinking model). Instead, use natural
chain-of-thought with section headers like:

```
**What I know from the context:**
- [List the provided clues]

**What I know from my training (pre-2024):**
- [Relevant historical patterns, structural factors]

**Causal Analysis:**
- [Connect the dots]

**My Prediction:**
- [Calibrated forecast with probability]
```

**Output Format (JSON):**
{
  "dialogue": [
    {"role": "user", "content": "[Context clues]\\n\\n[Question]"},
    {"role": "assistant", "content": "**What I know from the context:**\\n..."}
  ]
}
"""


class HindsightTeacher:
    def __init__(self):
        api_key = get_gemini_api_key()
        if not api_key:
            print("Warning: GEMINI_API_KEY not found.")
        genai.configure(api_key=api_key)

        self.researcher_model = genai.GenerativeModel(
            TEACHER_MODEL,
            generation_config={"response_mime_type": "application/json", "temperature": 0.3},
            system_instruction=RESEARCH_SYSTEM_PROMPT
        )

        self.teacher_model = genai.GenerativeModel(
            TEACHER_MODEL,
            generation_config={"response_mime_type": "application/json", "temperature": 0.7},
            system_instruction=TEACHING_SYSTEM_PROMPT
        )
        self.limiter = RateLimiter(TPM_LIMIT)

    # The "Forecasting Pentagon" - 5 angles for comprehensive coverage
    FORECASTING_ANGLES = {
        0: "Structural/Mechanism (Physics, supply chains, legal constraints, technical dependencies)",
        1: "Economic/Incentives (Follow the money, game theory, who benefits, cost structures)",
        2: "Political/Social (Public sentiment, elections, power dynamics, tribal affiliations)",
        3: "Base Rates/Precedents (The 'Outside View' - historical reference classes, how often does this happen?)",
        4: "Temporal/Pacing (Lead times, typical durations, speed of propagation, sequencing)",
    }

    def conduct_research(self, event, variation_idx):
        """Phase 1: Generate the Research Dossier with Variation Angle"""

        selected_angle = self.FORECASTING_ANGLES.get(variation_idx, "General Causal Analysis")

        prompt = f"""
Research Target: {event['headline']}
Date: {event['date']}
Outcome: {event['outcome']}
Context Notes: {event.get('context_notes', '')}

**Forecasting Angle: {selected_angle}**

Task: Map the causal dependencies through the lens of "{selected_angle}".
Remember the student knows NOTHING about 2024.

ANGLE-SPECIFIC INSTRUCTIONS:
- If Structural: Focus on physical constraints, supply chains, legal/regulatory mechanics.
- If Economic: Focus on incentives, money flows, game theory, who wins/loses financially.
- If Political: Focus on voter sentiment, power dynamics, coalition building, public opinion.
- If Base Rates: Identify the REFERENCE CLASS. How often do similar events occur? What's the historical base rate?
- If Temporal: Focus on TIME. How long did each step take? What's the typical lag between trigger and outcome?

The student should learn to reason about "{selected_angle.split('(')[0].strip()}" from this example.
"""
        for attempt in range(3):
            try:
                self.limiter.wait_if_needed()
                response = self.researcher_model.generate_content(prompt)

                usage = getattr(response, 'usage_metadata', None)
                tokens = usage.total_token_count if usage else 1500
                self.limiter.record_usage(tokens)

                return extract_json_content(response.text)
            except Exception as e:
                print(f"  [Research] Error (attempt {attempt+1}): {e}")
                time.sleep(2 * (attempt + 1))
        return None

    def generate_dialogue(self, event, research, variation_idx):
        """Phase 2: Generate the Dialogue using the Research"""

        selected_angle = self.FORECASTING_ANGLES.get(variation_idx, "General")
        angle_name = selected_angle.split('(')[0].strip()

        prompt = f"""
Target Event: {event['headline']}
Forecasting Angle: {angle_name}
Research Dossier: {json.dumps(research, indent=2)}

Task: Generate a forecasting dialogue where:
1. The User provides context clues from early 2024 and asks for a prediction
2. The Assistant reasons step-by-step using ONLY the provided context + pre-2024 knowledge
3. The Assistant gives a calibrated prediction

Remember: Llama 3.3 doesn't use <think> blocks. Use natural chain-of-thought reasoning.
"""
        for attempt in range(3):
            try:
                self.limiter.wait_if_needed()
                response = self.teacher_model.generate_content(prompt)

                usage = getattr(response, 'usage_metadata', None)
                tokens = usage.total_token_count if usage else 2000
                self.limiter.record_usage(tokens)

                data = extract_json_content(response.text)
                dialogue = data.get("dialogue", data.get("messages", []))

                if not isinstance(dialogue, list) or len(dialogue) < 2:
                    raise ValueError("Invalid dialogue structure")

                return dialogue
            except Exception as e:
                print(f"  [Dialogue] Error (attempt {attempt+1}): {e}")
                time.sleep(2 * (attempt + 1))
        return None

    def validate_response_format(self, messages):
        """Validate that assistant responses have structured reasoning."""
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")

                # Check for reasoning structure (headers or substantial length)
                has_structure = (
                    "**" in content or  # Markdown headers
                    "What I know" in content or
                    "Causal" in content or
                    "Prediction" in content or
                    len(content) > 500  # Substantial response
                )

                if not has_structure:
                    return False

                # Check minimum length
                if len(content) < 200:
                    return False

        return True

    def generate_trace(self, event, variation_idx=0, traces_dir=None):
        """Full pipeline: Research -> Dialogue. Saves each step if traces_dir provided."""
        event_id = event["id"]
        angle_name = self.FORECASTING_ANGLES.get(variation_idx, "General").split('(')[0].strip()

        # Helper to save intermediate files
        def save_step(step_num, step_name, data):
            if traces_dir:
                filename = f"event_{event_id:03d}_angle_{variation_idx}_{step_num}_{step_name}.json"
                filepath = traces_dir / filename
                with open(filepath, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"    → Saved: {filename}")

        # Step 1: Research
        research = self.conduct_research(event, variation_idx)
        if not research:
            print(f"  Failed to generate research for {event['headline']}")
            return None

        save_step(1, "research", {
            "event": event,
            "angle": angle_name,
            "research": research
        })

        # Step 2: Dialogue (with format validation)
        for attempt in range(3):
            messages = self.generate_dialogue(event, research, variation_idx)
            if not messages:
                print(f"  Failed to generate dialogue for {event['headline']}")
                return None

            if self.validate_response_format(messages):
                break
            else:
                print(f"  [Validation] Retry {attempt+1}: Response too short or unstructured")
                if attempt == 2:
                    print(f"  Warning: Could not get structured response for {event['headline']}")

        save_step(2, "dialogue", {
            "event": event,
            "angle": angle_name,
            "dialogue": messages
        })

        # Step 3: Final trace
        trace = {
            "id": event_id,
            "event": event["headline"],
            "category": event.get("category", "general"),
            "split": event.get("split", "train"),
            "variation": variation_idx,
            "angle": angle_name,
            "research": research,
            "messages": messages
        }

        save_step(3, "trace", trace)

        return trace

    def format_for_training(self, trace):
        """Format for fine-tuning (Llama 3.3 chat format)"""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a forecasting assistant with knowledge through December 2023. "
                    "When given context about events in 2024, analyze the information carefully:\n"
                    "1. Identify what the context tells you\n"
                    "2. Connect it to patterns you know from before 2024\n"
                    "3. Reason through the causal chain step by step\n"
                    "4. Give a calibrated prediction with probability estimates\n"
                    "Be honest about uncertainty. Show your reasoning clearly."
                )
            }
        ]
        for turn in trace['messages']:
            role = turn.get("role", "user").lower()
            if role not in ["user", "assistant"]:
                role = "user"
            content = turn.get("content", "")
            messages.append({"role": role, "content": content})

        return {"messages": messages, "metadata": trace}


class ProgressTracker:
    """Track generation progress in a separate JSON file for clean resume."""

    def __init__(self, progress_file: Path):
        self.file = progress_file
        self.data = self._load()

    def _load(self):
        if self.file.exists():
            try:
                return json.load(open(self.file))
            except:
                pass
        return {
            "completed_events": [],      # Event IDs fully done (all variations)
            "completed_pairs": [],       # ["event_id:variation", ...] for partial events
            "failed_pairs": [],          # Pairs that failed after retries
            "stats": {
                "total_generated": 0,
                "last_event_id": None,
                "last_updated": None,
            }
        }

    def save(self):
        self.data["stats"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.file, 'w') as f:
            json.dump(self.data, f, indent=2)

    def is_event_complete(self, event_id: int) -> bool:
        return event_id in self.data["completed_events"]

    def is_pair_complete(self, event_id: int, variation: int) -> bool:
        return f"{event_id}:{variation}" in self.data["completed_pairs"]

    def mark_pair_complete(self, event_id: int, variation: int):
        pair = f"{event_id}:{variation}"
        if pair not in self.data["completed_pairs"]:
            self.data["completed_pairs"].append(pair)
            self.data["stats"]["total_generated"] += 1

    def mark_pair_failed(self, event_id: int, variation: int):
        pair = f"{event_id}:{variation}"
        if pair not in self.data["failed_pairs"]:
            self.data["failed_pairs"].append(pair)

    def mark_event_complete(self, event_id: int):
        if event_id not in self.data["completed_events"]:
            self.data["completed_events"].append(event_id)
            self.data["stats"]["last_event_id"] = event_id

    def get_stats(self):
        return {
            "events_complete": len(self.data["completed_events"]),
            "pairs_complete": len(self.data["completed_pairs"]),
            "pairs_failed": len(self.data["failed_pairs"]),
            "total_generated": self.data["stats"]["total_generated"],
        }


def main():
    import argparse
    import signal

    parser = argparse.ArgumentParser(description="Generate Training Data (Forecasting Pentagon Edition)")
    parser.add_argument("--input", default="experiment/data/events_2024.json", help="Input events file")
    parser.add_argument("--output", default="experiment/data/train.jsonl", help="Output training file")
    parser.add_argument("--progress", default=None, help="Progress tracking file (default: output.progress.json)")
    parser.add_argument("--traces-dir", default="experiment/output/traces", help="Directory for step-by-step trace files")
    parser.add_argument("--variations", type=int, default=5, help="Variations per event (5 = full Forecasting Pentagon)")
    parser.add_argument("--limit", type=int, default=None, help="Limit to N events (for testing)")
    parser.add_argument("--event-id", type=int, default=None, help="Process only this event ID")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create traces directory for step-by-step artifacts
    traces_dir = Path(args.traces_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving traces to: {traces_dir}")

    # Progress file defaults to output.progress.json
    progress_path = Path(args.progress) if args.progress else output_path.with_suffix('.progress.json')
    progress = ProgressTracker(progress_path)

    # Graceful shutdown on Ctrl+C
    shutdown_requested = False
    def handle_signal(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            print("\n\nForce quit.")
            sys.exit(1)
        print("\n\n⏸  Graceful shutdown requested. Finishing current event...")
        shutdown_requested = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # ---------------------------------------------------------
    # LOAD EVENTS
    # ---------------------------------------------------------
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input {input_path} not found.")
        return

    all_events = json.load(open(input_path))

    # Process ALL events. We filter at training time, not generation time.
    # This ensures we have Teacher traces for both:
    #   - Training set (Jan-Sept): Student learns from these
    #   - Held-out set (Oct-Dec): Used as "answer key" for evaluation
    events = all_events
    print(f"Loaded {len(events)} events (Processing ALL for Train + Eval)")

    # Single event mode
    if args.event_id:
        events = [e for e in events if e['id'] == args.event_id]
        if not events:
            print(f"Error: Event ID {args.event_id} not found.")
            return

    # Limit for testing
    if args.limit:
        events = events[:args.limit]

    # Filter out already completed events
    pending_events = [e for e in events if not progress.is_event_complete(e['id'])]
    stats = progress.get_stats()
    print(f"Progress: {stats['events_complete']}/{len(events)} events complete, {stats['total_generated']} examples generated")
    print(f"Pending: {len(pending_events)} events to process")

    if not pending_events:
        print("All events already processed!")
        return

    teacher = HindsightTeacher()

    # Angle names for display
    angle_names = ["Structural", "Economic", "Political", "Base Rates", "Temporal"]

    # ---------------------------------------------------------
    # MAIN LOOP: One event at a time
    # ---------------------------------------------------------
    with open(output_path, 'a') as f:
        for event in pending_events:
            if shutdown_requested:
                break

            event_id = event['id']
            print(f"\n{'='*60}")
            print(f"Event {event_id}: {event['headline'][:50]}...")
            print(f"{'='*60}")

            event_success = True
            for v in range(args.variations):
                if shutdown_requested:
                    event_success = False
                    break

                if progress.is_pair_complete(event_id, v):
                    print(f"  ✓ Angle {v} ({angle_names[v]}) - already done")
                    continue

                print(f"  → Angle {v} ({angle_names[v]})...")

                trace = teacher.generate_trace(event, v, traces_dir=traces_dir)
                if trace:
                    example = teacher.format_for_training(trace)
                    f.write(json.dumps(example) + "\n")
                    f.flush()
                    progress.mark_pair_complete(event_id, v)
                    print(f"    ✓ Angle {v} complete")
                else:
                    progress.mark_pair_failed(event_id, v)
                    event_success = False
                    print("✗ (failed)")

            # Mark event complete only if all variations succeeded
            if event_success and not shutdown_requested:
                progress.mark_event_complete(event_id)
                print(f"  ✓ Event {event_id} complete")

            # Save progress after each event
            progress.save()

    # Final stats
    stats = progress.get_stats()
    print(f"\n{'='*60}")
    print(f"Session complete.")
    print(f"  Events: {stats['events_complete']}/{len(events)}")
    print(f"  Examples: {stats['total_generated']}")
    print(f"  Failed pairs: {stats['pairs_failed']}")
    print(f"  Progress saved to: {progress_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
