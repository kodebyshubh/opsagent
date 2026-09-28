# Decision Log

**Project:** OpsAgent (Xiarch Bharat Assessment Option 3)
**Companion docs:** `PRD.md`, `IMPLEMENTATION_PLAN.md`

## Rules

- Never silently change an architectural decision. To change one, add a new DEC entry and mark the old one `Status: Superseded by DEC-XXX`.
- **Accepted** = confirmed by Shubh or forced by the brief. **Proposed** = default chosen to unblock work, needs Shubh's approval (these are the OPEN DECISIONS in PRD Section 23).
- Only material decisions go here. Minor coding choices do not.

## Index

| ID | Title | Status |
|---|---|---|
| DEC-001 | Python as the implementation language | Accepted |
| DEC-002 | LangGraph as the agent orchestrator | Accepted |
| DEC-003 | LLM provider and model | Accepted |
| DEC-004 | LLM decides, deterministic code enforces safety | Accepted |
| DEC-005 | Structured outputs with graph-executed tools (no native tool calling) | Accepted |
| DEC-006 | Interfaces: CLI + Streamlit over a shared service layer, no HTTP API | Accepted |
| DEC-007 | SQLite for store data, memory, runs, audit; no migration tool | Accepted |
| DEC-008 | ChromaDB with local default embeddings for policy docs | Accepted |
| DEC-009 | Long-term memory in SQLite, loaded via retrieval tool, summary from reasoner | Accepted |
| DEC-010 | Parallelism via thread pools with an entity-aware scheduler | Accepted |
| DEC-011 | Money stored as integer INR rupees | Rejected, replaced by DEC-018 |
| DEC-012 | LangGraph interrupts + SQLite checkpointer for approval and clarification | Accepted |
| DEC-013 | Testing: offline FakeLLM tests + separate live eval | Accepted |
| DEC-014 | Fixed seed "today" for reproducible demos | Accepted |
| DEC-015 | Action set and risk tiers | Accepted |
| DEC-016 | Append-only audit table | Accepted |
| DEC-017 | Domain: fictional Indian e-commerce operations | Accepted |
| DEC-018 | Money as integer paise in storage, 2-decimal rupees everywhere else | Accepted |
| DEC-019 | Target Python 3.13 (support 3.11+) | Accepted |
| DEC-020 | Also satisfy the submission form and Engineer Intern brief | Accepted |
| DEC-021 | Offline embedding fallback for the policy index | Accepted |
| DEC-022 | Default Groq model: openai/gpt-oss-120b (Llama 3.3 70B retired) | Accepted |
| DEC-023 | Version-aware hybrid policy retrieval, placeholder-free planning | Accepted |

---

## DEC-001: Python as the implementation language

Date: 2026-09-27
Status: Accepted (version range refined by DEC-019)

### Context
Brief allows any language (C-12). Shubh's core stack is Python AI engineering and he does not want Java.

### Options Considered
1. Python
2. TypeScript (LangGraph.js)

### Decision
Python 3.11+.

### Reasoning
Best library support (LangGraph, ChromaDB, Streamlit), fastest for Shubh under a 48h deadline.

### Tradeoffs
Gain: speed, ecosystem. Lose: nothing material.

### Consequences
All modules Python; Pydantic for schemas; pytest and ruff.

### Related Requirements
All

### Related Tasks
TASK-001

---

## DEC-002: LangGraph as the agent orchestrator

Date: 2026-09-27
Status: Accepted

### Context
Need planning, loops (second retrieval round, repair), pauses for human approval and clarification, and auditable steps (B-03, B-04, B-08).

### Options Considered
1. LangGraph state graph
2. Hand-written Python loop
3. CrewAI / AutoGen multi-agent
4. OpenAI Agents SDK

### Decision
LangGraph with explicit nodes: load_memory, plan, retrieve, reason, ask_clarification, validate, approval, execute, respond, save_memory.

### Reasoning
First-class `interrupt()` and checkpointing make HITL simple and correct. Explicit nodes map one to one to audit events and to the assessment's "reason, decide, execute, explain" wording. Shubh has used it (kaam.ai). Multi-agent frameworks add overhead with no requirement for multiple agents. The OpenAI Agents SDK ties us to one provider.

