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

from pydantic import BaseModel, Field, ValidationError, ConfigDict



load_dotenv()

def get_llm() -> ChatOpenAI:

    """LLM factory (a function so tests can monkeypatch it)."""

    if not os.getenv("OPENAI_API_KEY"):

        raise RuntimeError("OPENAI_API_KEY is not set (add it to .env)")

    return ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0.2)



import logging

from datetime import datetime, timezone

log = logging.getLogger(__name__)


def audit(
    actor: str,
    action: str,
    detail: str,
) -> list[AuditEvent]:
    """
    Create and log an audit event.

    Returns a one-element list for the
    LangGraph audit reducer.
    """

    event = AuditEvent(
        ts=datetime.now(timezone.utc),
        actor=actor,
        action=action,
        detail=detail,
    )

    log.info(
        "AUDIT | actor=%s | action=%s | detail=%s",
        event.actor,
        event.action,
        event.detail,
    )

    return [event]



##===============================================##
class Patient(BaseModel):
    """Patient information used for medication safety checks."""

    model_config = ConfigDict(
        validate_assignment=True,  # it helps prevent invalid values from being assigned to patient fields after the patient object has been created.
        extra="forbid", # Reject the inclusion of any fields not explicitly defined in the model.
        str_strip_whitespace=True, # Automatically remove leading and trailing whitespace from string fields
    )

    patient_id: str = Field(
        ..., # It means required field, must be provided when creating a Patient instance.
        description="Unique identifier for the patient",
        min_length=1,
        examples=["P001"],
    )

    age: int = Field(
        ...,
        description="Patient's age in completed years",
        ge=0,
        le=120,
        examples=[45],
    )

    weight_kg: float = Field(
        ...,
        description="Patient's body weight in kilograms",
        gt=0,
        le=500,
        examples=[72.5],
    )

    egfr: float = Field(
        ...,
        description="Estimated glomerular filtration rate in mL/min/1.73 m²",
        ge=0,
        examples=[85.0],
    )

    allergies: list[str] = Field(
        default_factory=list,
        description="Known medication or substance allergies",
        examples=[["Penicillin", "Sulfa"]],
    )

    current_meds: list[str] = Field(
        default_factory=list,
        description="Medications currently taken by the patient",
        examples=[["Metformin", "Lisinopril"]],
    )

    conditions: list[str] = Field(
        default_factory=list,
        description="Known medical conditions and diagnoses",
        examples=[["Type 2 Diabetes", "Hypertension"]],
    )

    recent_orders: list[str] = Field(
        default_factory=list,
        description="Recent medication orders retrieved from the long-term Store [F11]",
        examples=[["Amoxicillin 500 mg"]],
    )



class Prescription(BaseModel):
    """Represents a medication prescription for safety validation."""

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    drug: str = Field(
        ...,
        description="Name of the prescribed medication",
        min_length=1,
        examples=["Amoxicillin"],
    )

    dose_mg: float = Field(
        ...,
        description="Medication dose in milligrams per administration",
        gt=0,
        examples=[500.0],
    )

    frequency_per_day: int = Field(
        ...,
        description="Number of times the medication is taken per day",
        ge=1,
        le=24,
        examples=[3],
    )

    route: str = Field(
        ...,
        description="Route through which the medication is administered",
        min_length=1,
        examples=["oral", "intravenous", "intramuscular"],
    )

    indication: str = Field(
        ...,
        description="Medical condition or reason for prescribing the medication",
        min_length=1,
        examples=["Bacterial infection"],
    )



# Allowed severity levels
Severity = Literal["none", "low", "medium", "high", "critical"]


class CheckResult(BaseModel):
    """Represents the result of a medication safety check performed by a worker."""

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    check: str = Field(
        ...,
        description="Name of the worker or safety check that produced this result",
        min_length=1,
        examples=["allergy_check", "renal_check", "interaction_check"],
    )

    revision: int = Field(
        ...,
        description="Prescription revision number to which this check result belongs",
        ge=0,
        examples=[1],
    )

    severity: Severity = Field(
        ...,
        description="Severity level of the safety issue identified by the worker",
        examples=["high"],
    )

    findings: list[str] = Field(
        default_factory=list,
        description="List of findings, warnings, or observations produced by the safety check",
        examples=[
            ["Patient has a documented allergy to the prescribed medication"]
        ],
    )



