"""

Prescription Safety Check - LangGraph + OpenAI + Human-in-the-Loop

==================================================================



!!! DEMO ONLY !!!

The drug database, patient records and rules below are SIMPLIFIED MOCK DATA

for learning purposes. They are NOT clinical guidance. A real system must use

a licensed drug database, a BAA with the LLM provider, PHI controls, and

clinical validation.



Install:

    pip install -U langgraph langchain-openai python-dotenv



.env:

    OPENAI_API_KEY=sk-...

    OPENAI_MODEL=gpt-4o-mini     # optional

    LOG_LEVEL=INFO               # optional (DEBUG shows more)

    FLAKY_FORMULARY=1            # optional: first formulary call fails -> shows RetryPolicy



Run:

    python rx_safety_graph_visual_learning.py                  # interactive menu

    python rx_safety_graph_visual_learning.py --scenario 2     # pick a scenario

    python rx_safety_graph_visual_learning.py --scenario 1 --auto   # scripted "humans" (demo / CI)



FLOW

----

 START -> load_patient -> validate_prescription -> dispatch_checks

                                  ^                     |  Send() x4 (parallel)

                                  |        +------------+-------------+-----------+

                                  |        v            v             v           v

                                  |   interactions   allergies     dosage     formulary

                                  |        \\\            |             |          /

                                  |         +-----------+-------------+---------+

                                  |                     v

                                  |               risk_assessment  (rules decide risk, LLM only explains)

                                  |                     |

                                  |        high/blocked |        low/medium

                                  |                     v              |

                                  +-- modify ---- prescriber_review    |   [HITL #1: doctor]

                                  |                     |              |

                                  |                     v              v

                                  +-- modify ------ pharmacist_review      [HITL #2: pharmacist]

                                                        | approve

                                                        v

                                                  release_order -> patient_instructions -> END



LANGGRAPH FEATURES (search for the [F#] tags)

    [F1]  State + reducers + input/output schemas

    [F2]  Structured output from OpenAI (Pydantic)

    [F3]  Send()          parallel fan-out of safety checks

    [F4]  Command()       routing + state update in one return

    [F5]  interrupt()     HITL pauses (prescriber + pharmacist)

    [F6]  Command(resume) resume with the human's decision

    [F7]  Checkpointer    persistence per thread_id

    [F8]  get_state()     snapshot.next / snapshot.tasks

    [F9]  RetryPolicy     flaky external APIs

    [F10] CachePolicy     cache repeated interaction lookups

    [F11] Store           long-term memory (recent orders per patient)

    [F12] Streaming       "updates" + "custom" events

    [F13] Time travel     get_state_history()

    [F14] Logging         thread-aware logs + audit trail in state

"""



from __future__ import annotations



import argparse

import functools

import logging

import operator

import os

import sys

import time

import uuid

from datetime import datetime, timezone

from typing import Annotated, Any, Callable, Literal, TypedDict



from dotenv import load_dotenv

from langchain_core.runnables import RunnableConfig

from langchain_openai import ChatOpenAI

from langgraph.cache.memory import InMemoryCache

from langgraph.checkpoint.memory import InMemorySaver

from langgraph.config import get_config, get_stream_writer

from langgraph.errors import GraphInterrupt

from langgraph.graph import END, START, StateGraph

from langgraph.store.base import BaseStore

from langgraph.store.memory import InMemoryStore

from langgraph.types import CachePolicy, Command, RetryPolicy, Send, interrupt

from pydantic import BaseModel, Field, ValidationError



load_dotenv()



# ============================================================================

# [F14] LOGGING

# ============================================================================

# Two outputs:

#   * console  - human readable, for development

#   * rx_audit.log - file, for traceability

# A logging.Filter injects the LangGraph thread_id into every log line, so you

# can grep one prescription's whole journey. We log patient_id (a pseudonymous

# key) but NEVER names / free-text PHI.

# ============================================================================

log = logging.getLogger("rx")





class ThreadContextFilter(logging.Filter):

    """Adds `thread_id` to each log record using LangGraph's runtime context."""



    def filter(self, record: logging.LogRecord) -> bool:

        try:

            cfg = get_config()  # only works while a node is executing

            record.thread_id = cfg.get("configurable", {}).get("thread_id", "-")

        except RuntimeError:  # called outside a graph run (e.g. in main())

            record.thread_id = "-"

        return True





def setup_logging() -> None:

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | thread=%(thread_id)s | %(message)s", "%H:%M:%S")



    console = logging.StreamHandler(sys.stdout)

    console.setFormatter(fmt)

    console.addFilter(ThreadContextFilter())



    file_h = logging.FileHandler("rx_execution.log", encoding="utf-8")

    file_h.setFormatter(fmt)

    file_h.addFilter(ThreadContextFilter())



    log.setLevel(level)

    log.handlers = [console, file_h]

    log.propagate = False



    # Third-party logs can obscure our educational step-by-step messages.

    for noisy in ("httpx", "httpcore", "openai", "langgraph"):

        logging.getLogger(noisy).setLevel(logging.WARNING)