### Tradeoffs
Gain: HITL, resumability, clear control flow. Lose: API churn risk between LangGraph versions (mitigated by pinning).

### Consequences
State lives in a TypedDict; nodes are pure-ish functions; checkpointer required (DEC-012).

### Related Requirements
FR-008, FR-009, FR-011, FR-013, FR-019

### Related Tasks
TASK-014, TASK-015, TASK-021

---

## DEC-003: LLM provider and model

Date: 2026-09-27
Status: Accepted (approved by Shubh 2026-09-27); default model superseded by DEC-022

### Context
Planner and Reasoner need reliable structured JSON output, low latency, and free or cheap usage.

### Options Considered
1. Groq, Llama 3.3 70B class model
2. Google Gemini Flash class model
3. Local Ollama model
4. OpenAI / Anthropic paid APIs

### Decision
Provider-agnostic factory. Default Groq (Llama 3.3 70B class); Gemini Flash class supported via `OPSAGENT_PROVIDER=gemini`. Exact model ids live in `.env`, verified against provider docs at setup.

### Reasoning
Groq is very fast (good for a live demo) with a free tier; Gemini is a solid fallback with strong structured output. Local models are too unreliable for strict schemas on a laptop. Paid APIs add cost with no need.

### Tradeoffs
Gain: speed and zero cost; switchable. Lose: free-tier rate limits during eval runs; structured-output quality differs between providers (eval will tell).

### Consequences
`llm.py` must not use provider-specific features beyond `with_structured_output`. README documents both keys. If Groq structured output proves unreliable during TASK-010, switch default to Gemini via a new DEC entry.

### Related Requirements
FR-009, FR-010, NFR Performance

### Related Tasks
TASK-010, TASK-023, TASK-028

---

## DEC-004: LLM decides, deterministic code enforces safety

Date: 2026-09-27
Status: Accepted

### Context
Brief forbids "hardcoded responses, predefined rules, or static outputs" (C-10) but also asks for human approval of critical actions (B-04). Business invariants (cannot refund more than paid) must never depend on LLM correctness.

### Options Considered
1. LLM decides everything including risk and validity
2. Rules engine decides actions, LLM only phrases answers
3. LLM decides actions and reasoning; code enforces guardrails only

### Decision
Option 3. LLM: interpretation, retrieval choice, conflict reasoning, action choice, args, explanation. Code: arg schemas, preconditions, evidence-ref checks, risk tier, approval requirement, scheduling, transactions, audit, memory writes.

### Reasoning
Satisfies C-10 (no rule decides what to do or what to say) while keeping the system safe. Conflict-resolution guidance is given to the LLM as reasoning guidance in the prompt, not implemented as code rules.

### Tradeoffs
Gain: safety and testability; clear story for reviewers. Lose: reviewers might misread guardrails as "rules", so the README must explain the split.

### Consequences
README section "What the LLM decides vs what code enforces" is mandatory. No code path executes an action without passing validator and risk gate.

### Related Requirements
FR-010, FR-012, FR-015, FR-016, FR-017, FR-019

### Related Tasks
TASK-017, TASK-018, TASK-019, TASK-021, TASK-029

---

## DEC-005: Structured outputs with graph-executed tools

Date: 2026-09-27
Status: Accepted

### Context
The LLM must select tools and args. Two styles: native function calling (LLM calls tools in a loop, ReAct) or LLM emits a structured plan/decision that the graph executes.

### Options Considered
1. Native tool-calling ReAct loop
2. Structured `Plan` and `Decision` objects executed by the graph

### Decision
Option 2.

### Reasoning
Every proposed action is visible, validated and approvable before any side effect (B-04, B-08). Retrievals can be run in parallel deterministically (B-02). Fewer LLM calls than a ReAct loop. Works the same across providers.

### Tradeoffs
Gain: auditability, parallelism, predictability, cost. Lose: less "free-roaming" agent behaviour; bounded to 2 retrieval rounds.

