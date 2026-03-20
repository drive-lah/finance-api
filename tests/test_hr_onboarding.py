"""Tests for POST /api/hr/onboard/bulk — bulk employee onboarding endpoint.

TDD RED phase: all tests written before implementation.
Validates happy path, validation failures, conflicts, and transaction rollback.
"""
import pytest
from datetime import date

from sqlalchemy import create_engine, Table, Column, Integer as SAInteger, String as SAString, Boolean as SABoolean, Date as SADate, text
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

    # The users table is in admin-bff; we stub it here with the onboarding columns
    # added by migration 034.
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
        FinanceAccount(
            code="5063", name="Customer Support Salary",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ),
    ]
    for a in accs:
        db_session.add(a)
    db_session.commit()
    return accs


def _insert_user(db_session, user_id, name="Test User", is_employee=False,
                 onboarding_status="PENDING"):
    """Insert a stub user row directly via SQL (users table is from admin-bff)."""
    db_session.execute(
        text(
            "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
            "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
        ),
        {
            "id": user_id,
            "name": name,
            "email": f"user{user_id}@test.com",
            "is_employee": is_employee,
            "onboarding_status": onboarding_status,
        },
    )
    db_session.commit()


# ============================================================================
# Import service under test
# ============================================================================

from src.services.hr_onboarding_service import hr_onboarding_service


# ============================================================================
# Happy Path Tests
# ============================================================================


class TestBulkOnboardHappyPath:

    def test_onboard_single_employee(self, db_session, entity, accounts):
        """Onboarding a single valid user creates HrEmployee + FinanceCounterparty."""
        _insert_user(db_session, 5, name="Alice")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 5,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
                "bank_account_number": "1001-1234-5678",
                "bank_code": "OCBCSGSG",
            }
        ])

        assert result["success"] is True
        assert result["onboarded_count"] == 1
        assert result["errors"] == []

        # Verify HrEmployee created
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 5).first()
        assert emp is not None
        assert emp.entity_id == entity.id
        assert emp.salary_expense_code == "6000"
        assert emp.employee_type == "FULL_TIME"

        # Verify FinanceCounterparty created
        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.name == "Alice",
            FinanceCounterparty.type == "employee",
        ).first()
        assert cp is not None
        assert cp.entity_id == entity.id

        # Verify users table updated
        row = db_session.execute(
            text("SELECT is_employee, onboarding_status, employee_type, bank_account_number, bank_code FROM users WHERE id = :id"),
            {"id": 5},
        ).fetchone()
        assert bool(row[0]) is True  # is_employee
        assert row[1] == "COMPLETED"  # onboarding_status
        assert row[2] == "FULL_TIME"
        assert row[3] == "1001-1234-5678"
        assert row[4] == "OCBCSGSG"

    def test_onboard_multiple_employees(self, db_session, entity, accounts):
        """Batch of 3 valid employees all get onboarded."""
        for uid in [10, 11, 12]:
            _insert_user(db_session, uid, name=f"User{uid}")

        payload = [
            {
                "user_id": uid,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
            for uid in [10, 11, 12]
        ]

        result = hr_onboarding_service.bulk_onboard(db_session, payload)
        assert result["success"] is True
        assert result["onboarded_count"] == 3
        assert result["errors"] == []

        # All 3 HrEmployee records created
        count = db_session.query(HrEmployee).filter(
            HrEmployee.user_id.in_([10, 11, 12])
        ).count()
        assert count == 3

    def test_onboard_with_optional_fields_missing(self, db_session, entity, accounts):
        """bank_account_number and bank_code are optional."""
        _insert_user(db_session, 20, name="NoBank")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 20,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
                # No bank_account_number, no bank_code
            }
        ])

        assert result["success"] is True
        assert result["onboarded_count"] == 1

    def test_counterparty_not_duplicated_if_exists(self, db_session, entity, accounts):
        """If a FinanceCounterparty already exists for the user, don't create a duplicate."""
        _insert_user(db_session, 30, name="ExistingCP")

        # Pre-create counterparty
        cp = FinanceCounterparty(
            name="ExistingCP", type="employee", entity_id=entity.id,
            external_id="30", external_system="users",
        )
        db_session.add(cp)
        db_session.commit()

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 30,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert result["success"] is True
        assert result["onboarded_count"] == 1

        # Only 1 counterparty with external_id=30
        cp_count = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "30",
            FinanceCounterparty.external_system == "users",
        ).count()
        assert cp_count == 1