NODE_EXPLANATIONS = {
    "load_patient": "Read the patient's record and previous medication orders",
    "validate_prescription": "Check that the prescription is valid and supported",
    "dispatch_checks": "Start four independent safety checks in parallel",
    "check_interactions": "Look for conflicts with existing medicines",
    "check_allergies": "Check the patient's known allergies",
    "check_dosage": "Check dose limits and kidney-related precautions",
    "check_formulary": "Check whether insurance covers the medicine",
    "risk_assessment": "Combine all safety results and explain the risk",
    "prescriber_review": "Pause for the prescribing doctor's decision",
    "pharmacist_review": "Pause for the pharmacist's final decision",
    "release_order": "Create the approved pharmacy order",
    "patient_instructions": "Prepare simple instructions for the patient",
}


def traced(fn: Callable) -> Callable:
    """Explain each node automatically without changing its business logic.

    IMPORTANT: GraphInterrupt must be re-raised; on resume the node starts again.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        name = fn.__name__
        purpose = NODE_EXPLANATIONS.get(name, "Run workflow step")
        started = time.perf_counter()
        log.info("STEP START | %s | %s", name, purpose)
        try:
            result = fn(*args, **kwargs)
            log.info("STEP DONE  | %s | finished in %.2f seconds", name, time.perf_counter() - started)
            return result
        except GraphInterrupt:
            log.info("WAITING    | %s | workflow paused for human input; this node restarts on resume", name)
            raise
        except Exception as exc:
            log.warning("STEP ERROR | %s | %s: %s | %.2f seconds | a configured retry may follow",
                        name, type(exc).__name__, exc, time.perf_counter() - started,
                        exc_info=log.isEnabledFor(logging.DEBUG))
            raise
    return wrapper
MAX_REVISIONS = 3  # how many times the prescription can be modified & re-checked



Severity = Literal["none", "minor", "moderate", "major", "contraindicated"]

# Numeric ranking so we can take the "worst" severity with max()

SEVERITY_RANK: dict[str, int] = {"none": 0, "minor": 1, "moderate": 2, "major": 3, "contraindicated": 4}





def get_llm() -> ChatOpenAI:

    """LLM factory (a function so tests can monkeypatch it)."""

    if not os.getenv("OPENAI_API_KEY"):

        raise RuntimeError("OPENAI_API_KEY is not set (add it to .env)")

    return ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0.2)





# ============================================================================

# MOCK DATA (stand-ins for EHR, drug database, formulary and pharmacy system)

# ============================================================================

# Patients carry NO names on purpose (PHI minimisation).

MOCK_EHR: dict[str, dict] = {

    "P001": {  # elderly, CKD, on warfarin, penicillin allergy

        "patient_id": "P001", "age": 67, "weight_kg": 70.0, "egfr": 45.0,

        "allergies": ["penicillin"], "current_meds": ["warfarin", "lisinopril"],

        "conditions": ["atrial fibrillation", "CKD stage 3"],

    },

    "P002": {  # healthy adult

        "patient_id": "P002", "age": 30, "weight_kg": 80.0, "egfr": 105.0,

        "allergies": [], "current_meds": [], "conditions": [],

    },

}



# Simplified drug facts. egfr thresholds are in mL/min/1.73m2.

MOCK_DRUG_DB: dict[str, dict] = {

    "ibuprofen": {

        "class": "nsaid", "max_single_mg": 800, "max_daily_mg": 3200,

        "renal": [(30, "major", "Avoid NSAIDs when eGFR < 30"), (60, "moderate", "Use NSAIDs with caution when eGFR < 60")],

        "interactions": {

            "warfarin": ("major", "NSAID + warfarin: significantly increased bleeding risk"),

            "lisinopril": ("moderate", "NSAID + ACE inhibitor: reduced BP control and kidney injury risk"),

        },

    },

    "amoxicillin": {

        "class": "penicillin", "max_single_mg": 1000, "max_daily_mg": 3000,

        "renal": [(30, "moderate", "Dose adjustment needed when eGFR < 30")],

        "interactions": {"warfarin": ("minor", "Antibiotic may enhance anticoagulant effect; monitor INR")},

    },

    "acetaminophen": {

        "class": "analgesic", "max_single_mg": 1000, "max_daily_mg": 4000,

        "renal": [],

        "interactions": {"warfarin": ("moderate", "Regular high-dose use can raise INR; monitor")},

    },

    "metformin": {

        "class": "biguanide", "max_single_mg": 1000, "max_daily_mg": 2550,

        "renal": [(30, "contraindicated", "Contraindicated when eGFR < 30"), (45, "moderate", "Reassess benefit/risk when eGFR < 45")],

        "interactions": {},

    },

}



MOCK_FORMULARY: dict[str, dict] = {

    "ibuprofen": {"covered": True, "tier": 1, "prior_auth": False},

    "amoxicillin": {"covered": True, "tier": 1, "prior_auth": False},

    "acetaminophen": {"covered": False, "tier": None, "prior_auth": False, "note": "OTC - patient pays"},

    "metformin": {"covered": True, "tier": 1, "prior_auth": False},

}



MOCK_PHARMACY_ORDERS: dict[str, dict] = {}  # order_id -> order (the "irreversible" side effect)



# Used to simulate a flaky external API exactly once (shows RetryPolicy working)

_formulary_calls = {"n": 0}





def query_formulary_api(drug: str) -> dict:

    """Pretend external API. With FLAKY_FORMULARY=1 the FIRST call times out."""

    _formulary_calls["n"] += 1

    if os.getenv("FLAKY_FORMULARY") == "1" and _formulary_calls["n"] == 1:

        raise TimeoutError("formulary API timed out (simulated)")

    return MOCK_FORMULARY.get(drug, {"covered": False, "tier": None, "prior_auth": False, "note": "not in formulary"})





# ============================================================================

# [F1] STATE

# ============================================================================

class Patient(TypedDict):

    patient_id: str

    age: int

    weight_kg: float

    egfr: float

    allergies: list[str]

    current_meds: list[str]

    conditions: list[str]

    recent_orders: list[str]  # filled from the long-term Store [F11]





class Prescription(TypedDict):

    drug: str

    dose_mg: float          # per single dose

    frequency_per_day: int

    route: str

    indication: str





class CheckResult(TypedDict):

    check: str              # which worker produced it

    revision: int           # which version of the prescription it belongs to

    severity: Severity

    findings: list[str]





class AuditEvent(TypedDict):

    ts: str

    actor: str              # "system" | "ai" | "prescriber:\<id>" | "pharmacist:\<id>"

    action: str

    detail: str





class InputState(TypedDict):

    """What the API caller provides."""

    patient_id: str

    prescriber_id: str

    prescription: Prescription





class OutputState(TypedDict, total=False):

    """What the API caller gets back."""

    status: str

    risk_level: str

    order_id: str

    patient_instructions: str

    audit: list[AuditEvent]





class State(TypedDict, total=False):

    """Full internal state."""

    # inputs

    patient_id: str

    prescriber_id: str

    prescription: Prescription

    # loaded context

    patient: Patient

    revision: int

    # Reducer: every parallel check appends its result; operator.add merges them

    checks: Annotated[list[CheckResult], operator.add]

    # assessment

    risk_level: Literal["low", "medium", "high", "blocked"]

    ai_summary: str

    ai_recommendations: list[str]

    override_reason: str

    # outcome

    status: str

    order_id: str

    patient_instructions: str

    # Reducer: append-only audit trail (who did what, when)

    audit: Annotated[list[AuditEvent], operator.add]





class CheckInput(TypedDict):

    """Payload given to each parallel check worker via Send() - differs from State."""

    patient: Patient

    prescription: Prescription

    revision: int





def audit(actor: str, action: str, detail: str = "") -> list[AuditEvent]:

    """Build a one-element audit list (ready to be returned from a node)."""

    log.info("AUDIT     | actor=%s | action=%s | %s", actor, action, detail)

    return [{"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": actor, "action": action, "detail": detail}]





# [F2] Pydantic models: validation + structured LLM output

class PrescriptionModel(BaseModel):

    """Validates the incoming prescription before we spend time on checks."""

    drug: str

    dose_mg: float = Field(gt=0)

    frequency_per_day: int = Field(ge=1, le=6)

    route: str = "oral"

    indication: str = ""





class ClinicalSummary(BaseModel):

    """What we ask the LLM to produce. NOTE: no risk level - rules decide that."""

    summary: str = Field(description="3-4 sentence plain summary of the safety findings for a pharmacist")

    recommendations: list[str] = Field(description="Up to 4 concrete suggestions (alternatives, monitoring, dose changes)")





# ============================================================================

# NODES

# ============================================================================

@traced

def load_patient(state: State, *, store: BaseStore) -> dict:

    """Fetch the patient from the (mock) EHR and enrich with long-term memory."""

    pid = state["patient_id"]

    record = MOCK_EHR.get(pid)

    if record is None:

        log.error("Unknown patient_id=%s", pid)

        return {"status": "patient_not_found", "audit": audit("system", "load_patient_failed", f"patient_id={pid}")}



    # [F11] Long-term memory: what did we order for this patient before?

    item = store.get(("patients", pid), "recent_orders")

    recent = item.value["drugs"] if item else []

    log.info("Loaded patient_id=%s (age=%s, eGFR=%s, %d meds, %d allergies, recent_orders=%s)",

             pid, record["age"], record["egfr"], len(record["current_meds"]), len(record["allergies"]), recent)



    return {"patient": {**record, "recent_orders": recent}, "audit": audit("system", "patient_loaded", f"patient_id={pid}")}





@traced

def validate_prescription(state: State) -> Command[Literal["dispatch_checks", "__end__"]]:

    """

    Schema + business validation. Also the re-entry point after a 'modify'

    decision, so it bumps the revision counter (used to keep checks of

    different versions apart).

    """

    if state.get("status") == "patient_not_found":

        return Command(goto=END)



    revision = state.get("revision", 0) + 1

    if revision > MAX_REVISIONS:

        log.warning("Max revisions (%d) exceeded", MAX_REVISIONS)

        return Command(goto=END, update={"status": "max_revisions_exceeded",

                                         "audit": audit("system", "max_revisions_exceeded", str(MAX_REVISIONS))})

    try:

        rx = PrescriptionModel(**state["prescription"])

    except ValidationError as e:

        log.warning("Prescription failed validation: %s", e.errors())

        return Command(goto=END, update={"status": "invalid_prescription",

                                         "audit": audit("system", "validation_failed", str(e.errors()))})



    drug = rx.drug.strip().lower()

    if drug not in MOCK_DRUG_DB:

        log.warning("Drug '%s' not in drug database", drug)

        return Command(goto=END, update={"status": "unknown_drug",

                                         "audit": audit("system", "unknown_drug", drug)})



    clean: Prescription = {**rx.model_dump(), "drug": drug}  # normalised copy

    log.info("VALID | Prescription revision=%d passed input validation", revision)
    log.info("NEXT | Run all four safety checks on revision=%d", revision)

    return Command(

        goto="dispatch_checks",

        update={"prescription": clean, "revision": revision,

                "audit": audit(f"prescriber:{state.get('prescriber_id', '?')}", "prescription_submitted",

                               f"rev={revision} {drug} {rx.dose_mg}mg x{rx.frequency_per_day}/day")},

    )





@traced

def dispatch_checks(state: State) -> Command[Literal["check_interactions", "check_allergies", "check_dosage", "check_formulary"]]:

    """

    [F3][F4] Fan-out: one Send() per safety check. All four run IN PARALLEL in

    the same super-step; the graph moves on only when ALL of them finished.

    """

    payload: CheckInput = {"patient": state["patient"], "prescription": state["prescription"], "revision": state["revision"]}

    targets = ["check_interactions", "check_allergies", "check_dosage", "check_formulary"]

    log.info("PARALLEL  | Starting %d checks together; risk assessment waits for ALL to finish: %s", len(targets), ", ".join(targets))

    return Command(goto=[Send(t, payload) for t in targets])





# ---- the four parallel workers (deterministic rules - no LLM on purpose) ----

@traced

def check_interactions(state: CheckInput) -> dict:

    """Drug-drug interactions + duplicate therapy. Cached [F10]."""

    get_stream_writer()({"progress": "checking interactions..."})  # [F12] custom event

    drug = state["prescription"]["drug"]

    info = MOCK_DRUG_DB[drug]

    findings, worst = [], "none"



    for med in state["patient"]["current_meds"]:

        med = med.lower()

        hit = info["interactions"].get(med)

        # also look the other way round (med's own table lists our drug)

        if hit is None and med in MOCK_DRUG_DB:

            hit = MOCK_DRUG_DB[med]["interactions"].get(drug)

        if hit:

            sev, msg = hit

            findings.append(f"[{sev}] {drug} + {med}: {msg}")

            if SEVERITY_RANK[sev] > SEVERITY_RANK[worst]:

                worst = sev



    if drug in state["patient"]["recent_orders"]:  # uses long-term memory

        findings.append(f"[minor] Duplicate therapy: {drug} was already ordered recently")

        worst = worst if SEVERITY_RANK[worst] >= 1 else "minor"



    log.info("CHECK     | Drug interactions: severity=%s; findings=%d", worst, len(findings))

    return {"checks": [{"check": "interactions", "revision": state["revision"], "severity": worst, "findings": findings or ["No interactions found"]}]}





@traced

def check_allergies(state: CheckInput) -> dict:

    """Direct allergy match or drug-class match -> contraindicated."""

    get_stream_writer()({"progress": "checking allergies..."})

    drug = state["prescription"]["drug"]

    drug_class = MOCK_DRUG_DB[drug]["class"]

    findings = []

    for allergy in (a.lower() for a in state["patient"]["allergies"]):

        if allergy in (drug, drug_class):

            findings.append(f"[contraindicated] Patient is allergic to '{allergy}' ({drug} is a {drug_class})")

    sev: Severity = "contraindicated" if findings else "none"

    log.info("CHECK     | Allergies: severity=%s; findings=%d", sev, len(findings))

    return {"checks": [{"check": "allergies", "revision": state["revision"], "severity": sev, "findings": findings or ["No allergy conflicts"]}]}





@traced

def check_dosage(state: CheckInput) -> dict:

    """Max single/daily dose + renal (eGFR) adjustments."""

    get_stream_writer()({"progress": "checking dosage & renal function..."})

    rx, p = state["prescription"], state["patient"]

    info = MOCK_DRUG_DB[rx["drug"]]

    findings, worst = [], "none"



    def add(sev: str, msg: str) -> None:

        nonlocal worst

        findings.append(f"[{sev}] {msg}")

        if SEVERITY_RANK[sev] > SEVERITY_RANK[worst]:

            worst = sev



    daily = rx["dose_mg"] * rx["frequency_per_day"]

    if rx["dose_mg"] > info["max_single_mg"]:

        add("major", f"Single dose {rx['dose_mg']}mg exceeds max {info['max_single_mg']}mg")

    if daily > info["max_daily_mg"]:

        add("major", f"Daily dose {daily}mg exceeds max {info['max_daily_mg']}mg")



    # renal rules are sorted ascending by threshold: first matching threshold wins

    for threshold, sev, msg in sorted(info["renal"], key=lambda r: r[0]):

        if p["egfr"] < threshold:

            add(sev, f"{msg} (patient eGFR={p['egfr']})")

            break



    log.info("CHECK     | Dose and kidney function: severity=%s; daily dose=%smg", worst, daily)

    return {"checks": [{"check": "dosage", "revision": state["revision"], "severity": worst, "findings": findings or ["Dose within limits"]}]}





@traced

def check_formulary(state: CheckInput) -> dict:

    """Insurance coverage lookup (external API)."""

    get_stream_writer()({"progress": "checking formulary coverage..."})

    drug = state["prescription"]["drug"]

    cov = query_formulary_api(drug)  # may raise TimeoutError -> RetryPolicy [F9]

    findings, sev = [], "none"

    if not cov["covered"]:

        findings.append(f"[minor] Not covered by plan: {cov.get('note', '')}")

        sev = "minor"

    elif cov.get("prior_auth"):

        findings.append("[minor] Prior authorization required")

        sev = "minor"

    log.info("CHECK     | Insurance coverage: severity=%s; covered=%s", sev, cov["covered"])

    return {"checks": [{"check": "formulary", "revision": state["revision"], "severity": sev, "findings": findings or [f"Covered (tier {cov['tier']})"]}]}





@traced

def risk_assessment(state: State) -> Command[Literal["prescriber_review", "pharmacist_review"]]:

    """

    Join point (runs once after ALL four checks complete).



    SAFETY DESIGN: the risk level is computed by deterministic RULES. The LLM

    only writes a human-readable summary - it can never lower the risk.

    If the LLM is down we still continue with a rule-based summary.

    """

    # keep only checks for the CURRENT revision (older ones stay in state for audit)

    current = [c for c in state["checks"] if c["revision"] == state["revision"]]

    worst = max((c["severity"] for c in current), key=lambda s: SEVERITY_RANK[s], default="none")

    risk = {"contraindicated": "blocked", "major": "high", "moderate": "medium"}.get(worst, "low")

    log.info("RISK      | Checked %d results for revision %d; highest severity=%s; overall risk=%s", len(current), state["revision"], worst, risk)



    # --- LLM narrative (PHI-minimised: age/weight/eGFR/conditions only, no IDs) ---

    p, rx = state["patient"], state["prescription"]

    findings_txt = "\n".join(f"- {c['check']}: {f}" for c in current for f in c["findings"])

    prompt = (

        "You assist a hospital pharmacist. Summarise the safety findings below. Do NOT invent findings and do NOT "

        "state a risk level. Be concise.\n"

        f"Patient: age {p['age']}, {p['weight_kg']}kg, eGFR {p['egfr']}, conditions {p['conditions']}, "

        f"current meds {p['current_meds']}, allergies {p['allergies']}\n"

        f"Proposed: {rx['drug']} {rx['dose_mg']}mg x{rx['frequency_per_day']}/day ({rx['route']}) for '{rx['indication']}'\n"

        f"Findings:\n{findings_txt}"

    )

    try:

        out = get_llm().with_structured_output(ClinicalSummary).invoke(prompt)

        summary, recs = out.summary, out.recommendations

        log.info("LLM summary generated (%d chars, %d recommendations)", len(summary), len(recs))

    except Exception as e:  # graceful degradation

        log.warning("LLM summary failed (%s) - falling back to rule-based summary", e)

        summary, recs = "AI summary unavailable. Review the raw findings:\n" + findings_txt, []



    # [F4] route by risk: high/blocked need the prescriber to look first

    nxt = "prescriber_review" if risk in ("high", "blocked") else "pharmacist_review"

    log.info("NEXT STEP | %s because overall risk is %s", nxt, risk)

    return Command(goto=nxt, update={"risk_level": risk, "ai_summary": summary, "ai_recommendations": recs,

                                     "audit": audit("ai", "risk_assessed", f"rev={state['revision']} risk={risk}")})





def _merge_changes(state: State, changes: dict) -> Prescription:

    """Apply the human's modifications on top of the current prescription."""

    return {**state["prescription"], **changes}