### Consequences
Pydantic models `Plan`, `Decision`, `ProposedAction` are the contract; tool catalogs are rendered into prompts from registries.

### Related Requirements
FR-006, FR-009, FR-010, FR-018

### Related Tasks
TASK-007, TASK-009, TASK-010, TASK-011

---

## DEC-006: Interfaces: CLI + Streamlit over a shared service layer, no HTTP API

Date: 2026-09-27
Status: Accepted (Streamlit approved by Shubh 2026-09-27)

### Context
Reviewers need to run it easily. Time is limited. The brief does not require an API.

### Options Considered
1. CLI only
2. CLI + Streamlit, both calling `service.py`
3. FastAPI backend + React frontend

### Decision
Option 2, with Streamlit as the first item cut if behind schedule.

### Reasoning
CLI is fastest and scriptable (eval). Streamlit makes the approval flow and audit visually clear in minutes of reviewer time. FastAPI + React doubles the surface area with no requirement behind it.

### Tradeoffs
Gain: speed, one code path for both UIs. Lose: no API for integrations (future scope).

### Consequences
No business logic in `cli.py` or `ui/`. No FastAPI dependency.

### Related Requirements
FR-027, FR-028, FR-029, NG-03

### Related Tasks
TASK-016, TASK-022, TASK-026, TASK-027

---

## DEC-007: SQLite for store data, memory, runs, audit; no migration tool

Date: 2026-09-27
Status: Accepted

### Context
Need a transactional internal source that actions can modify, plus storage for memory and audit. Reviewers must set it up with zero friction.

### Options Considered
1. SQLite (stdlib)
2. PostgreSQL (Docker)
3. JSON files only

### Decision
SQLite, WAL mode, one connection per thread, schema recreated by `opsagent seed`. No Alembic.

### Reasoning
Zero install. Real SQL, transactions and constraints. Recreating the schema is fine for a demo dataset.

### Tradeoffs
Gain: simplicity. Lose: limited concurrent writes (mitigated by entity-aware scheduler and busy_timeout); no migrations (acceptable, NG-08).

### Consequences
Replacing SQLite requires a new DEC. Checkpoints stored in a separate file (DEC-012) so reseeding is safe.

### Related Requirements
FR-001, FR-023, FR-024, FR-026

### Related Tasks
TASK-004, TASK-005

---

## DEC-008: ChromaDB with local default embeddings for policy docs

Date: 2026-09-27
Status: Accepted (fallback behaviour added by DEC-021)

### Context
Need a vector source for unstructured policy text (C-01, B-06) and metadata (effective_date) for conflict reasoning (B-07).

### Options Considered
1. ChromaDB persistent + default local embedding function
2. ChromaDB + provider embedding API
3. FAISS
4. Keyword search only

### Decision
Option 1.

### Reasoning
No API key or cost for embeddings, metadata filtering built in, Shubh knows it.

### Tradeoffs
Gain: offline, free. Lose: first run downloads a small embedding model; slightly lower retrieval quality than large API embeddings (irrelevant at 5 documents).

### Consequences
README warns about the first-run model download. Seed rebuilds the collection.

### Related Requirements
FR-002

### Related Tasks
TASK-006

---

## DEC-009: Long-term memory in SQLite, loaded via retrieval tool, summary from reasoner

Date: 2026-09-27
Status: Accepted

### Context
B-01 requires long-term memory. Options range from simple per-customer notes to semantic memory search.

### Options Considered
1. SQLite `memory_entries` keyed by customer, most recent N retrieved
2. Chroma collection with semantic search over past interactions
3. LangGraph Store API

### Decision
Option 1. The planner requests `get_customer_memory(customer_id)` like any other source. The reasoner emits `summary_for_memory` in its existing call; `save_memory` node writes it deterministically after the run.

### Reasoning
Simplest thing that visibly works across process restarts and feeds decisions (S-09). Avoids an extra LLM call. Semantic memory adds little for a customer-keyed domain.

### Tradeoffs
Gain: simple, testable, cheap. Lose: no fuzzy recall across customers (future scope).

