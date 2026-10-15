def decide(*, blocked, check_failure, p_match, p_injection, offer, refused_undo) -> dict:
    raise NotImplementedError  # {"decision": "pay"|"ask"|"decline"|"block", "rule", "undo_cost_pence", "expected_loss_pence"}
