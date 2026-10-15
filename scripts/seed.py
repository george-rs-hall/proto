import app.config  # noqa: F401  (must load before cognee)

import asyncio
import shutil
import time
from datetime import datetime, timezone
from typing import Any

import cognee
from cognee.infrastructure.engine import DataPoint, Edge
from cognee.modules.pipelines import Task
from cognee.tasks.storage import add_data_points
from pydantic import SkipValidation

from app import signing
from app.config import COGNEE_DIR
from app.models import Fact

KEY_ID = "user-george"
FACTS = [
    ("waist_size", "34"),
    ("prefers_fit", "slim"),
    ("avoids_material", "polyester"),
    ("shoe_size", "UK 10"),
    ("tshirt_size", "M"),
    ("allergic_to", "peanuts"),
    ("diet", "vegetarian"),
    ("lives_in", "London"),
    ("prefers_seat", "aisle"),
    ("favourite_colour", "navy"),
    ("coffee_order", "flat white"),
    ("hotel_budget_per_night", "£180"),
    ("has_dog", "Biscuit"),
    ("phone_model", "iPhone 16"),
    ("prefers_delivery", "weekday mornings"),
    ("avoids_scent", "strong fragrances"),
    ("reads_on", "Kindle"),
    ("commutes_by", "bicycle"),
    ("plays_sport", "tennis"),
    ("music_service", "Spotify"),
    ("favourite_cuisine", "Japanese"),
    ("birthday_month", "March"),
    ("preferred_supermarket", "Waitrose"),
    ("laptop_model", "MacBook Air"),
    ("bed_size", "king"),
]


class Person(DataPoint):
    name: str
    has_attribute: SkipValidation[Any] = None
    metadata: dict = {"index_fields": ["name"]}


class Value(DataPoint):
    name: str
    metadata: dict = {"index_fields": ["name"]}


def signed_facts() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for relation, obj in FACTS:
        payload = Fact(subject="George", relation=relation, object=obj, signed_at=now, key_id=KEY_ID).model_dump()
        signed = signing.sign(payload, KEY_ID)
        out.append({**signed["payload"], "signature": signed["signature"]})
    return out


def build_graph(facts: list[dict]) -> list[DataPoint]:
    george = Person(name="George")
    values = [Value(name=fact["object"]) for fact in facts]
    george.has_attribute = [
        Edge(
            target=value,
            relationship_type=fact["relation"],
            edge_text=f"George {fact['relation'].replace('_', ' ')} {fact['object']}",
            properties=fact,
        )
        for fact, value in zip(facts, values)
    ]
    return [george, *values]


async def build_task(data):
    return build_graph(data[0])


async def main():
    shutil.rmtree(COGNEE_DIR, ignore_errors=True)
    facts = signed_facts()
    t = time.time()
    await cognee.run_custom_pipeline(
        tasks=[Task(build_task), Task(add_data_points)],
        data=[facts],
        dataset="george_facts",
        pipeline_name="signed_facts",
    )
    print(f"seeded {len(facts)} signed facts in {time.time() - t:.1f}s")
    for f in facts:
        print(f"  {f['subject']} {f['relation']} {f['object']}")


if __name__ == "__main__":
    asyncio.run(main())