### Consequences
`load_memory` node only pre-loads when the query contains an explicit customer id; otherwise memory arrives through retrieval.

### Related Requirements
FR-024, FR-025

### Related Tasks
TASK-008, TASK-014, TASK-024, TASK-025

---

## DEC-010: Parallelism via thread pools with an entity-aware scheduler

Date: 2026-09-27
Status: Accepted

### Context
B-02 requires parallel execution where applicable, without corrupting data (T-04).

### Options Considered
1. LangGraph `Send` fan-out nodes
2. `ThreadPoolExecutor` inside `retrieve` and `execute` nodes, with a scheduler that groups by dependency level and unique entity key
3. asyncio throughout

### Decision
Option 2.

### Reasoning
Tools are synchronous (sqlite3, Chroma); threads are the simplest correct fit. The scheduler makes the "where applicable" rule explicit and unit-testable (same entity never concurrent; dependencies respected). `Send` spreads one logical step over many graph nodes, which complicates the audit trail and approval handling.

### Tradeoffs
Gain: simple, testable, visible in audit timestamps. Lose: not using LangGraph's native parallel primitive (acceptable; can be revisited).

### Consequences
Each worker opens its own DB connection. Results record `group` index for display.

### Related Requirements
FR-007, FR-021, FR-022

### Related Tasks
TASK-009, TASK-020

---

## DEC-011: Money stored as integer INR rupees

Date: 2026-09-27
Status: Rejected (Shubh requires decimal amounts, 2026-09-27). Replaced by DEC-018.

### Context
Refund amounts and thresholds need exact comparisons.

### Options Considered
1. Integer rupees
2. Integer paise
3. REAL/float

### Decision (proposed, then rejected)
Integer rupees.

### Reasoning
Demo prices can be whole rupees; avoids float errors and paise conversion noise in prompts.

### Tradeoffs
Gain: simplicity. Lose: cannot represent paise (not needed).

### Consequences
All amount fields are `int`. Threshold env var is in rupees.

### Related Requirements
FR-001, FR-016, FR-017

### Related Tasks
TASK-003, TASK-004, TASK-017

---

## DEC-012: LangGraph interrupts + SQLite checkpointer for approval and clarification

Date: 2026-09-27
Status: Accepted

### Context
Runs must pause for approval (B-04) or clarification (B-07), then resume. Streamlit reruns the script on every interaction, so in-memory state is fragile.

### Options Considered
1. `interrupt()` + in-memory saver
2. `interrupt()` + SQLite checkpointer (`langgraph-checkpoint-sqlite`)
3. Custom pause/resume by persisting state manually

### Decision
Option 2, checkpoint file `data/checkpoints.db`, `thread_id = run_id`.

### Reasoning
Works identically for CLI and Streamlit; enables resume after restart (FR-020) almost for free.

### Tradeoffs
Gain: robust pauses. Lose: one extra dependency and file.

### Consequences
Service layer resumes with `Command(resume=...)`. Checkpoint file is not recreated by `seed` (but is gitignored and safe to delete).

### Related Requirements
FR-013, FR-014, FR-019, FR-020

### Related Tasks
TASK-015, TASK-021, TASK-022, TASK-026

---

## DEC-013: Testing: offline FakeLLM tests + separate live eval

Date: 2026-09-27
Status: Accepted

### Context
LLM output is non-deterministic and rate-limited; tests must be fast and reliable, yet quality must be measured.

### Options Considered
1. Only live tests
2. Mock LLM for all tests, no quality measurement
3. FakeLLM for pytest (orchestration + guardrails) and a separate live `opsagent eval` over 10 scenarios

### Decision
Option 3.

### Reasoning
Deterministic tests prove the safety guarantees hold regardless of what the LLM says (for example, injection cannot bypass approval). Live eval gives reviewers a quality number.

### Tradeoffs
Gain: fast CI-style tests plus real quality signal. Lose: eval costs free-tier quota and is not run automatically.

### Consequences
`structured_call` is injected into the graph builder. `@pytest.mark.live` excluded by default.

### Related Requirements
FR-030, PRD Section 18

