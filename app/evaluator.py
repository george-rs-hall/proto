def render_facts(facts: list[dict]) -> list[str]:
    return [f"{f['subject']} {f['relation']} {f['object']}" for f in facts]


def evaluate(request: str, fact_sentences: list[str], items: list[dict]) -> dict:
    raise NotImplementedError  # {"p_match", "p_injection", "source": "jev"|"claude"}
