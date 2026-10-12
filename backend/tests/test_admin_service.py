"""Authorization and invitation safety tests for the administrator feature."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.admin import PatientInvitationDetails
from app.services.admin_service import (
    AdminAuthorizationError,
    PatientInvitationServiceError,
    invite_patient,
    require_active_admin,
)


class AdminRepository:
    def __init__(self, user_id) -> None:
        self.user_id = user_id
        self.profile_rows = [
            {
                "id": str(user_id),
                "email": "admin@example.com",
                "display_name": "Careline Admin",
                "is_active": True,
            }
        ]
        self.role_rows = [{"user_id": str(user_id), "role": "admin", "is_active": True}]

    def select_admin_profile(self, _: str):
        return self.profile_rows

    def select_active_role(self, _: str, role: str):
        assert role == "admin"
        return self.role_rows


def test_admin_requires_both_profile_and_active_role() -> None:
    user_id = uuid4()
    repository = AdminRepository(user_id)
    assert require_active_admin(repository, user_id).id == user_id  # type: ignore[arg-type]

    repository.role_rows = []
    with pytest.raises(AdminAuthorizationError):
        require_active_admin(repository, user_id)  # type: ignore[arg-type]


class PatientTable:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.values = None

    def update(self, values):
        self.values = values
        return self

    def eq(self, *_):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class InvitationClient:
    def __init__(self, patient_id, rows) -> None:
        self.patient_id = patient_id
        self.table_query = PatientTable(rows)
        self.invite_args = None
        self.deleted = []
        self.auth = SimpleNamespace(
            admin=SimpleNamespace(
                invite_user_by_email=self.invite_user_by_email,
                delete_user=self.delete_user,
            )
        )

    def invite_user_by_email(self, email, options):
        self.invite_args = (email, options)
        return SimpleNamespace(user=SimpleNamespace(id=str(self.patient_id)))

    def delete_user(self, user_id):
        self.deleted.append(user_id)

    def table(self, name):
        assert name == "patients"
        return self.table_query


def invitation_details() -> PatientInvitationDetails:
    return PatientInvitationDetails(
        full_name="Jamie Patient",
        date_of_birth=date(1990, 5, 12),
        phone="+16045550123",
        email="jamie@example.com",
    )


def test_invitation_sends_email_and_completes_trigger_created_profile() -> None:
    patient_id = uuid4()
    client = InvitationClient(patient_id, [{"id": str(patient_id)}])
    repository = SimpleNamespace(client=client)

    result = invite_patient(
        repository,  # type: ignore[arg-type]
        invitation_details(),
        redirect_to="http://localhost:5173/set-password",
    )

    assert result == patient_id
    assert client.invite_args == (
        "jamie@example.com",
        {
            "data": {"full_name": "Jamie Patient"},
            "redirect_to": "http://localhost:5173/set-password",
        },
    )
    assert client.table_query.values == {
        "full_name": "Jamie Patient",
        "date_of_birth": "1990-05-12",
        "phone": "+16045550123",
    }
    assert client.deleted == []


def test_invitation_compensates_when_profile_completion_fails() -> None:
    patient_id = uuid4()
    client = InvitationClient(patient_id, [])
    repository = SimpleNamespace(client=client)

    with pytest.raises(PatientInvitationServiceError):
        invite_patient(
            repository,  # type: ignore[arg-type]
            invitation_details(),
            redirect_to="http://localhost:5173/set-password",
        )

    assert client.deleted == [str(patient_id)]