# ============================================================================
# Validation Error Tests
# ============================================================================


class TestBulkOnboardValidation:

    def test_missing_user_id_returns_error(self, db_session, entity, accounts):
        """Each item must have user_id."""
        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert result["success"] is False
        assert result["onboarded_count"] == 0
        assert len(result["errors"]) == 1
        assert "user_id" in result["errors"][0]["message"].lower()

    def test_user_not_found_returns_error(self, db_session, entity, accounts):
        """User ID that doesn't exist in users table."""
        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 9999,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert result["success"] is False
        assert result["onboarded_count"] == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["user_id"] == 9999
        assert "not found" in result["errors"][0]["message"].lower()

    def test_invalid_entity_id_returns_error(self, db_session, entity, accounts):
        """payroll_entity_id must reference a valid entity."""
        _insert_user(db_session, 40, name="BadEntity")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 40,
                "payroll_entity_id": 9999,  # doesn't exist
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert "entity" in result["errors"][0]["message"].lower()

    def test_invalid_salary_expense_code_returns_error(self, db_session, entity, accounts):
        """salary_expense_code must be a valid COA account code."""
        _insert_user(db_session, 41, name="BadCOA")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 41,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "9999",  # doesn't exist in COA
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert "salary_expense_code" in result["errors"][0]["message"].lower() or \
               "coa" in result["errors"][0]["message"].lower()

    def test_duplicate_user_ids_in_batch_returns_error(self, db_session, entity, accounts):
        """Duplicate user_id within the same batch should fail."""
        _insert_user(db_session, 50, name="Dupe")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 50,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
            {
                "user_id": 50,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
        ])

        assert result["success"] is False
        assert any("duplicate" in e["message"].lower() for e in result["errors"])


# ============================================================================
# Conflict Tests
# ============================================================================


class TestBulkOnboardConflict:

    def test_already_onboarded_user_returns_conflict(self, db_session, entity, accounts):
        """User with is_employee=true should return 409-style conflict error."""
        _insert_user(db_session, 60, name="AlreadyOnboarded", is_employee=True,
                     onboarding_status="COMPLETED")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 60,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["user_id"] == 60
        assert "already onboarded" in result["errors"][0]["message"].lower()


# ============================================================================
# Transaction Rollback Tests
# ============================================================================


