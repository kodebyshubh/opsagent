"""Scripted LLM outputs for offline integration tests (not used by the app)."""

S01_PLAN = {
    "intent": "Refund order 1042 for Rahul, restock the item and email him.",
    "retrievals": [
        {"tool": "get_order", "args": {"order_id": 1042}, "why": "order facts"},
        {"tool": "find_customer", "args": {"query": "Rahul"}, "why": "customer id"},
        {"tool": "search_policies", "args": {"query": "refund return window days"}, "why": "policy"},
        {"tool": "get_product", "args": {"sku": "SKU-HP-001"}, "why": "product rules"},
        {"tool": "get_customer_memory", "args": {"customer_id": "C001"}, "why": "history"},
    ],
    "anticipated_steps": ["check eligibility", "refund", "restock", "email"],
}

S01_DECISION = {
    "answer": "Order 1042 was delivered 20 days ago; the v2 policy (30 days) applies, so a full "
              "refund of 6499.00 is allowed. It exceeds the approval threshold.",
    "citations": ["E1", "E3"],
    "conflicts": [{
        "field": "return window",
        "values": [{"value": "30 days", "evidence_id": "E3"},
                   {"value": "15 days", "evidence_id": "E3"}],
        "resolution": "30 days",
        "justification": "refund_policy_v2 (2026-01-01) supersedes the 2024 FAQ",
    }],
    "proposed_actions": [
        {"action_id": "a1", "tool": "refund_order",
         "args": {"order_id": 1042, "amount": "6499.00", "reason": "Defective ear cup"},
         "reasoning": "Within 30-day window", "evidence_refs": ["E1", "E3"], "confidence": 0.9,
         "depends_on": []},
        {"action_id": "a2", "tool": "restock_item",
         "args": {"sku": "SKU-HP-001", "quantity": 1, "reason": "Returned unit"},
         "reasoning": "Returned item", "evidence_refs": ["E1"], "confidence": 0.85,
         "depends_on": ["a1"]},
        {"action_id": "a3", "tool": "send_customer_email",
         "args": {"customer_id": "C001", "subject": "Your refund for order 1042",
                  "body": "Hi Rahul, your refund of Rs 6,499.00 has been issued."},
         "reasoning": "Policy requires informing the customer", "evidence_refs": ["E2", "E3"],
         "confidence": 0.9, "depends_on": ["a1"]},
    ],
    "summary_for_memory": "Rahul requested a third refund this month (order 1042, headphones).",
}

S02_PLAN = {"intent": "Warranty question", "retrievals": [
    {"tool": "get_product", "args": {"sku": "SKU-HP-001"}, "why": "warranty"}],
    "anticipated_steps": ["answer"]}
S02_DECISION = {"answer": "12 months warranty (E1).", "citations": ["E1"],
                "proposed_actions": [], "summary_for_memory": ""}

S05_PLAN = {"intent": "Cancel Priya's order", "retrievals": [
    {"tool": "find_customer", "args": {"query": "Priya"}, "why": "id"},
    {"tool": "list_customer_orders", "args": {"customer_id": "C002", "open_only": True},
     "why": "which order"}], "anticipated_steps": ["identify order", "cancel"]}
S05_CLARIFY = {"answer": "Priya has 3 open orders.", "citations": ["E2"],
               "missing_info": ["order id"],
               "clarification_question": "Priya has open orders 1051, 1063 and 1077. Which one?",
               "proposed_actions": []}
S05_PLAN2 = {"intent": "Cancel order 1063", "retrievals": [
    {"tool": "get_order", "args": {"order_id": 1063}, "why": "status"}], "anticipated_steps": []}
S05_DECISION = {"answer": "Cancelling 1063 (packed).", "citations": ["E3"], "proposed_actions": [
    {"action_id": "a1", "tool": "cancel_order", "args": {"order_id": 1063, "reason": "Customer request"},
     "reasoning": "Order is packed so it can be cancelled", "evidence_refs": ["E3"],
     "confidence": 0.95, "depends_on": []}], "summary_for_memory": "Priya cancelled order 1063."}