@traced

def prescriber_review(state: State) -> Command[Literal["validate_prescription", "pharmacist_review", "prescriber_review", "__end__"]]:

    """

    [F5] HITL #1 - the prescribing doctor sees the warnings and decides:

      modify   -> change the prescription, all checks run again

      override -> proceed anyway with a written justification (NOT allowed if blocked)

      cancel   -> abandon the order

    """

    current = [c for c in state["checks"] if c["revision"] == state["revision"]]

    decision = interrupt({

        "type": "prescriber_review",

        "message": f"Risk is {state['risk_level'].upper()}. Prescriber decision required.",

        "prescription": state["prescription"],

        "risk_level": state["risk_level"],

        "checks": current,

        "ai_summary": state["ai_summary"],

        "ai_recommendations": state["ai_recommendations"],

        "actions": ["modify", "cancel"] + ([] if state["risk_level"] == "blocked" else ["override"]),

    })

    who = f"prescriber:{decision.get('prescriber_id', state.get('prescriber_id', '?'))}"

    action = decision.get("action")

    log.info("HUMAN     | Doctor chose: %s", action)



    if action == "modify":

        return Command(goto="validate_prescription",

                       update={"prescription": _merge_changes(state, decision.get("changes", {})),

                               "audit": audit(who, "modified_prescription", str(decision.get("changes")))})



    if action == "override":

        if state["risk_level"] == "blocked":  # hard stop - contraindications can't be overridden

            log.warning("Override attempted on BLOCKED order - rejected")

            return Command(goto=END, update={"status": "override_not_permitted",

                                             "audit": audit(who, "override_denied", "contraindicated findings cannot be overridden")})

        reason = (decision.get("reason") or "").strip()

        if not reason:  # justification is mandatory -> ask again

            log.warning("Override without justification - asking again")

            return Command(goto="prescriber_review", update={"audit": audit("system", "override_missing_reason", "re-prompting")})

        return Command(goto="pharmacist_review", update={"override_reason": reason,

                                                         "audit": audit(who, "override", reason)})



    return Command(goto=END, update={"status": "cancelled_by_prescriber",

                                     "audit": audit(who, "cancelled", decision.get("reason", ""))})