### Related Tasks
TASK-012, TASK-028

---

## DEC-014: Fixed seed "today" for reproducible demos

Date: 2026-09-27
Status: Accepted

### Context
Refund windows depend on days since delivery. If seed dates are absolute and the reviewer runs it next week, S-01 and S-03 outcomes change.

### Options Considered
1. Absolute dates, real today
2. Dates relative to real today, generated at seed time
3. Fixed `SEED_TODAY` constant used by tools unless `OPSAGENT_USE_REAL_DATE=1`

### Decision
Option 3.

### Reasoning
Reproducible demo and eval regardless of when the reviewer runs it; seed JSON stays static and committed.

### Tradeoffs
Gain: stable results. Lose: "today" in the app is not the calendar date by default (documented in README).

### Consequences
Tools compute `days_since_delivery` from `config.today()`; prompts state the current date explicitly.

### Related Requirements
FR-005, FR-012

### Related Tasks
TASK-002, TASK-003, TASK-008

---

## DEC-015: Action set and risk tiers

Date: 2026-09-27
Status: Accepted (approved by Shubh 2026-09-27)

### Context
B-04 requires approval for critical or irreversible actions. Need a concrete, demo-able set.

### Options Considered
1. Minimal: refund, restock, email
2. Proposed set of 8 tools with tiers (below)
3. Larger set incl. bulk operations

### Decision
| Tool | Tier |
|---|---|
| refund_order | HIGH if amount > ₹5,000.00, else MEDIUM |
| cancel_order | HIGH (irreversible) |
| update_order_status | MEDIUM |
| send_customer_email | MEDIUM (auto-executes; simulated via outbox) |
| restock_item | LOW |
| create_support_ticket | LOW |
| escalate_ticket | LOW |
| add_customer_note | LOW |

Approval required for HIGH, or any action with confidence < 0.6. Max 5 actions per run. No bulk tools.

### Reasoning
Covers answer-only, auto-executed, approval-gated, and precondition-failure paths in the demo.

### Tradeoffs
Gain: rich demo with bounded scope. Lose: MEDIUM actions auto-execute (acceptable because simulated). Alternative: require approval for MEDIUM too (safer, noisier demo).

### Consequences
Registry encodes tiers; threshold and min confidence in env.

### Related Requirements
FR-015, FR-016, FR-019

### Related Tasks
TASK-017, TASK-019

---

## DEC-016: Append-only audit table

Date: 2026-09-27
Status: Accepted

### Context
B-05 requires audit logs for every action; reviewers should trust the trail.

### Options Considered
1. Append-only SQLite `audit_events` + mutable `runs`/`action_records` status tables
2. JSONL log files
3. LangSmith / external tracing

### Decision
Option 1.

### Reasoning
Queryable per run, viewable in CLI/UI, no external account. Append-only enforced by code convention plus a grep test.

### Tradeoffs
Gain: simple, inspectable. Lose: not tamper-proof (acceptable for demo).

### Consequences
Every node logs events; payloads JSON; keys and full prompts never logged.

### Related Requirements
FR-026, FR-027

### Related Tasks
TASK-013, TASK-014, TASK-021, TASK-026

---

## DEC-017: Domain: fictional Indian e-commerce operations

Date: 2026-09-27
Status: Accepted (confirmed by Shubh 2026-09-27)

### Context
Option 3 is domain-agnostic. The domain determines seed data, tools and demo scenarios.

### Options Considered
1. E-commerce operations (orders, refunds, inventory, tickets)
2. IT helpdesk (assets, access requests, incidents)
3. HR operations (leave, policies, onboarding)

### Decision
E-commerce operations for a fictional Indian store, INR, synthetic data.

### Reasoning
Actions are intuitive to any reviewer; natural critical actions (refunds, cancellations); natural conflicts (old vs new return policy); fits memory (repeat refund requests).

### Tradeoffs
Gain: clear demo narrative. Lose: nothing material; common domain.

### Consequences
Changing domain after TASK-003 means redoing seed data, tools and scenarios; confirm before TASK-003.

### Related Requirements
FR-001 to FR-005, FR-016