class TestBulkOnboardRollback:

    def test_one_invalid_user_rolls_back_entire_batch(self, db_session, entity, accounts):
        """If any user in the batch fails validation, ALL changes roll back."""
        _insert_user(db_session, 70, name="Valid1")
        _insert_user(db_session, 71, name="Valid2")
        # user_id 72 does NOT exist

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 70,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
            {
                "user_id": 71,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
            {
                "user_id": 72,  # doesn't exist
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
        ])

        assert result["success"] is False
        # None of the valid users should have been committed
        emp70 = db_session.query(HrEmployee).filter(HrEmployee.user_id == 70).first()
        assert emp70 is None, "Rollback should have removed user 70's HrEmployee record"

        emp71 = db_session.query(HrEmployee).filter(HrEmployee.user_id == 71).first()
        assert emp71 is None, "Rollback should have removed user 71's HrEmployee record"

    def test_already_onboarded_in_batch_rolls_back(self, db_session, entity, accounts):
        """If one user is already onboarded, the entire batch is rolled back."""
        _insert_user(db_session, 80, name="ValidUser")
        _insert_user(db_session, 81, name="OnboardedUser", is_employee=True,
                     onboarding_status="COMPLETED")

        result = hr_onboarding_service.bulk_onboard(db_session, [
            {
                "user_id": 80,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
            {
                "user_id": 81,
                "payroll_entity_id": entity.id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            },
        ])

        assert result["success"] is False
        emp80 = db_session.query(HrEmployee).filter(HrEmployee.user_id == 80).first()
        assert emp80 is None


# ============================================================================
# Route / HTTP-Level Tests
# ============================================================================


class TestBulkOnboardRoute:
    """Test the HTTP route layer via Flask test client."""

    @pytest.fixture
    def app(self, db_engine):
        """Create a Flask app wired to the test database."""
        from src.app import create_app
        import src.database as db_mod

        app = create_app()

        # Monkey-patch the db_session context manager to use our test engine
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

    def test_route_exists(self, client, db_engine):
        """POST /api/hr/onboard/bulk should not return 404."""
        response = client.post("/api/hr/onboard/bulk", json=[])
        # Empty array should give 400, not 404
        assert response.status_code != 404

    def test_non_json_body_returns_400(self, client):
        """Non-JSON body should return 400."""
        response = client.post(
            "/api/hr/onboard/bulk",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400

    def test_empty_array_returns_400(self, client):
        """Empty array should return 400."""
        response = client.post("/api/hr/onboard/bulk", json=[])
        assert response.status_code == 400

    def test_happy_path_returns_200(self, client, db_engine):
        """Full happy path through the route returns 200."""
        Session = sessionmaker(bind=db_engine)
        session = Session()

        # Seed entity
        e = FinanceEntity(
            name="Route Test Entity", country="SG", base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        session.add(e)
        session.flush()
        entity_id = e.id

        # Seed COA
        session.add(FinanceAccount(
            code="6000", name="Salary Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ))

        # Seed user
        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
            ),
            {"id": 100, "name": "RouteUser", "email": "route@test.com",
             "is_employee": False, "onboarding_status": "PENDING"},
        )
        session.commit()
        session.close()

        response = client.post("/api/hr/onboard/bulk", json=[
            {
                "user_id": 100,
                "payroll_entity_id": entity_id,
                "salary_expense_code": "6000",
                "employee_type": "FULL_TIME",
                "teams": ["Engineering"],
            }
        ])

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["onboarded_count"] == 1


# ============================================================================
# Individual Onboarding — Service Layer Tests
# ============================================================================


class TestIndividualOnboardService:
    """Tests for HrOnboardingService.single_onboard() method."""

    def test_happy_path_onboards_single_user(self, db_session, entity, accounts):
        """Onboarding a single valid user returns full user details."""
        _insert_user(db_session, 200, name="Alice Lee")

        result = hr_onboarding_service.single_onboard(db_session, user_id=200, payload={
            "payroll_entity_id": entity.id,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": ["Customer Support"],
            "bank_account_number": "1001-1234-5678",
            "bank_code": "OCBCSGSG",
        })

        assert result["success"] is True
        assert result["user"]["user_id"] == 200
        assert result["user"]["name"] == "Alice Lee"
        assert result["user"]["onboarding_status"] == "COMPLETED"
        assert result["user"]["salary_expense_code"] == "6000"
        assert result["user"]["employee_type"] == "FULL_TIME"
        assert result["user"]["teams"] == ["Customer Support"]
        assert result["user"]["payroll_entity_id"] == entity.id
        assert result["user"]["bank_account_number"] == "1001-1234-5678"

        # Verify HrEmployee created
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 200).first()
        assert emp is not None
        assert emp.salary_expense_code == "6000"

        # Verify FinanceCounterparty created
        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "200",
            FinanceCounterparty.external_system == "users",
        ).first()
        assert cp is not None

    def test_user_not_found_returns_error(self, db_session, entity, accounts):
        """Non-existent user_id returns not_found error."""
        result = hr_onboarding_service.single_onboard(db_session, user_id=9999, payload={
            "payroll_entity_id": entity.id,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert result["success"] is False
        assert result["error_type"] == "not_found"
        assert "not found" in result["message"].lower()

    def test_already_onboarded_returns_conflict(self, db_session, entity, accounts):
        """User with is_employee=true returns conflict error."""
        _insert_user(db_session, 201, name="Already Done", is_employee=True,
                     onboarding_status="COMPLETED")

        result = hr_onboarding_service.single_onboard(db_session, user_id=201, payload={
            "payroll_entity_id": entity.id,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert result["success"] is False
        assert result["error_type"] == "conflict"
        assert "already onboarded" in result["message"].lower()

    def test_invalid_entity_returns_error(self, db_session, entity, accounts):
        """Invalid payroll_entity_id returns validation error."""
        _insert_user(db_session, 202, name="Bad Entity")

        result = hr_onboarding_service.single_onboard(db_session, user_id=202, payload={
            "payroll_entity_id": 9999,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert result["success"] is False
        assert result["error_type"] == "validation"
        assert "entity" in result["message"].lower()

    def test_invalid_coa_returns_error(self, db_session, entity, accounts):
        """Invalid salary_expense_code returns validation error."""
        _insert_user(db_session, 203, name="Bad COA")

        result = hr_onboarding_service.single_onboard(db_session, user_id=203, payload={
            "payroll_entity_id": entity.id,
            "salary_expense_code": "9999",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert result["success"] is False
        assert result["error_type"] == "validation"
        assert "salary_expense_code" in result["message"].lower() or "coa" in result["message"].lower()

    def test_missing_payroll_entity_id_returns_error(self, db_session, entity, accounts):
        """Missing payroll_entity_id field returns validation error."""
        _insert_user(db_session, 204, name="No Entity")

        result = hr_onboarding_service.single_onboard(db_session, user_id=204, payload={
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert result["success"] is False
        assert result["error_type"] == "validation"

    def test_defaults_salary_expense_code_to_6000(self, db_session, entity, accounts):
        """salary_expense_code defaults to '6000' if not provided."""
        _insert_user(db_session, 205, name="Default COA")

        result = hr_onboarding_service.single_onboard(db_session, user_id=205, payload={
            "payroll_entity_id": entity.id,
            "employee_type": "FULL_TIME",
            "teams": ["Engineering"],
        })

        assert result["success"] is True
        assert result["user"]["salary_expense_code"] == "6000"


# ============================================================================
# Individual Onboarding — Route / HTTP-Level Tests
# ============================================================================


class TestIndividualOnboardRoute:
    """Test POST /api/hr/onboard/<user_id> HTTP endpoint."""

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

    def _seed_data(self, db_engine):
        """Seed entity, COA, and return entity_id."""
        Session = sessionmaker(bind=db_engine)
        session = Session()

        e = FinanceEntity(
            name="Ind Test Entity", country="SG", base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        session.add(e)
        session.flush()
        entity_id = e.id

        session.add(FinanceAccount(
            code="6000", name="Salary Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ))
        session.commit()
        session.close()
        return entity_id

    def test_route_returns_200_on_success(self, client, db_engine):
        """POST /api/hr/onboard/<user_id> returns 200 with user details."""
        entity_id = self._seed_data(db_engine)

        Session = sessionmaker(bind=db_engine)
        session = Session()
        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
            ),
            {"id": 300, "name": "Alice Lee", "email": "alice@test.com",
             "is_employee": False, "onboarding_status": "PENDING"},
        )
        session.commit()
        session.close()

        response = client.post("/api/hr/onboard/300", json={
            "payroll_entity_id": entity_id,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": ["Customer Support"],
            "bank_account_number": "1001-1234-5678",
            "bank_code": "OCBCSGSG",
        })

        assert response.status_code == 200
        data = response.get_json()
        assert data["user_id"] == 300
        assert data["name"] == "Alice Lee"
        assert data["onboarding_status"] == "COMPLETED"
        assert data["employee_type"] == "FULL_TIME"
        assert data["teams"] == ["Customer Support"]
        assert data["bank_account_number"] == "1001-1234-5678"

    def test_route_returns_404_for_nonexistent_user(self, client, db_engine):
        """POST /api/hr/onboard/<user_id> returns 404 if user not found."""
        self._seed_data(db_engine)

        response = client.post("/api/hr/onboard/9999", json={
            "payroll_entity_id": 1,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert response.status_code == 404

    def test_route_returns_409_for_already_onboarded(self, client, db_engine):
        """POST /api/hr/onboard/<user_id> returns 409 if already onboarded."""
        entity_id = self._seed_data(db_engine)

        Session = sessionmaker(bind=db_engine)
        session = Session()
        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
            ),
            {"id": 301, "name": "Bob Done", "email": "bob@test.com",
             "is_employee": True, "onboarding_status": "COMPLETED"},
        )
        session.commit()
        session.close()

        response = client.post("/api/hr/onboard/301", json={
            "payroll_entity_id": entity_id,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert response.status_code == 409

    def test_route_returns_400_for_invalid_coa(self, client, db_engine):
        """POST /api/hr/onboard/<user_id> returns 400 for invalid COA."""
        entity_id = self._seed_data(db_engine)

        Session = sessionmaker(bind=db_engine)
        session = Session()
        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
            ),
            {"id": 302, "name": "Cathy Bad", "email": "cathy@test.com",
             "is_employee": False, "onboarding_status": "PENDING"},
        )
        session.commit()
        session.close()

        response = client.post("/api/hr/onboard/302", json={
            "payroll_entity_id": entity_id,
            "salary_expense_code": "9999",
            "employee_type": "FULL_TIME",
            "teams": [],
        })

        assert response.status_code == 400

    def test_route_returns_400_for_non_json_body(self, client):
        """Non-JSON body returns 400."""
        response = client.post(
            "/api/hr/onboard/1",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400


# ============================================================================
# Offboarding — Service Layer Tests
# ============================================================================


def _onboard_user(db_session, user_id, name, entity, salary_code="6000"):
    """Helper: insert user, onboard via service, return user_id."""
    _insert_user(db_session, user_id, name=name, is_employee=False, onboarding_status="PENDING")
    hr_onboarding_service.single_onboard(db_session, user_id=user_id, payload={
        "payroll_entity_id": entity.id,
        "salary_expense_code": salary_code,
        "employee_type": "FULL_TIME",
        "teams": ["Engineering"],
    })
    return user_id


class TestOffboardServiceHappyPath:
    """Tests for HrOnboardingService.offboard_employee() method."""

    def test_offboard_sets_is_employee_false(self, db_session, entity, accounts):
        """Offboarding sets is_employee=false on users table."""
        uid = _onboard_user(db_session, 500, "OffboardMe", entity)

        result = hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert result["success"] is True
        assert result["user"]["user_id"] == uid
        assert result["user"]["is_employee"] is False
        assert result["user"]["employment_end_date"] == "2026-03-31"
        assert result["user"]["status"] == "offboarded"

    def test_offboard_sets_hr_employee_end_date(self, db_session, entity, accounts):
        """Offboarding sets employment_end_date on hr_employees record."""
        uid = _onboard_user(db_session, 501, "HrEndDate", entity)

        hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "2026-03-31",
            "reason": "Contract end",
        })

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == uid).first()
        assert emp is not None
        assert emp.employment_end_date == date(2026, 3, 31)

    def test_offboard_deactivates_counterparty(self, db_session, entity, accounts):
        """Offboarding sets counterparty status to inactive."""
        uid = _onboard_user(db_session, 502, "DeactivateCP", entity)

        hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == str(uid),
            FinanceCounterparty.external_system == "users",
        ).first()
        assert cp is not None
        assert cp.status == "inactive"

    def test_offboard_returns_user_name(self, db_session, entity, accounts):
        """Offboard response includes the user name."""
        uid = _onboard_user(db_session, 503, "Alice Lee", entity)

        result = hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert result["user"]["name"] == "Alice Lee"

    def test_offboard_without_counterparty_still_succeeds(self, db_session, entity, accounts):
        """If no counterparty exists (edge case), offboarding still works."""
        _insert_user(db_session, 504, name="NoCPUser", is_employee=False)
        # Directly set is_employee=true and create HrEmployee without counterparty
        db_session.execute(
            text("UPDATE users SET is_employee = 1 WHERE id = :id"),
            {"id": 504},
        )
        emp = HrEmployee(user_id=504, entity_id=entity.id, employee_type="FULL_TIME",
                         salary_expense_code="6000")
        db_session.add(emp)
        db_session.commit()

        result = hr_onboarding_service.offboard_employee(db_session, user_id=504, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert result["success"] is True


class TestOffboardServiceErrors:
    """Tests for offboarding error cases."""

    def test_user_not_found_returns_404(self, db_session, entity, accounts):
        """Non-existent user_id returns not_found error."""
        result = hr_onboarding_service.offboard_employee(db_session, user_id=9999, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert result["success"] is False
        assert result["error_type"] == "not_found"
        assert "not found" in result["message"].lower()

    def test_not_onboarded_returns_conflict(self, db_session, entity, accounts):
        """User with is_employee=false returns conflict."""
        _insert_user(db_session, 510, name="NotEmployee", is_employee=False)

        result = hr_onboarding_service.offboard_employee(db_session, user_id=510, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert result["success"] is False
        assert result["error_type"] == "conflict"
        assert "not currently onboarded" in result["message"].lower()

    def test_already_offboarded_returns_conflict(self, db_session, entity, accounts):
        """User with employment_end_date already set returns conflict."""
        uid = _onboard_user(db_session, 511, "AlreadyOff", entity)

        # First offboard
        hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        # Try again — should fail because employment_end_date is already set
        result = hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "2026-04-30",
            "reason": "Changed mind",
        })

        assert result["success"] is False
        assert result["error_type"] == "conflict"
        assert "already offboarded" in result["message"].lower()

    def test_missing_offboard_date_returns_validation(self, db_session, entity, accounts):
        """Missing offboard_date field returns validation error."""
        uid = _onboard_user(db_session, 512, "NoDate", entity)

        result = hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "reason": "Resignation",
        })

        assert result["success"] is False
        assert result["error_type"] == "validation"
        assert "offboard_date" in result["message"].lower()

    def test_invalid_offboard_date_format_returns_validation(self, db_session, entity, accounts):
        """Malformed offboard_date returns validation error."""
        uid = _onboard_user(db_session, 513, "BadDate", entity)

        result = hr_onboarding_service.offboard_employee(db_session, user_id=uid, payload={
            "offboard_date": "not-a-date",
            "reason": "Resignation",
        })

        assert result["success"] is False
        assert result["error_type"] == "validation"


# ============================================================================
# Offboarding — Route / HTTP-Level Tests
# ============================================================================


class TestOffboardRoute:
    """Test POST /api/hr/offboard/<user_id> HTTP endpoint."""

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

    def _seed_and_onboard(self, db_engine, user_id=600, name="OffRoute User"):
        """Seed entity, COA, user, and onboard them. Returns entity_id."""
        Session = sessionmaker(bind=db_engine)
        session = Session()

        e = FinanceEntity(
            name="Offboard Test Entity", country="SG", base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        session.add(e)
        session.flush()
        entity_id = e.id

        session.add(FinanceAccount(
            code="6000", name="Salary Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ))

        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
            ),
            {"id": user_id, "name": name, "email": f"user{user_id}@test.com",
             "is_employee": False, "onboarding_status": "PENDING"},
        )
        session.commit()

        # Onboard through service
        hr_onboarding_service.single_onboard(session, user_id=user_id, payload={
            "payroll_entity_id": entity_id,
            "salary_expense_code": "6000",
            "employee_type": "FULL_TIME",
            "teams": ["Engineering"],
        })
        session.commit()
        session.close()
        return entity_id

    def test_offboard_route_returns_200(self, client, db_engine):
        """POST /api/hr/offboard/<user_id> returns 200 on success."""
        self._seed_and_onboard(db_engine, user_id=600, name="Alice Lee")

        response = client.post("/api/hr/offboard/600", json={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
            "notes": "Moving to another company",
        })

        assert response.status_code == 200
        data = response.get_json()
        assert data["user_id"] == 600
        assert data["name"] == "Alice Lee"
        assert data["is_employee"] is False
        assert data["employment_end_date"] == "2026-03-31"
        assert data["status"] == "offboarded"

    def test_offboard_route_returns_404_for_nonexistent_user(self, client, db_engine):
        """POST /api/hr/offboard/9999 returns 404."""
        response = client.post("/api/hr/offboard/9999", json={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert response.status_code == 404

    def test_offboard_route_returns_409_not_onboarded(self, client, db_engine):
        """POST /api/hr/offboard/<id> returns 409 when is_employee=false."""
        Session = sessionmaker(bind=db_engine)
        session = Session()

        # Seed entity for schema creation
        e = FinanceEntity(
            name="Conflict Entity", country="SG", base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        session.add(e)

        session.execute(
            text(
                "INSERT INTO users (id, name, email, is_employee, onboarding_status) "
                "VALUES (:id, :name, :email, :is_employee, :onboarding_status)"
            ),
            {"id": 601, "name": "Not Employee", "email": "ne@test.com",
             "is_employee": False, "onboarding_status": "PENDING"},
        )
        session.commit()
        session.close()

        response = client.post("/api/hr/offboard/601", json={
            "offboard_date": "2026-03-31",
            "reason": "Resignation",
        })

        assert response.status_code == 409

    def test_offboard_route_returns_400_for_non_json(self, client):
        """Non-JSON body returns 400."""
        response = client.post(
            "/api/hr/offboard/1",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400

    def test_offboard_route_returns_400_for_missing_date(self, client, db_engine):
        """Missing offboard_date returns 400."""
        self._seed_and_onboard(db_engine, user_id=602, name="No Date")

        response = client.post("/api/hr/offboard/602", json={
            "reason": "Resignation",
        })

        assert response.status_code == 400