class AuditEvent(BaseModel):
    """Represents an audit log entry in the medication safety workflow."""

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    ts: datetime = Field(
        ...,
        description="Timestamp of the audit event, including timezone information",
        examples=["2026-10-09T14:30:00Z"],
    )

    actor: str = Field(
        ...,
        description=(
            "Entity that performed the action. "
            "Examples: system, ai, prescriber:<id>, pharmacist:<id>"
        ),
        pattern=r"^(system|ai|prescriber:[^:\s]+|pharmacist:[^:\s]+)$",
        examples=[
            "system",
            "ai",
            "prescriber:123",
            "pharmacist:456",
        ],
    )

    action: str = Field(
        ...,
        description="Name of the action performed during the workflow",
        min_length=1,
        examples=[
            "prescription_created",
            "safety_check_completed",
            "prescription_revised",
            "pharmacist_approved",
        ],
    )

    detail: str = Field(
        ...,
        description="Detailed explanation of the action or event",
        min_length=1,
        examples=[
            "Allergy safety check completed for prescription revision 1"
        ],
    )


class InputState(BaseModel):
    """
    Represents the initial input provided by the API caller
    to the LangGraph medication safety workflow.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    patient_id: str = Field(
        ...,
        description="Unique identifier of the patient receiving the prescription",
        min_length=1,
        examples=["P001"],
    )

    prescriber_id: str = Field(
        ...,
        description="Unique identifier of the healthcare professional prescribing the medication",
        min_length=1,
        examples=["DR123"],
    )

    prescription: Prescription = Field(
        ...,
        description="Prescription details to be evaluated by the medication safety workflow",
        examples=[
            {
                "drug": "Amoxicillin",
                "dose_mg": 500.0,
                "frequency_per_day": 3,
                "route": "oral",
                "indication": "Bacterial infection",
            }
        ],
    )



    
from pydantic import BaseModel, Field, ConfigDict


class OutputState(BaseModel):
    """
    Represents the final response returned to the API caller
    after the LangGraph medication safety workflow completes.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    status: str | None = Field(
        default=None,
        description="Final processing status of the prescription workflow",
        examples=["approved", "rejected", "pending_review"],
    )

    risk_level: str | None = Field(
        default=None,
        description="Overall medication safety risk level",
        examples=["none", "low", "medium", "high", "critical"],
    )

    order_id: str | None = Field(
        default=None,
        description="Unique identifier of the medication order, if created",
        examples=["ORD-1001"],
    )

    patient_instructions: str | None = Field(
        default=None,
        description="Patient-facing medication instructions generated by the workflow",
        examples=["Take as directed by your prescriber."],
    )

    audit: list[AuditEvent] = Field(
        default_factory=list,  # default to empty list if no audit events are recorded
        description="Chronological list of audit events recorded during the workflow",
    )


    
import operator

from typing import Annotated, Literal, TypedDict
from pydantic import Field

#For your LangGraph internal State, I recommend keeping TypedDict with Annotated reducers rather
#  than converting everything to BaseModel.
# The reason is that LangGraph uses Annotated[..., operator.add] to merge updates from 
# parallel workers. This is especially important for your checks and audit fields.

class State(TypedDict, total=False):
    """
    Full internal state of the LangGraph medication safety workflow.

    Tracks input data, patient context, parallel safety checks,
    AI assessments, prescription outcomes, and audit history.
    """

    # --------------------------------------------------
    # 1. INPUTS
    # --------------------------------------------------

    patient_id: Annotated[
        str,
        Field(
            description="Unique identifier of the patient",
            min_length=1,
            examples=["P001"],
        ),
    ]

    prescriber_id: Annotated[
        str,
        Field(
            description="Unique identifier of the prescribing clinician",
            min_length=1,
            examples=["DR123"],
        ),
    ]

    prescription: Annotated[
        Prescription,
        Field(
            description="Prescription submitted for medication safety evaluation",
        ),
    ]

    # --------------------------------------------------
    # 2. LOADED PATIENT CONTEXT
    # --------------------------------------------------

    patient: Annotated[
        Patient,
        Field(
            description="Patient information loaded from the database or Store",
        ),
    ]

    revision: Annotated[
        int,
        Field(
            description="Current prescription revision number",
            ge=0,
            examples=[1],
        ),
    ]

    # --------------------------------------------------
    # 3. PARALLEL SAFETY CHECK RESULTS
    # --------------------------------------------------

    checks: Annotated[
        list[CheckResult],
        operator.add,
    ]

    # --------------------------------------------------
    # 4. AI RISK ASSESSMENT
    # --------------------------------------------------

    risk_level: Annotated[
        Literal["low", "medium", "high", "blocked"],
        Field(
            description="Overall medication safety risk level",
            examples=["high"],
        ),
    ]

    ai_summary: Annotated[
        str,
        Field(
            description="AI-generated summary of medication safety findings",
            examples=["Potential allergy risk identified"],
        ),
    ]

    ai_recommendations: Annotated[
        list[str],
        Field(
            description="AI-generated recommendations for clinician review",
            examples=[["Review documented medication allergies"]],
        ),
    ]

    override_reason: Annotated[
        str,
        Field(
            description="Documented reason for an authorized clinical override",
            examples=["Reviewed and approved by the responsible clinician"],
        ),
    ]

    # --------------------------------------------------
    # 5. PRESCRIPTION OUTCOME
    # --------------------------------------------------

    status: Annotated[
        str,
        Field(
            description="Current or final prescription workflow status",
            examples=["pending_review", "approved", "rejected"],
        ),
    ]

    order_id: Annotated[
        str,
        Field(
            description="Identifier of the medication order if created",
            examples=["ORD-1001"],
        ),
    ]

    patient_instructions: Annotated[
        str,
        Field(
            description="Patient-facing instructions approved for release",
        ),
    ]

    # --------------------------------------------------
    # 6. AUDIT TRAIL
    # --------------------------------------------------

    audit: Annotated[
        list[AuditEvent],
        operator.add,
    ]