### Related Tasks
TASK-003, TASK-017, TASK-028

---

## DEC-018: Money as integer paise in storage, 2-decimal rupees everywhere else

Date: 2026-09-27
Status: Accepted (replaces rejected DEC-011)

### Context
Shubh rejected whole-rupee amounts: amounts must support decimals (e.g. ₹450.50, ₹1,299.99). Refund limits and the ₹5,000.00 approval threshold need exact comparisons, and the LLM reads and writes amounts.

### Options Considered
1. REAL/float rupees
2. Decimal stored as TEXT in SQLite
3. Integer paise in storage, `Decimal` rupees (2 dp) at every boundary

### Decision
Option 3. SQLite and seed-derived rows use `*_paise` INTEGER columns. `src/opsagent/money.py` is the only place that converts. Tool args, evidence, prompts, CLI, UI and audit payloads use rupees as 2-decimal strings/`Decimal`. Floats are never used for money.

### Reasoning
Integer paise keeps SQL sums and comparisons exact and cheap. The LLM and humans naturally read rupees with decimals, so they never see paise. One conversion module avoids scattered rounding bugs.

### Tradeoffs
Gain: exact arithmetic, natural display, decimals supported. Lose: a conversion step and `_paise` naming discipline; amounts with more than 2 decimals from the LLM are rejected by validation (then repaired via FR-018).

### Consequences
Schema columns renamed (`total_amount_paise`, `unit_price_paise`, `amount_paise`, catalog `price_paise`). `refund_order.amount` is `Decimal(max_digits=12, decimal_places=2)`. Threshold env var parsed as `Decimal`. New test TEST-001b.

### Related Requirements
FR-001, FR-016, FR-017, FR-019

### Related Tasks
TASK-002, TASK-003, TASK-004, TASK-005, TASK-008, TASK-017, TASK-019

---

## DEC-019: Target Python 3.13 (support 3.11+)

Date: 2026-09-27
Status: Accepted

### Context
Shubh's Windows machine runs Python 3.13.5. Reviewers may use 3.11 or 3.12. DEC-001 said 3.11+.

### Options Considered
1. Require 3.11/3.12 and ask Shubh to install another Python
2. Support 3.11 to 3.13, develop and verify on 3.13

### Decision
Option 2. `requires-python = ">=3.11"`. Every dependency pin must have Windows wheels for 3.13 (ChromaDB and its onnxruntime dependency are the ones to check first).

### Reasoning
No extra setup for Shubh; covers reviewers on older versions.

### Tradeoffs
Gain: zero setup friction. Lose: if a dependency lacks 3.13 wheels, we must pin a compatible version or record a new decision (fallback: Python 3.12 venv via `py -3.12`).

### Consequences
TASK-001 verifies installation on 3.13 before any other work.

### Related Requirements
NFR Compatibility

### Related Tasks
TASK-001, TASK-030

---

## DEC-020: Also satisfy the submission form and Engineer Intern brief

Date: 2026-09-27
Status: Accepted (approved by Shubh 2026-09-27)

### Context
The HR email's Google Form is the submission form. It links an "Agentic AI Engineer Intern" brief (planning trace, 2+ tools, self-correction, induced failure, structured report) and asks for an architecture diagram, run transcripts and a write-up.

### Options Considered
1. Submit only what Option 3 asks for
2. Upload only the form files (diagram, transcripts, write-up)
3. Add failure injection, per-run JSON/MD reports, diagram PNG, transcripts, WRITEUP.md

### Decision
Option 3 (FR-031 to FR-033, TASK-031 to TASK-034).

### Reasoning
Reviewers may score against the Engineer rubric (robustness 20%, initiative 10%). The extras are small on top of the existing design (retries and repair already exist; reports reuse RunState).

### Tradeoffs
Gain: matches every form field and both briefs. Lose: ~2 extra hours; failure-injection hooks add a little code to core paths (kept behind a single `failures.should_fail` check, off by default).

### Consequences
New modules `failures.py`, `report.py`; new folders `reports/`, `transcripts/`; README documents `--inject-failure`.

