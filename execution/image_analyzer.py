"""
Image Analyzer
Uses GPT-4 Vision to analyze vehicle condition from images.

Usage:
    python -m execution.image_analyzer --vehicle-id 1 --max-images 5
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional

from execution.config import OPENAI_API_KEY
from execution.db_repository import Repository


@dataclass
class ImageResult:
    image_url: str
    image_type: str
    condition_score: float
    damages: list[str]
    assessment: str


@dataclass
class AnalysisResult:
    overall_score: float
    images_analyzed: int
    damages: list[str]
    repair_cost_estimate: Optional[float]
    value_adjustment: Optional[float]
    image_results: list[dict]


ANALYSIS_PROMPT = """Analyze this vehicle image. Respond in JSON format with:
{
  "image_type": "exterior" | "interior" | "engine" | "damage" | "other",
  "condition_score": <1-10 where 10 is perfect>,
  "overall_condition": "excellent" | "good" | "fair" | "poor",
  "confidence": <0.0-1.0>,
  "damages": ["list of visible damage or issues"],
  "assessment": "Brief description of what you see and the vehicle's condition"
}

Be specific about any visible damage, rust, dents, scratches, wear, or issues.
If you can't determine something, note it as uncertain."""


def analyze_single_image(client, image_url: str) -> Optional[dict]:
    """Analyze a single image with GPT-4 Vision."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ANALYSIS_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            max_tokens=500,
        )

        text = response.choices[0].message.content.strip()

        # Try to parse JSON from response
        # Handle markdown code blocks
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return json.loads(text)

    except json.JSONDecodeError:
        print(f"    Warning: Could not parse GPT response as JSON", file=sys.stderr)
        return {
            "image_type": "other",
            "condition_score": 5,
            "overall_condition": "unknown",
            "confidence": 0.3,
            "damages": [],
            "assessment": text if 'text' in dir() else "Analysis failed",
        }
    except Exception as e:
        print(f"    Error analyzing image: {e}", file=sys.stderr)
        return None


def run(vehicle_id: Optional[int] = None, max_images: int = 5,
        bike_id: Optional[int] = None) -> Optional[AnalysisResult]:
    """Analyze images for a vehicle or bike."""
    if not vehicle_id and not bike_id:
        print("Error: provide --vehicle-id or --bike-id", file=sys.stderr)
        return None

    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not set in .env", file=sys.stderr)
        return None

    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai not installed. Run: pip install openai", file=sys.stderr)
        return None

    repo = Repository()

    if bike_id:
        entity = repo.get_bike(bike_id)
        if not entity:
            print(f"Bike {bike_id} not found", file=sys.stderr)
            repo.close()
            return None
        image_urls = entity.image_urls[:max_images]
        entity_label = f"Bike {bike_id} ({entity.brand} {entity.model})"
        def save_fn(url, **kwargs):
            repo.save_bike_image_analysis(bike_id=bike_id, image_url=url, **kwargs)
    else:
        entity = repo.get_vehicle(vehicle_id)
        if not entity:
            print(f"Vehicle {vehicle_id} not found", file=sys.stderr)
            repo.close()
            return None
        image_urls = entity.image_urls[:max_images]
        entity_label = f"Vehicle {vehicle_id} ({entity.make} {entity.model})"
        def save_fn(url, **kwargs):
            repo.save_image_analysis(vehicle_id=vehicle_id, image_url=url, **kwargs)

    if not image_urls:
        print(f"{entity_label} has no images", file=sys.stderr)
        repo.close()
        return None

    print(f"Analyzing {len(image_urls)} images for {entity_label}")

    client = OpenAI(api_key=OPENAI_API_KEY)
    image_results = []
    all_damages = []
    scores = []

    for i, url in enumerate(image_urls, 1):
        print(f"  [{i}/{len(image_urls)}] Analyzing {url[:80]}...")
        result = analyze_single_image(client, url)
        if not result:
            continue

        save_fn(
            url,
            image_type=result.get("image_type", "other"),
            condition_score=result.get("condition_score", 5),
            overall_condition=result.get("overall_condition", "unknown"),
            confidence=result.get("confidence", 0.5),
            damage_detected=json.dumps(result.get("damages", [])),
            assessment_details=json.dumps({"assessment": result.get("assessment", "")}),
            raw_response=json.dumps(result),
            model_used="gpt-4o",
        )

        image_results.append(result)
        all_damages.extend(result.get("damages", []))
        if result.get("condition_score"):
            scores.append(result["condition_score"])

        print(f"    Type: {result.get('image_type')} | Score: {result.get('condition_score')}/10 | {result.get('assessment', '')[:80]}")

    repo.close()

    if not scores:
        return AnalysisResult(overall_score=0, images_analyzed=0, damages=[],
                               repair_cost_estimate=None, value_adjustment=None, image_results=[])

    overall_score = sum(scores) / len(scores)
    repair_cost = len(set(all_damages)) * 200 if all_damages else None
    value_adjustment = -repair_cost if repair_cost else None
    unique_damages = list(set(all_damages))

    print(f"\nOverall condition score: {overall_score:.1f}/10")
    if unique_damages:
        print(f"Damages found: {', '.join(unique_damages)}")

    return AnalysisResult(
        overall_score=round(overall_score, 1),
        images_analyzed=len(image_results),
        damages=unique_damages,
        repair_cost_estimate=repair_cost,
        value_adjustment=value_adjustment,
        image_results=image_results,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze vehicle or bike images with GPT-4 Vision")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--vehicle-id", type=int, help="Vehicle ID")
    group.add_argument("--bike-id", type=int, help="Bike ID")
    parser.add_argument("--max-images", type=int, default=5, help="Max images to analyze")
    args = parser.parse_args()

    result = run(vehicle_id=args.vehicle_id, bike_id=args.bike_id, max_images=args.max_images)
    if result:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(json.dumps({"error": "Could not analyze images"}))
