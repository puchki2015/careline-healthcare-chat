"""Low-level service-role queries for the appointment coordinator."""

from datetime import UTC, datetime
from typing import Any

from supabase import Client


class AppointmentRepository:
    """Keep all appointment database access in one narrow gateway."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def select_patient_appointments(self, patient_id: str, *, include_past: bool) -> object:
        query = (
            self._client.table("appointments")
            .select(
                "id,starts_at,ends_at,status,reason,cancellation_reason,"
                "provider_availability(location_type,location),"
                "doctor_profiles(id,display_name,specialty)"
            )
            .eq("patient_id", patient_id)
        )
        if not include_past:
            # Status filtering keeps this stable in tests and ensures cancelled
            # records are not described as upcoming by the agent.
            query = query.eq("status", "scheduled")
        return query.order("starts_at").limit(100).execute().data

    def select_availability(
        self,
        *,
        starts_at: str,
        ends_at: str,
        specialty: str | None,
        doctor_name: str | None,
    ) -> object:
        query = (
            self._client.table("provider_availability")
            .select(
                "id,starts_at,ends_at,location_type,location,"
                "doctor_profiles!inner(id,display_name,specialty,is_active)"
            )
            .eq("status", "available")
            .gte("starts_at", starts_at)
            .lte("starts_at", ends_at)
            .eq("doctor_profiles.is_active", True)
        )
        if specialty:
            query = query.ilike("doctor_profiles.specialty", specialty)
        if doctor_name:
            query = query.ilike("doctor_profiles.display_name", f"%{doctor_name}%")
        return query.order("starts_at").limit(50).execute().data

    def select_available_slot(self, slot_id: str) -> object:
        """Resolve one exact selectable slot for a mutation proposal."""

        return (
            self._client.table("provider_availability")
            .select(
                "id,starts_at,ends_at,location_type,location,"
                "doctor_profiles!inner(id,display_name,specialty,is_active)"
            )
            .eq("id", slot_id)
            .eq("status", "available")
            .eq("doctor_profiles.is_active", True)
            .limit(1)
            .execute()
            .data
        )

    def mutate(self, function_name: str, values: dict[str, Any]) -> object:
        """Run one database transaction for booking/rescheduling/cancellation."""

        return self._client.rpc(function_name, values).execute().data

    # Administrator schedule queries use this same service-role gateway, but
    # their service functions require CurrentAdmin before calling them.
    def select_doctors(self, *, include_inactive: bool) -> object:
        query = self._client.table("doctor_profiles").select("*")
        if not include_inactive:
            query = query.eq("is_active", True)
        return query.order("display_name").execute().data

    def insert_doctor(self, values: dict[str, Any]) -> object:
        return self._client.table("doctor_profiles").insert(values).execute().data

    def update_doctor(self, doctor_id: str, values: dict[str, Any]) -> object:
        return (
            self._client.table("doctor_profiles").update(values).eq("id", doctor_id).execute().data
        )

    def select_admin_availability(
        self, *, doctor_id: str | None, starts_at: str | None, ends_at: str | None
    ) -> object:
        query = self._client.table("provider_availability").select(
            "id,doctor_id,starts_at,ends_at,status,location_type,location,created_at,updated_at,"
            "doctor_profiles(id,display_name,specialty,timezone,is_active,created_at,updated_at)"
        )
        if doctor_id:
            query = query.eq("doctor_id", doctor_id)
        if starts_at:
            query = query.gte("starts_at", starts_at)
        if ends_at:
            query = query.lte("starts_at", ends_at)
        return query.order("starts_at").limit(500).execute().data

    def insert_availability(self, values: dict[str, Any]) -> object:
        return self._client.table("provider_availability").insert(values).execute().data

    def update_availability_status(self, slot_id: str, status: str) -> object:
        return (
            self._client.table("provider_availability")
            .update({"status": status, "updated_at": datetime.now(UTC).isoformat()})
            .eq("id", slot_id)
            .neq("status", "booked")
            .execute()
            .data
        )

    def select_admin_appointments(
        self,
        *,
        status: str | None,
        doctor_id: str | None,
        patient_id: str | None,
        starts_at: str | None,
        ends_at: str | None,
    ) -> object:
        query = self._client.table("appointments").select(
            "id,patient_id,starts_at,ends_at,status,reason,cancellation_reason,created_at,"
            "patients(full_name,email),provider_availability(location_type,location),"
            "doctor_profiles(id,display_name,specialty,timezone,is_active,created_at,updated_at)"
        )
        if status:
            query = query.eq("status", status)
        if doctor_id:
            query = query.eq("doctor_id", doctor_id)
        if patient_id:
            query = query.eq("patient_id", patient_id)
        if starts_at:
            query = query.gte("starts_at", starts_at)
        if ends_at:
            query = query.lte("starts_at", ends_at)
        return query.order("starts_at", desc=True).limit(500).execute().data
