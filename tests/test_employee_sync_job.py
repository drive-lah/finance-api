"""Tests for employee sync job — src/jobs/employee_sync_job.py

TDD RED phase: all tests written before implementation.
Validates sync logic: create new, update mutable fields, offboard,
skip unchanged, protect immutable fields, error handling.
"""
import pytest
from datetime import date, datetime

from sqlalchemy import (
    create_engine, Table, Column,
    Integer as SAInteger, String as SAString, Boolean as SABoolean,
    Date as SADate, text,
)
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.hr_employee import HrEmployee
from src.models.counterparty import FinanceCounterparty


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def db_engine():
    """Create an in-memory SQLite engine with the full schema."""
    engine = create_engine("sqlite:///:memory:")

    # Stub 'users' table with onboarding columns from migration 034
    Table(
        "users", Base.metadata,
        Column("id", SAInteger, primary_key=True),
        Column("name", SAString(255), nullable=True),
        Column("email", SAString(255), nullable=True),
        Column("is_employee", SABoolean, nullable=True, default=False),
        Column("onboarding_status", SAString(20), nullable=True, default="PENDING"),
        Column("employee_type", SAString(20), nullable=True),
        Column("employment_end_date", SADate, nullable=True),
        Column("bank_account_number", SAString(50), nullable=True),
        Column("bank_code", SAString(20), nullable=True),
        Column("teams", SAString(500), nullable=True),
        extend_existing=True,
    )

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def entity(db_session):
    e = FinanceEntity(
        name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE,
    )
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def accounts(db_session):
    """Create COA accounts including the salary expense code 6000."""
    accs = [
        FinanceAccount(
            code="6000", name="Salary Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ),
    ]
    for a in accs:
        db_session.add(a)
    db_session.commit()
    return accs


def _insert_user(db_session, user_id, name="Test User", is_employee=False,
                 onboarding_status="PENDING", employee_type=None,
                 employment_end_date=None, bank_account_number=None,
                 bank_code=None, teams=None):
    """Insert a stub user row directly via SQL."""
    db_session.execute(
        text(
            "INSERT INTO users (id, name, email, is_employee, onboarding_status, "
            "employee_type, employment_end_date, bank_account_number, bank_code, teams) "
            "VALUES (:id, :name, :email, :is_employee, :onboarding_status, "
            ":employee_type, :employment_end_date, :bank_account_number, :bank_code, :teams)"
        ),
        {
            "id": user_id,
            "name": name,
            "email": f"user{user_id}@test.com",
            "is_employee": is_employee,
            "onboarding_status": onboarding_status,
            "employee_type": employee_type,
            "employment_end_date": employment_end_date,
            "bank_account_number": bank_account_number,
            "bank_code": bank_code,
            "teams": teams,
        },
    )
    db_session.commit()


def _create_hr_employee(db_session, user_id, entity_id, employee_type="FULL_TIME",
                        salary_expense_code="6000", employment_end_date=None):
    """Create an HrEmployee record."""
    emp = HrEmployee(
        user_id=user_id,
        entity_id=entity_id,
        employee_type=employee_type,
        salary_expense_code=salary_expense_code,
        employment_end_date=employment_end_date,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


# ============================================================================
# Import service under test
# ============================================================================

from src.jobs.employee_sync_job import sync_employees


# ============================================================================
# Happy Path: Create New HrEmployee
# ============================================================================

class TestSyncCreatesNewEmployees:

    def test_creates_hr_employee_for_new_onboarded_user(self, db_session, entity, accounts):
        """User with is_employee=True but no HrEmployee record gets one created."""
        _insert_user(db_session, 1, name="Alice", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME",
                     teams="Engineering")

        result = sync_employees(db_session)

        assert result["created"] >= 1
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 1).first()
        assert emp is not None
        assert emp.entity_id == entity.id
        assert emp.employee_type == "FULL_TIME"

    def test_creates_multiple_new_employees(self, db_session, entity, accounts):
        """Multiple onboarded users without HrEmployee records all get created."""
        for uid in [10, 11, 12]:
            _insert_user(db_session, uid, name=f"User{uid}", is_employee=True,
                         onboarding_status="COMPLETED", employee_type="FULL_TIME")

        result = sync_employees(db_session)

        assert result["created"] == 3
        count = db_session.query(HrEmployee).filter(
            HrEmployee.user_id.in_([10, 11, 12])
        ).count()
        assert count == 3

    def test_does_not_create_for_non_employee_users(self, db_session, entity, accounts):
        """Users with is_employee=False should be skipped entirely."""
        _insert_user(db_session, 20, name="NotEmployee", is_employee=False)

        result = sync_employees(db_session)

        assert result["created"] == 0
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 20).first()
        assert emp is None