@traced

def pharmacist_review(state: State) -> Command[Literal["release_order", "validate_prescription", "__end__"]]:

    """

    [F5] HITL #2 - the pharmacist is the FINAL gate (always required, even for low risk).

      approve -> release the order

      modify  -> change something (e.g. dose) -> re-validate and re-check

      reject  -> stop

    """

    current = [c for c in state["checks"] if c["revision"] == state["revision"]]

    decision = interrupt({

        "type": "pharmacist_review",

        "message": f"Final pharmacist verification (risk: {state['risk_level'].upper()}).",

        "prescription": state["prescription"],

        "risk_level": state["risk_level"],

        "checks": current,

        "ai_summary": state["ai_summary"],

        "ai_recommendations": state["ai_recommendations"],

        "override_reason": state.get("override_reason"),

        "actions": ["approve", "modify", "reject"],

    })

    who = f"pharmacist:{decision.get('pharmacist_id', '?')}"

    action = decision.get("action")

    log.info("HUMAN     | Pharmacist chose: %s", action)



    if action == "approve":

        return Command(goto="release_order", update={"audit": audit(who, "approved", f"rev={state['revision']}")})

    if action == "modify":

        return Command(goto="validate_prescription",

                       update={"prescription": _merge_changes(state, decision.get("changes", {})),

                               "audit": audit(who, "modified_prescription", str(decision.get("changes")))})

    return Command(goto=END, update={"status": "rejected_by_pharmacist",

                                     "audit": audit(who, "rejected", decision.get("reason", ""))})





