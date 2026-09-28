"""OpsAgent Streamlit UI (FR-029). Run: streamlit run ui/streamlit_app.py

Thin client: all logic goes through opsagent.service (no direct DB writes here).
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from opsagent.config import FAILURE_MODES, ConfigError, get_settings
from opsagent.db import connect
from opsagent.models import OpsAgentError, RunState
from opsagent.service import AgentService, get_audit, get_memory, list_runs

st.set_page_config(page_title="OpsAgent", page_icon="🛒", layout="wide")

EXAMPLES = {
    "Refund + restock + email (approval)": (
        "Rahul Mehta says the left ear cup of the headphones from order 1042 crackles and he "
        "wants a refund. Refund him, restock the returned unit and email him about it."),
    "Policy conflict check": "Is order 1042 still eligible for a refund under our policy? "
                             "Only check, do not take any action.",
    "Ambiguous request": "Cancel Priya's order.",
    "Small refund (auto)": "Sneha Reddy (C005) received a misprinted copy of the book in order "
                           "1033. Refund the full amount paid.",
    "Parallel restock": "Restock every item that is at or below its reorder level so that each "
                        "one has 20 units in stock.",
    "Suspicious ticket": "Handle the open support ticket from Aditya Rao (C010).",
    "Memory": "Rahul Mehta (C001) is asking for a refund on order 1042. Check his history "
              "before deciding.",
    "Out of scope": "What is the capital of France?",
}


@st.cache_resource
def service() -> AgentService:
    return AgentService()


def j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def show_run(rs: RunState) -> None:
    if rs.plan:
        with st.expander(f"🧭 Plan: {rs.plan.intent}", expanded=True):
            for i, s in enumerate(rs.plan.anticipated_steps, 1):
                st.markdown(f"{i}. {s}")
            st.caption("Retrievals requested (executed in parallel):")
            st.table([{"tool": r.tool, "args": j(r.args), "why": r.why}
                      for r in rs.plan.retrievals])
    if rs.evidence:
        with st.expander(f"📚 Evidence ({len(rs.evidence)} items)", expanded=False):
            for e in rs.evidence:
                label = f"{e.evidence_id} · {e.source} · {e.tool} {j(e.args)} · {e.latency_ms} ms"
                if e.found:
                    st.markdown(f"**{label}**")
                    st.json(e.data, expanded=False)
                else:
                    st.markdown(f"**{label}** · :red[{e.error}]")
    d = rs.decision
    if d:
        for c in d.conflicts:
            vals = "; ".join(f"{v.value} ({v.evidence_id})" for v in c.values)
            st.warning(f"**Conflict: {c.field}**: {vals}\n\n**Resolution:** {c.resolution}. "
                       f"**Why:** {c.justification}")
        if d.missing_info:
            st.warning("Missing info: " + "; ".join(d.missing_info))
        st.markdown("#### 🧠 Decision (explained before execution)")
        st.info(d.answer + (f"\n\nCitations: {', '.join(d.citations)}" if d.citations else ""))
        vals = {v.action_id: v for v in rs.validation}
        for a in d.proposed_actions:
            v = vals.get(a.action_id)
            badge = f"`{v.risk} risk`" if v else ""
            with st.container(border=True):
                st.markdown(f"**{a.action_id} · {a.tool}** {badge} · confidence "
                            f"{a.confidence:.2f} · depends on: {', '.join(a.depends_on) or 'none'}")
                st.code(j(v.args if v else a.args), language="json")
                st.markdown(f"**Reasoning:** {a.reasoning}  \nEvidence: "
                            f"{', '.join(a.evidence_refs)}")
                if v and not v.ok:
                    st.error("Rejected by validator: " + "; ".join(v.errors))
                elif v and v.requires_approval:
                    st.warning("Needs human approval: " + v.risk_reason)
                elif v:
                    st.success("Auto-approved: " + v.risk_reason)
    if rs.results:
        st.markdown("#### ⚙️ Execution (same group = ran in parallel)")
        st.table([{"action": r.action_id, "tool": r.tool, "group": r.group, "status": r.status,
                   "detail": j(r.result) if r.result else (r.error or "")}
                  for r in sorted(rs.results, key=lambda r: (r.group or 0, r.action_id))])


def agent_tab() -> None:
    st.subheader("Ask the agent")
    cols = st.columns(4)
    for i, (label, text) in enumerate(EXAMPLES.items()):
        if cols[i % 4].button(label, use_container_width=True):
            st.session_state.query = text
    query = st.text_area("Request", key="query", height=90,
                         placeholder="e.g. Refund order 1042 and restock the item")
    c1, c2 = st.columns([3, 1])
    failure = c2.selectbox("Failure injection (demo)", ["none", *FAILURE_MODES])
    if c1.button("Run agent", type="primary", disabled=not (query or "").strip()):
        with st.status("Planning, retrieving in parallel, reasoning...", expanded=False):
            try:
                st.session_state.run = service().start_run(
                    query, inject_failure=None if failure == "none" else failure)
            except (OpsAgentError, ValueError) as exc:
                st.session_state.run = None
                st.error(str(exc))

    rs: RunState | None = st.session_state.get("run")
    if not rs:
        return
    st.divider()
    st.caption(f"run_id `{rs.run_id}` · status **{rs.status}**")
    show_run(rs)

    if rs.status == "awaiting_clarification":
        st.warning(f"❓ {rs.clarification_question}")
        ans = st.text_input("Your answer", key=f"ans_{rs.run_id}")
        if st.button("Send answer", disabled=not ans.strip()):
            with st.status("Re-planning with your answer..."):
                st.session_state.run = service().submit_clarification(rs.run_id, ans)
            st.rerun()

    if rs.status == "awaiting_approval":
        st.markdown("#### ✋ Human approval required")
        decisions = {}
        for p in rs.pending_approvals:
            with st.container(border=True):
                st.markdown(f"**{p['action_id']} · {p['tool']}** `{p['risk']}`: {p['risk_reason']}")
                st.code(j(p["args"]), language="json")
                st.markdown(f"Reasoning: {p['reasoning']}")
                choice = st.radio("Decision", ["Approve", "Reject"], horizontal=True,
                                  key=f"ch_{rs.run_id}_{p['action_id']}")
                reason = st.text_input("Reason (optional)", key=f"rs_{rs.run_id}_{p['action_id']}")
                decisions[p["action_id"]] = {"approve": choice == "Approve", "reason": reason}
        if st.button("Submit decisions", type="primary"):
            with st.status("Executing..."):
                st.session_state.run = service().submit_approval(rs.run_id, decisions)
            st.rerun()

    if rs.status == "completed":
        st.success(rs.summary)
    if rs.status == "failed":
        st.error(rs.error or rs.summary)
    for p in rs.report_paths:
        path = Path(p)
        if path.exists():
            st.download_button(f"Download {path.suffix[1:].upper()} report", path.read_bytes(),
                               file_name=path.name, key=f"dl_{path.name}")


def audit_tab() -> None:
    runs = list_runs(50)
    if not runs:
        st.info("No runs yet. Try an example query on the Agent tab.")
        return
    st.dataframe([{k: r[k] for k in ("run_id", "started_at", "status", "query")} for r in runs],
                 use_container_width=True, hide_index=True)
    rid = st.selectbox("Run", [r["run_id"] for r in runs])
    for e in get_audit(rid):
        with st.expander(f"{e['ts'][11:23]} · {e['event_type']} · {e['node'] or ''}"):
            st.json(e["payload"], expanded=True)


def memory_tab() -> None:
    conn = connect()
    try:
        customers = [dict(r) for r in conn.execute("SELECT id, name FROM customers ORDER BY id")]
    finally:
        conn.close()
    pick = st.selectbox("Customer", [f"{c['id']} · {c['name']}" for c in customers])
    rows = get_memory(pick.split(" · ")[0])
    if not rows:
        st.info("No memory entries for this customer yet.")
    else:
        st.dataframe(rows, use_container_width=True, hide_index=True)


def data_tab() -> None:
    table = st.selectbox("Table", ["orders", "refunds", "inventory", "tickets", "outbox"])
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 100")]  # noqa: S608 fixed choices
    finally:
        conn.close()
    st.caption("Money columns are stored in paise (divide by 100 for rupees).")
    st.dataframe(rows, use_container_width=True, hide_index=True)


def main() -> None:
    st.title("🛒 OpsAgent")
    st.caption("Autonomous knowledge execution agent for ShopKart operations · plans, retrieves "
               "from SQLite + ChromaDB + JSON in parallel, reasons, asks for approval, acts, "
               "and audits everything.")
    try:
        s = get_settings()
        s.require_llm()
        service()
    except (ConfigError, OpsAgentError) as exc:
        st.error(str(exc))
        st.stop()
    st.sidebar.markdown(f"**Model:** {s.provider} · `{s.model}`")
    st.sidebar.markdown(f"**Refund approval threshold:** ₹{s.refund_approval_threshold:,.2f}")
    st.sidebar.markdown(f"**Demo date:** {s.today().isoformat()}")
    t1, t2, t3, t4 = st.tabs(["Agent", "Audit log", "Memory", "Data"])
    with t1:
        agent_tab()
    with t2:
        audit_tab()
    with t3:
        memory_tab()
    with t4:
        data_tab()


main()