### Related Requirements
FR-031, FR-032, FR-033

### Related Tasks
TASK-031, TASK-032, TASK-033, TASK-034


---

## DEC-021: Offline embedding fallback for the policy index

Date: 2026-09-27
Status: Accepted (refines DEC-008)

### Context
During the build, the sandbox network blocked the download of ChromaDB's default ONNX embedding model (S3 and Hugging Face both refused). Reviewers behind corporate proxies could hit the same problem, and tests must run offline.

### Options Considered
1. Require the ONNX download (setup fails on restricted networks)
2. Use a provider embedding API (needs another key; Groq has no embeddings)
3. Default to the ONNX model, and fall back automatically to a small built-in hashed bag-of-words embedding

### Decision
Option 3. `OPSAGENT_EMBEDDINGS=auto|onnx|local` (default `auto`). The backend used is stored in the collection metadata so queries always match the index. Embeddings are computed by OpsAgent and passed to Chroma explicitly. Tests use `local`.

### Reasoning
Setup never breaks, and tests are deterministic and offline. With only 5 policy documents and keyword-heavy queries, retrieval quality with the fallback is adequate. The ONNX model is still used whenever it can be downloaded.

### Tradeoffs
Gain: robust setup and offline tests. Lose: the fallback is less semantic than MiniLM.

### Consequences
`seed` prints the backend used. README documents the flag.

### Related Requirements
FR-002

### Related Tasks
TASK-006

---

## DEC-022: Default Groq model changed to openai/gpt-oss-120b

Date: 2026-09-28
Status: Accepted (supersedes the default model named in DEC-003; the provider choice is unchanged)

### Context
The first live run returned `404 model_not_found` for `llama-3.3-70b-versatile`. Groq's deprecation page says Llama 3.3 70B Versatile was shut down for free and developer tiers on 2026-08-16, with `openai/gpt-oss-120b` (or a Qwen 27B model) as the recommended replacement.

### Options Considered
1. `openai/gpt-oss-120b` (production model, JSON object and JSON schema modes, reasoning model)
2. `qwen/qwen3.8-27b` (preview model, may be discontinued at short notice)
3. `openai/gpt-oss-20b` (faster, weaker)

### Decision
Option 1 as the default (`OPSAGENT_MODEL=openai/gpt-oss-120b`). The model id stays configurable in `.env`.

### Reasoning
It is Groq's recommended replacement, it is a production (not preview) model, and it supports the JSON object mode that `llm.py` uses.

### Tradeoffs
Gain: supported, stronger reasoning. Lose: slightly higher latency per call because it is a reasoning model.

### Consequences
`.env.example`, config default, README, write-up and architecture diagram updated. No code-path changes.

### Related Requirements
FR-009, FR-010

### Related Tasks
TASK-010


---

## DEC-023: Version-aware hybrid policy retrieval and placeholder-free planning

Date: 2026-09-28
Status: Accepted (refines DEC-008 and DEC-021)

### Context
The first live eval scored 7/10. The failing runs showed two root causes: (1) the planner sent lookups with placeholder arguments such as `"<to be filled after find_customer>"`, so product and memory evidence came back empty; (2) policy search returned only the newest refund policy, so the reasoner never saw the legacy 15-day FAQ and could not report the version conflict (and once picked the wrong window).

### Options Considered
1. Only tweak prompts
2. Hard-code conflict detection in Python
3. Fix retrieval (hybrid vector + keyword ranking, and include the best-matching chunk of any document that a hit supersedes, annotated with `superseded_by`), reject placeholder arguments deterministically, and tell the planner/reasoner to fetch dependent lookups in the second round

### Decision
Option 3. The LLM still decides what conflicts mean and what to do (DEC-004); the code only makes sure the relevant, versioned evidence is in front of it.

### Tradeoffs
Gain: conflicts and history reliably visible; no wasted lookups. Lose: one extra LLM round in some runs (more latency under free-tier pacing).

### Related Requirements
FR-006, FR-008, FR-012, FR-024

### Related Tasks
TASK-006, TASK-009, TASK-011