@traced

def release_order(state: State, *, store: BaseStore) -> dict:

    """

    The irreversible side effect. It runs ONLY after the pharmacist approval,

    and the order id is deterministic (thread + revision) so a retry can never

    create a duplicate order (idempotency).

    """

    thread_id = get_config()["configurable"]["thread_id"]

    order_id = "ORD-" + uuid.uuid5(uuid.NAMESPACE_OID, f"{thread_id}:{state['revision']}").hex[:10].upper()



    if order_id in MOCK_PHARMACY_ORDERS:

        log.warning("Order %s already exists - not creating a duplicate", order_id)

    else:

        MOCK_PHARMACY_ORDERS[order_id] = {"patient_id": state["patient_id"], "prescription": state["prescription"]}

        log.info("ORDER | Created approved pharmacy order %s", order_id)



    # [F11] Update shared store: future prescriptions can detect repeat orders.

    pid, drug = state["patient_id"], state["prescription"]["drug"]

    prev = store.get(("patients", pid), "recent_orders")

    drugs = sorted(set((prev.value["drugs"] if prev else []) + [drug]))

    store.put(("patients", pid), "recent_orders", {"drugs": drugs})



    return {"status": "released", "order_id": order_id,

            "audit": audit("system", "order_released", f"order_id={order_id}")}