class CheckInput(BaseModel):
    """
    Input payload passed to each parallel medication safety
    check worker through LangGraph's Send() function.

    Contains only the information required by the worker,
    rather than the complete internal graph State.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    patient: Patient = Field(
        ...,
        description=(
            "Patient information required for medication safety "
            "checks, including age, weight, kidney function, "
            "allergies, medications, and medical conditions."
        ),
    )

    prescription: Prescription = Field(
        ...,
        description=(
            "Prescription being evaluated, including medication "
            "name, dose, frequency, route, and indication."
        ),
    )

    revision: int = Field(
        ...,
        description=(
            "Prescription revision number associated with "
            "this safety check. Used to identify stale results."
        ),
        ge=0,
        examples=[1],
    )




from pydantic import BaseModel, Field, ConfigDict


class PrescriptionModel(BaseModel):
    """
    Validates incoming prescription data before
    executing medication safety checks.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    drug: str = Field(
        ...,
        description="Name of the prescribed medication",
        min_length=1,
        examples=["Amoxicillin"],
    )

    dose_mg: float = Field(
        ...,
        description="Medication dosage in milligrams per single administration",
        gt=0,
        examples=[500.0],
    )

    frequency_per_day: int = Field(
        ...,
        description="Number of times the medication is administered per day",
        ge=1,
        le=6,
        examples=[3],
    )

    route: str = Field(
        default="oral",
        description="Route of medication administration",
        min_length=1,
        examples=["oral", "intravenous", "intramuscular"],
    )

    indication: str = Field(
        default="",
        description="Medical condition or reason for prescribing the medication",
        examples=["Bacterial infection"],
    )



from pydantic import BaseModel, Field, ConfigDict


class ClinicalSummary(BaseModel):
    """
    Structured output generated by the LLM for pharmacist review.

    The LLM summarizes medication safety findings and provides
    recommendations. It does NOT determine the risk level.

    Risk classification is handled separately by deterministic
    clinical safety rules.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    summary: str = Field(
        ...,
        description=(
            "A concise, plain-English clinical summary of medication "
            "safety findings for pharmacist review. "
            "Prefer 3 to 4 sentences. Include relevant allergy, "
            "renal dosing, drug interaction, and other safety findings "
            "when applicable. Do not assign a risk level."
        ),
        min_length=1,
        examples=[
            "The patient has a documented penicillin allergy. "
            "The prescribed medication requires allergy review. "
            "Kidney function and current medications should also "
            "be considered before dispensing."
        ],
    )

    recommendations: list[str] = Field(
        ...,
        description=(
            "Up to four specific, actionable recommendations "
            "for pharmacist or prescriber review. "
            "Suggestions may include alternative medications, "
            "dose adjustments, monitoring, or additional assessment. "
            "Do not independently authorize prescription changes."
        ),
        min_length=0,
        max_length=4,
        examples=[
            [
                "Verify the documented allergy and reaction history.",
                "Review clinically appropriate alternative medications.",
                "Confirm renal dosing requirements.",
            ]
        ],
    )