# ============================================================================
# Happy Path: Update Mutable Fields
# ============================================================================

class TestSyncUpdatesMutableFields:

    def test_updates_employee_type_when_changed(self, db_session, entity, accounts):
        """If users.employee_type differs from HrEmployee.employee_type, update it."""
        _insert_user(db_session, 30, name="Bob", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="CONTRACTOR")
        _create_hr_employee(db_session, 30, entity.id, employee_type="FULL_TIME")

        result = sync_employees(db_session)

        assert result["updated"] >= 1
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 30).first()
        assert emp.employee_type == "CONTRACTOR"

    def test_updates_employment_end_date_for_offboarded(self, db_session, entity, accounts):
        """If users.employment_end_date is set, sync it to HrEmployee."""
        end_date = date(2026, 3, 31)
        _insert_user(db_session, 31, name="Leaving", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME",
                     employment_end_date=end_date)
        _create_hr_employee(db_session, 31, entity.id)

        result = sync_employees(db_session)

        assert result["offboarded"] >= 1
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 31).first()
        assert emp.employment_end_date == end_date


# ============================================================================
# Immutable Fields: salary_expense_code and entity_id
# ============================================================================

class TestSyncProtectsImmutableFields:

    def test_does_not_overwrite_salary_expense_code(self, db_session, entity, accounts):
        """salary_expense_code is set at onboarding and must never be overwritten by sync."""
        _insert_user(db_session, 40, name="Protected", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME")
        emp = _create_hr_employee(db_session, 40, entity.id, salary_expense_code="5063")

        result = sync_employees(db_session)

        emp_after = db_session.query(HrEmployee).filter(HrEmployee.user_id == 40).first()
        assert emp_after.salary_expense_code == "5063"  # unchanged

    def test_does_not_overwrite_entity_id(self, db_session, entity, accounts):
        """entity_id (payroll entity) is immutable after onboarding."""
        # Create a second entity
        e2 = FinanceEntity(
            name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE,
        )
        db_session.add(e2)
        db_session.commit()
        db_session.refresh(e2)

        _insert_user(db_session, 41, name="EntityProtected", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME")
        _create_hr_employee(db_session, 41, entity.id)

        result = sync_employees(db_session)

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 41).first()
        assert emp.entity_id == entity.id  # unchanged, stays original


# ============================================================================
# Skips Unchanged Employees
# ============================================================================

class TestSyncSkipsUnchanged:

    def test_unchanged_employee_not_counted_as_updated(self, db_session, entity, accounts):
        """Employee with no field changes should not be in the 'updated' count."""
        _insert_user(db_session, 50, name="NoChange", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME")
        _create_hr_employee(db_session, 50, entity.id, employee_type="FULL_TIME")

        result = sync_employees(db_session)

        assert result["updated"] == 0
        assert result["created"] == 0


# ============================================================================
# Offboarding
# ============================================================================

class TestSyncOffboarding:

    def test_marks_employee_as_offboarded(self, db_session, entity, accounts):
        """User with employment_end_date set gets HrEmployee updated."""
        end_date = date(2026, 4, 1)
        _insert_user(db_session, 60, name="Offboarded", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME",
                     employment_end_date=end_date)
        _create_hr_employee(db_session, 60, entity.id)

        result = sync_employees(db_session)

        assert result["offboarded"] >= 1
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 60).first()
        assert emp.employment_end_date == end_date

    def test_already_offboarded_employee_not_counted_again(self, db_session, entity, accounts):
        """If HrEmployee already has the same employment_end_date, don't re-count."""
        end_date = date(2026, 4, 1)
        _insert_user(db_session, 61, name="AlreadyOff", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME",
                     employment_end_date=end_date)
        _create_hr_employee(db_session, 61, entity.id, employment_end_date=end_date)

        result = sync_employees(db_session)

        assert result["offboarded"] == 0
        assert result["updated"] == 0


# ============================================================================
# Error Handling
# ============================================================================

class TestSyncErrorHandling:

    def test_returns_summary_dict(self, db_session, entity, accounts):
        """sync_employees always returns a summary dict with expected keys."""
        result = sync_employees(db_session)

        assert "synced" in result
        assert "created" in result
        assert "updated" in result
        assert "offboarded" in result
        assert "errors" in result
        assert isinstance(result["errors"], list)

    def test_empty_database_returns_zero_counts(self, db_session, entity, accounts):
        """No employees to sync returns all zeros."""
        result = sync_employees(db_session)

        assert result["synced"] == 0
        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["offboarded"] == 0
        assert result["errors"] == []


# ============================================================================
# Mixed Scenario
# ============================================================================

class TestSyncMixedScenario:

    def test_mixed_create_update_offboard_skip(self, db_session, entity, accounts):
        """
        Scenario with 5 employees:
        - User 100: new employee, no HrEmployee => create
        - User 101: existing, employee_type changed => update
        - User 102: existing, employment_end_date set => offboard
        - User 103: existing, no changes => skip
        - User 104: not an employee (is_employee=False) => skip
        """
        # User 100: new employee
        _insert_user(db_session, 100, name="NewGuy", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME")

        # User 101: type changed
        _insert_user(db_session, 101, name="TypeChange", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="CONTRACTOR")
        _create_hr_employee(db_session, 101, entity.id, employee_type="FULL_TIME")

        # User 102: offboarded
        _insert_user(db_session, 102, name="Leaving", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME",
                     employment_end_date=date(2026, 3, 31))
        _create_hr_employee(db_session, 102, entity.id)

        # User 103: no changes
        _insert_user(db_session, 103, name="Stable", is_employee=True,
                     onboarding_status="COMPLETED", employee_type="FULL_TIME")
        _create_hr_employee(db_session, 103, entity.id, employee_type="FULL_TIME")

        # User 104: not an employee
        _insert_user(db_session, 104, name="NotEmployee", is_employee=False)

        result = sync_employees(db_session)

        assert result["created"] == 1   # User 100
        assert result["updated"] == 2   # User 101 (type change) + User 102 (offboarded)
        assert result["offboarded"] == 1  # User 102
        assert result["synced"] == 3     # created + updated
        assert result["errors"] == []


# ============================================================================
# Route Tests
# ============================================================================

class TestSyncEmployeesRoute:
    """Test POST /api/jobs/sync-employees HTTP endpoint."""

    @pytest.fixture
    def app(self, db_engine):
        from src.app import create_app
        import src.database as db_mod

        app = create_app()
        original_db_session = db_mod.db_session
        Session = sessionmaker(bind=db_engine)

        from contextlib import contextmanager

        @contextmanager
        def test_db_session():
            session = Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        db_mod.db_session = test_db_session
        yield app
        db_mod.db_session = original_db_session

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_route_exists(self, client):
        """POST /api/jobs/sync-employees should not return 404."""
        response = client.post("/api/jobs/sync-employees")
        assert response.status_code != 404

    def test_returns_sync_summary(self, client, db_engine):
        """Route returns JSON with sync summary."""
        Session = sessionmaker(bind=db_engine)
        session = Session()

        # Seed entity
        e = FinanceEntity(
            name="Route Test Entity", country="SG", base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        session.add(e)
        session.flush()

        # Seed a user
        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status, employee_type) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status, :employee_type)"
            ),
            {"id": 500, "name": "RouteUser", "email": "route@test.com",
             "is_employee": True, "onboarding_status": "COMPLETED",
             "employee_type": "FULL_TIME"},
        )
        session.commit()
        session.close()

        response = client.post("/api/jobs/sync-employees")
        assert response.status_code == 200
        data = response.get_json()
        assert "synced" in data
        assert "created" in data
        assert "updated" in data
        assert "offboarded" in data
        assert "errors" in data