@traced

def patient_instructions(state: State) -> dict:

    """Post-approval: plain-language instructions for the patient (LLM, with fallback)."""

    rx = state["prescription"]

    prompt = (

        "Write brief, friendly, plain-language instructions (max 5 bullet points, 6th-grade reading level) "

        f"for a patient taking {rx['drug']} {rx['dose_mg']}mg, {rx['frequency_per_day']} time(s) per day, "

        f"by {rx['route']}, for '{rx['indication']}'. Include when to call the doctor. "

        "Do not add dosing beyond what is stated."

    )

    try:

        text = get_llm().invoke(prompt).content

    except Exception as e:

        log.warning("LLM patient instructions failed (%s) - using fallback", e)

        text = (f"Take {rx['drug']} {rx['dose_mg']}mg {rx['frequency_per_day']} time(s) a day as prescribed. "

                "Contact your pharmacist or doctor if you feel unwell or have questions.")

    return {"patient_instructions": text, "audit": audit("ai", "patient_instructions_generated", f"{len(text)} chars")}





# ============================================================================

# GRAPH ASSEMBLY

# ============================================================================

def build_graph(checkpointer=None, store=None, cache=None):

    """

    Factory used by the CLI now and by FastAPI in phase 2.

    In production pass a persistent checkpointer (Postgres/SQLite) - InMemorySaver

    loses all paused runs when the process restarts.

    """

    b = StateGraph(State, input_schema=InputState, output_schema=OutputState)



    llm_retry = RetryPolicy(max_attempts=3, initial_interval=1.0)

    # [F9] retry only on transient network-type errors (default policy ignores TimeoutError)

    api_retry = RetryPolicy(max_attempts=3, initial_interval=0.5, retry_on=(ConnectionError, TimeoutError))



    b.add_node("load_patient", load_patient, retry_policy=api_retry)

    b.add_node("validate_prescription", validate_prescription)

    b.add_node("dispatch_checks", dispatch_checks)

    # [F10] interaction lookups are pure functions of their input -> safe to cache

    b.add_node("check_interactions", check_interactions, cache_policy=CachePolicy(ttl=300))

    b.add_node("check_allergies", check_allergies)

    b.add_node("check_dosage", check_dosage)

    b.add_node("check_formulary", check_formulary, retry_policy=api_retry)

    b.add_node("risk_assessment", risk_assessment)

    b.add_node("prescriber_review", prescriber_review)

    b.add_node("pharmacist_review", pharmacist_review)

    b.add_node("release_order", release_order)

    b.add_node("patient_instructions", patient_instructions, retry_policy=llm_retry)



    b.add_edge(START, "load_patient")

    b.add_edge("load_patient", "validate_prescription")

    # validate_prescription -> dispatch_checks | END           (Command)

    # dispatch_checks       -> 4 check_* workers               (Command + Send)

    for worker in ("check_interactions", "check_allergies", "check_dosage", "check_formulary"):

        b.add_edge(worker, "risk_assessment")  # all four finish, THEN risk_assessment runs once

    # risk_assessment -> prescriber_review | pharmacist_review (Command)

    # prescriber_review / pharmacist_review -> various         (Command)

    b.add_edge("release_order", "patient_instructions")

    b.add_edge("patient_instructions", END)



    # [F7] checkpointer is mandatory for interrupt()

    return b.compile(

        checkpointer=checkpointer or InMemorySaver(),

        store=store or InMemoryStore(),

        cache=cache or InMemoryCache(),

    )





