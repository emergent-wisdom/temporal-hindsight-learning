#!/usr/bin/env python3
"""
Generate 2025 test data using the EXACT same methodology as generate_data.py
but with Claude Opus 4.6 as the Teacher instead of Gemini.

Same prompts. Same format. Same pipeline. Different teacher model.
"""

import json
import time
import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import anthropic

# Import the EXACT prompts from generate_data.py
from generate_data import (
    RESEARCH_SYSTEM_PROMPT,
    TEACHING_SYSTEM_PROMPT,
    extract_json_content,
)

TEACHER_MODEL = "claude-opus-4-6"

INPUT_FILE = Path(__file__).parent / "data" / "events_2025_test.json"
OUTPUT_FILE = Path(__file__).parent / "data" / "test_2025_sft.jsonl"
PROGRESS_FILE = OUTPUT_FILE.with_suffix('.progress.json')
TRACES_DIR = Path(__file__).parent / "output" / "test_traces_2025"

FORECASTING_ANGLES = {
    0: "Structural/Mechanism (Physics, supply chains, legal constraints, technical dependencies)",
    1: "Economic/Incentives (Follow the money, game theory, who benefits, cost structures)",
    2: "Political/Social (Public sentiment, elections, power dynamics, tribal affiliations)",
    3: "Base Rates/Precedents (The 'Outside View' - historical reference classes, how often does this happen?)",
    4: "Temporal/Pacing (Lead times, typical durations, speed of propagation, sequencing)",
}

SYSTEM_PROMPT_FOR_TRAINING = (
    "You are a forecasting assistant with knowledge through December 2023. "
    "When given context about events in 2024, analyze the information carefully:\n"
    "1. Identify what the context tells you\n"
    "2. Connect it to patterns you know from before 2024\n"
    "3. Reason through the causal chain step by step\n"
    "4. Give a calibrated prediction with probability estimates\n"
    "Be honest about uncertainty. Show your reasoning clearly."
)


class ClaudeTeacher:
    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=api_key)

    def conduct_research(self, event, variation_idx):
        """Phase 1: Research Dossier — SAME prompt as generate_data.py"""
        selected_angle = FORECASTING_ANGLES.get(variation_idx, "General Causal Analysis")

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

Respond with JSON matching this schema:
{{
  "event_context": {{"headline": "...", "date": "..."}},
  "temporal_split": {{"context_clues": ["..."], "outcome": "..."}},
  "causal_graph": {{"root_driver": "...", "mechanism": "...", "counterfactual_rejection": "..."}},
  "teaching_plan": {{"context_injection": "...", "key_questions": ["..."]}}
}}
"""
        for attempt in range(3):
            try:
                response = self.client.messages.create(
                    model=TEACHER_MODEL,
                    max_tokens=2000,
                    system=RESEARCH_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}]
                )
                return extract_json_content(response.content[0].text)
            except Exception as e:
                print(f"  [Research] Error (attempt {attempt+1}): {e}")
                time.sleep(3 * (attempt + 1))
        return None

    def generate_dialogue(self, event, research, variation_idx):
        """Phase 2: Dialogue — SAME prompt as generate_data.py"""
        selected_angle = FORECASTING_ANGLES.get(variation_idx, "General")
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

Respond with JSON: {{"dialogue": [{{"role": "user", "content": "..."}}, {{"role": "assistant", "content": "..."}}]}}
"""
        for attempt in range(3):
            try:
                response = self.client.messages.create(
                    model=TEACHER_MODEL,
                    max_tokens=3000,
                    system=TEACHING_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}]
                )
                data = extract_json_content(response.content[0].text)
                dialogue = data.get("dialogue", data.get("messages", []))
                if not isinstance(dialogue, list) or len(dialogue) < 2:
                    raise ValueError("Invalid dialogue structure")
                return dialogue
            except Exception as e:
                print(f"  [Dialogue] Error (attempt {attempt+1}): {e}")
                time.sleep(3 * (attempt + 1))
        return None


def format_for_sft(trace):
    """Format exactly like generate_data.py does — Llama 3.3 chat format."""
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-3.3-70B-Instruct")

    messages = [{"role": "system", "content": SYSTEM_PROMPT_FOR_TRAINING}]
    for turn in trace['messages']:
        role = turn.get("role", "user").lower()
        if role not in ["user", "assistant"]:
            role = "user"
        messages.append({"role": role, "content": turn.get("content", "")})

    text = tokenizer.apply_chat_template(messages, tokenize=False)
    return text


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed_pairs": [], "traces": []}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def main():
    print("=" * 60)
    print("THL 2025 Test Data Generation (Claude Opus 4.6)")
    print("Using SAME methodology as generate_data.py")
    print(f"Teacher: {TEACHER_MODEL}")
    print("=" * 60)

    with open(INPUT_FILE) as f:
        events = json.load(f)
    print(f"Loaded {len(events)} events")

    progress = load_progress()
    print(f"Progress: {len(progress['completed_pairs'])} pairs done")

    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    teacher = ClaudeTeacher()

    total = len(events) * 5
    for event in events:
        eid = event['id']
        print(f"\nEvent {eid}: {event['headline']}")

        for angle_idx in range(5):
            pair_key = f"{eid}:{angle_idx}"
            if pair_key in progress['completed_pairs']:
                print(f"  [{angle_idx+1}/5] {FORECASTING_ANGLES[angle_idx].split('(')[0].strip()} (done)")
                continue

            angle_name = FORECASTING_ANGLES[angle_idx].split('(')[0].strip()
            print(f"  [{angle_idx+1}/5] {angle_name}...", end=" ", flush=True)

            # Phase 1: Research
            research = teacher.conduct_research(event, angle_idx)
            if not research:
                print("FAILED (research)")
                continue

            # Phase 2: Dialogue
            dialogue = teacher.generate_dialogue(event, research, angle_idx)
            if not dialogue:
                print("FAILED (dialogue)")
                continue

            # Save trace
            trace = {
                "id": eid,
                "event": event["headline"],
                "category": event.get("category", "general"),
                "variation": angle_idx,
                "angle": angle_name,
                "research": research,
                "messages": dialogue,
            }

            # Save trace file
            trace_file = TRACES_DIR / f"event_{eid:03d}_angle_{angle_idx}.json"
            with open(trace_file, 'w') as f:
                json.dump(trace, f, indent=2)

            progress['traces'].append(trace)
            progress['completed_pairs'].append(pair_key)
            save_progress(progress)

            print("done")
            time.sleep(1)

    # Save final output (user turns = test prompts, assistant turns = reference)
    print(f"\nSaving {len(progress['traces'])} traces to {OUTPUT_FILE}")
    with open(OUTPUT_FILE, 'w') as f:
        for trace in progress['traces']:
            # Save the raw trace — inference will extract the user turn
            f.write(json.dumps(trace) + "\n")

    print(f"\n{'=' * 60}")
    print(f"DONE — {len(progress['traces'])} test traces generated")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