# ============================================================================

# [F8] INSPECTION HELPERS

# ============================================================================

def pending_interrupts(graph, config) -> list[dict]:

    """Payloads of all interrupts currently waiting for a human."""

    snap = graph.get_state(config)

    return [i.value for task in snap.tasks for i in task.interrupts]





def describe_state(graph, config) -> None:

    """Log what the graph will run next."""

    snap = graph.get_state(config)

    log.info("PAUSED    | Next nodes=%s | Waiting tasks=%s", snap.next, [t.name for t in snap.tasks])





# ============================================================================

# [F12] RUN LOOP: stream -> pause -> ask human -> resume

# ============================================================================

def run_until_done(graph, graph_input, config, answer_fn: Callable[[dict], dict]) -> dict:

    """

    Drives the graph until it finishes. Each time the graph pauses on an

    interrupt() we call `answer_fn(payload)` to get the human's decision and

    resume with Command(resume=...). In FastAPI this loop becomes two endpoints.

    """

    next_input = graph_input

    while True:

        for mode, chunk in graph.stream(next_input, config, stream_mode=["updates", "custom"]):

            if mode == "custom":

                log.info("PROGRESS  | %s", chunk.get("progress"))

            # "updates" chunks contain {node_name: state_update}; "__interrupt__" marks a pause

            elif mode == "updates" and "__interrupt__" in chunk:

                log.debug("STREAM | interrupt raised")



        snap = graph.get_state(config)

        if not snap.next:  # empty tuple -> graph finished

            log.info("COMPLETE  | Workflow ended; final status=%s", snap.values.get("status"))

            return snap.values



        describe_state(graph, config)

        payload = pending_interrupts(graph, config)[0]

        next_input = Command(resume=answer_fn(payload))  # [F6]





# ============================================================================

# [F13] TIME TRAVEL / AUDIT VIEW

# ============================================================================

def print_history(graph, config) -> None:

    """Every checkpoint of the run (newest first) - useful for audits and replays."""

    print("\n--- Checkpoint history (newest first) ---")

    for s in graph.get_state_history(config):

        print(f"  step={s.metadata.get('step'):>3} next={s.next}")





# ============================================================================

# CONSOLE "HUMANS" + SCENARIOS

# ============================================================================

SCENARIOS: dict[str, dict] = {

    "1": {"title": "Ibuprofen for a patient on warfarin with CKD  -> HIGH risk (prescriber override path)",

          "input": {"patient_id": "P001", "prescriber_id": "DR-17",

                    "prescription": {"drug": "ibuprofen", "dose_mg": 400, "frequency_per_day": 3, "route": "oral", "indication": "knee pain"}},

          "auto": [{"action": "override", "prescriber_id": "DR-17", "reason": "Short course, INR monitored twice weekly"},

                   {"action": "approve", "pharmacist_id": "PH-03"}]},

    "2": {"title": "Amoxicillin for a penicillin-allergic patient -> BLOCKED (must modify)",

          "input": {"patient_id": "P001", "prescriber_id": "DR-17",

                    "prescription": {"drug": "amoxicillin", "dose_mg": 500, "frequency_per_day": 3, "route": "oral", "indication": "sinusitis"}},

          "auto": [{"action": "override", "prescriber_id": "DR-17", "reason": "I know better"},      # denied (blocked)

                   ]},

    "3": {"title": "Amoxicillin for a healthy patient -> LOW risk (pharmacist only)",

          "input": {"patient_id": "P002", "prescriber_id": "DR-22",

                    "prescription": {"drug": "amoxicillin", "dose_mg": 500, "frequency_per_day": 3, "route": "oral", "indication": "sinusitis"}},

          "auto": [{"action": "approve", "pharmacist_id": "PH-03"}]},

    "4": {"title": "Blocked order that the doctor CHANGES to acetaminophen -> re-checked -> pharmacist approves",

          "input": {"patient_id": "P001", "prescriber_id": "DR-17",

                    "prescription": {"drug": "amoxicillin", "dose_mg": 500, "frequency_per_day": 3, "route": "oral", "indication": "sinusitis"}},

          "auto": [{"action": "modify", "prescriber_id": "DR-17",

                    "changes": {"drug": "acetaminophen", "dose_mg": 500, "frequency_per_day": 3, "indication": "pain"}},

                   {"action": "approve", "pharmacist_id": "PH-03"}]},

}





def show_review_payload(p: dict) -> None:

    rx = p["prescription"]

    print(f"\n{'=' * 70}\n  {p['message']}\n{'=' * 70}")

    print(f"  Rx: {rx['drug']} {rx['dose_mg']}mg x{rx['frequency_per_day']}/day ({rx['route']}) - {rx['indication']}")

    for c in p["checks"]:

        print(f"  [{c['check']:<12}] {c['severity'].upper()}")

        for f in c["findings"]:

            print(f"        - {f}")

    print(f"\n  AI summary: {p['ai_summary']}")

    for r in p["ai_recommendations"]:

        print(f"    * {r}")

    if p.get("override_reason"):

        print(f"\n  Prescriber override reason: {p['override_reason']}")

    print(f"  Allowed actions: {p['actions']}")





def console_human(payload: dict) -> dict:

    """Ask a real person in the terminal."""

    show_review_payload(payload)

    action = input("  Your decision > ").strip().lower()

    d: dict[str, Any] = {"action": action}

    if payload["type"] == "prescriber_review":

        d["prescriber_id"] = input("  Your prescriber id > ").strip()

    else:

        d["pharmacist_id"] = input("  Your pharmacist id > ").strip()

    if action == "modify":

        print("  Fields to change, e.g. drug=acetaminophen dose_mg=500 frequency_per_day=3")

        for kv in input("  changes > ").split():

            k, _, v = kv.partition("=")

            d.setdefault("changes", {})[k] = float(v) if k == "dose_mg" else int(v) if k == "frequency_per_day" else v

    elif action in ("override", "cancel", "reject"):

        d["reason"] = input("  Reason > ").strip()

    return d





def make_auto_human(script: list[dict]) -> Callable[[dict], dict]:

    """Scripted answers for demos / CI. If the script runs out, the prescriber cancels / pharmacist rejects."""

    it = iter(script)



    def _answer(payload: dict) -> dict:

        show_review_payload(payload)

        try:

            d = next(it)

        except StopIteration:

            d = {"action": "cancel" if payload["type"] == "prescriber_review" else "reject", "reason": "auto-script ended"}

        print(f"  [AUTO] human answers: {d}")

        return d



    return _answer





# ============================================================================

# MAIN

# ============================================================================

def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument("--scenario", choices=SCENARIOS.keys())

    parser.add_argument("--auto", action="store_true", help="use scripted human answers")

    args = parser.parse_args()



    setup_logging()



    sid = args.scenario

    if not sid:

        for k, v in SCENARIOS.items():

            print(f"  {k}. {v['title']}")

        sid = input("Pick a scenario > ").strip() or "1"

    sc = SCENARIOS[sid]

    log.info("SCENARIO  | Running example %s: %s", sid, sc["title"])



    graph = build_graph()

    # one thread_id == one prescription workflow (this is what FastAPI will return to the client)

    config = {"configurable": {"thread_id": f"rx-{sc['input']['patient_id']}-{uuid.uuid4().hex[:8]}"}}



    answer_fn = make_auto_human(sc["auto"]) if args.auto else console_human

    result = run_until_done(graph, sc["input"], config, answer_fn)



    print(f"\n{'#' * 70}\n  FINAL STATUS: {result.get('status')}   RISK: {result.get('risk_level')}   ORDER: {result.get('order_id')}")

    if result.get("patient_instructions"):

        print(f"\n  Patient instructions:\n{result['patient_instructions']}")

    print("\n  AUDIT TRAIL")

    for e in result.get("audit", []):

        print(f"   {e['ts']} | {e['actor']:<14} | {e['action']:<28} | {e['detail']}")



    print_history(graph, config)





if __name__ == "__main__":

    main()
