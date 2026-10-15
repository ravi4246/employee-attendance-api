"""Employee Attendance & Analytics API (assignment implementation)."""
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"),
    tz_aware=True,
    serverSelectionTimeoutMS=3000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE = ("PRESENT", "WFH", "ON_DUTY")
STATUSES = PRESENCE + ("ABSENT", "LEAVE")
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    """Create indexes idempotently at startup."""
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employee_emp_code",
    )
    db.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="idx_employee_department_code",
    )
    db.employees.create_index(
        [("joined_on", ASCENDING)],
        name="idx_employee_joined_on",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="idx_attendance_date_code",
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)],
        name="idx_attendance_status_date",
    )
    db.attendance_logs.create_index(
        [
            ("date", ASCENDING),
            ("late_minutes", DESCENDING),
            ("emp_code", ASCENDING),
        ],
        name="idx_attendance_late_date_code",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)],
        name="idx_attendance_employee_punchin",
    )


# ------------------------------ common helpers ------------------------------

def epoch_ms_to_datetime(value: Optional[int]) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).astimezone(IST)

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_EPOCH_MS
        or value > MAX_EPOCH_MS
    ):
        raise HTTPException(
            422,
            "Timestamp must be an integer epoch-millisecond value in the allowed range",
        )

    return (
        datetime.fromtimestamp(value / 1000, tz=UTC)
        .astimezone(IST)
        .replace(microsecond=0)
    )


def datetime_to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp()) * 1000


def parse_date(value: str, field: str = "date") -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field} must be YYYY-MM-DD")


def parse_month(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(422, "month must be YYYY-MM")

    start = date.fromisoformat(month + "-01")
    end = date(
        start.year + (start.month == 12),
        start.month % 12 + 1,
        1,
    ) - timedelta(days=1)

    return start, end


def validate_time(value: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", value
    ):
        raise HTTPException(422, f"{field} must be HH:MM in 24-hour format")
    return value


def get_attendance_date(
    ts: datetime,
    shift_start: str,
    shift_end: str,
) -> str:
    # Early-morning punches for overnight shifts belong to the prior shift day.
    if shift_end <= shift_start and ts.strftime("%H:%M") < shift_end:
        return (ts.date() - timedelta(days=1)).isoformat()
    return ts.date().isoformat()


def shift_start_datetime(
    attendance_date: str,
    shift_start: str,
) -> datetime:
    d = parse_date(attendance_date)
    h, m = map(int, shift_start.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=IST)


def shift_end_datetime(
    attendance_date: str,
    shift_start: str,
    shift_end: str,
) -> datetime:
    d = parse_date(attendance_date)
    h, m = map(int, shift_end.split(":"))
    end = datetime.combine(d, time(h, m), tzinfo=IST)

    if shift_end <= shift_start:
        end += timedelta(days=1)

    return end


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_date: Optional[str] = None,
) -> int:
    punch_in = punch_in.replace(microsecond=0)

    if attendance_date is None:
        attendance_date = punch_in.date().isoformat()

    start = shift_start_datetime(attendance_date, shift_start)
    seconds = int((punch_in - start).total_seconds())

    # More than ten minutes late is required before late minutes are counted.
    return seconds // 60 if seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    attendance_date: str,
) -> int:
    end = shift_end_datetime(
        attendance_date,
        shift_start,
        shift_end,
    )
    minutes = int(
        (punch_out.replace(microsecond=0) - end).total_seconds()
    ) // 60

    return minutes if minutes >= 30 else 0


def round_decimal(value: Any, places: int = 2) -> Optional[float]:
    if value is None:
        return None

    return float(
        Decimal(str(value)).quantize(
            Decimal("1." + "0" * places),
            rounding=ROUND_HALF_UP,
        )
    )


def attendance_response(doc: dict) -> dict:
    doc.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        doc[field] = datetime_to_epoch_ms(doc.get(field))

    for entry in doc.get("history", []):
        if entry.get("at") is not None:
            entry["at"] = datetime_to_epoch_ms(entry["at"])

        for change in entry.get("changes", {}).values():
            if isinstance(change, dict):
                for key in ("from", "to"):
                    if isinstance(change.get(key), datetime):
                        change[key] = datetime_to_epoch_ms(change[key])

    return doc


def employee_response(doc: dict) -> dict:
    doc.pop("_id", None)

    if "created_at" in doc:
        doc["created_at"] = datetime_to_epoch_ms(doc["created_at"])

    return doc


def require_employee(emp_code: str) -> dict:
    emp = db.employees.find_one({"emp_code": emp_code})

    if emp is None:
        raise HTTPException(404, "Employee not found")

    return emp


def is_presence(status: str) -> bool:
    return status in PRESENCE


def present_value(doc: dict) -> float:
    if doc.get("status") not in PRESENCE:
        return 0.0

    return 0.5 if doc.get("half_day") else 1.0


# ------------------------------ request models ------------------------------

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def valid_emp_code(cls, value):
        if not re.fullmatch(r"EMP\d{4,6}", value or ""):
            raise ValueError(
                "emp_code must match EMP followed by 4 to 6 digits"
            )
        return value

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not 1 <= len(value.strip()) <= 100:
            raise ValueError("name must contain 1 to 100 characters")
        return value.strip()

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value or ""):
            raise ValueError("email must be a valid email address")
        return value

    @field_validator("department")
    @classmethod
    def valid_department(cls, value):
        if not 1 <= len(value.strip()) <= 50:
            raise ValueError(
                "department must contain 1 to 50 characters"
            )
        return value.strip()

    @field_validator("shift_start", "shift_end")
    @classmethod
    def valid_shift_time(cls, value, info):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value or ""):
            raise ValueError(f"{info.field_name} must be HH:MM")
        return value

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value):
        parse_date(value, "joined_on")
        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[StrictInt] = None


class RegularizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    regularized_by: str = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=5, max_length=200)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value):
        if value is not None and value not in STATUSES:
            raise ValueError("Invalid attendance status")
        return value


# ---------------------------------- health ----------------------------------

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB is unavailable")


# -------------------------------- employees ---------------------------------

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(
            422,
            "shift_start and shift_end must differ",
        )

    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    return employee_response(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department else {}
    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for item in items:
        if "created_at" in item:
            item["created_at"] = datetime_to_epoch_ms(item["created_at"])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ------------------------------- punch in/out -------------------------------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = require_employee(body.emp_code)

    if body.status not in PRESENCE:
        raise HTTPException(
            422,
            "Punch-in status must be PRESENT, WFH, or ON_DUTY",
        )

    ts = epoch_ms_to_datetime(body.punched_at)
    date_str = get_attendance_date(
        ts,
        emp["shift_start"],
        emp["shift_end"],
    )

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            emp["shift_start"],
            date_str,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            409,
            "Already punched in for this attendance date",
        )

    return attendance_response(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = require_employee(body.emp_code)
    ts = epoch_ms_to_datetime(body.punched_at)

    # Find the latest punch-in at or before the supplied punch-out time.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(404, "Attendance record not found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")

    punch_in_time = record["punch_in"]

    if ts <= punch_in_time:
        raise HTTPException(422, "Punch-out must be after punch-in")

    if ts > punch_in_time + timedelta(hours=24):
        raise HTTPException(
            422,
            "Punch-out must be within 24 hours of punch-in",
        )

    work_hours = compute_work_hours(punch_in_time, ts)
    overtime = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )
    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            409,
            "Attendance record was updated by another request",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    return attendance_response(updated)


# -------------------------------- attendance --------------------------------

def build_attendance_query(
    emp_code=None,
    date_from=None,
    date_to=None,
    status=None,
):
    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            parse_date(date_from, "date_from")
            query["date"]["$gte"] = date_from

        if date_to:
            parse_date(date_to, "date_to")
            query["date"]["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                422,
                "date_from must be on or before date_to",
            )

    if status:
        if status not in STATUSES:
            raise HTTPException(422, "Invalid status")
        query["status"] = status

    return query


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = build_attendance_query(
        emp_code,
        date_from,
        date_to,
        status,
    )
    total = db.attendance_logs.count_documents(query)

    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [attendance_response(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    parse_date(date, "date")
    emp = require_employee(emp_code)
    query = {"emp_code": emp_code, "date": date}
    record = db.attendance_logs.find_one(query)

    if record is None:
        raise HTTPException(404, "Attendance record not found")

    if body.status in ("ABSENT", "LEAVE") and (
        body.punch_in is not None or body.punch_out is not None
    ):
        raise HTTPException(
            422,
            "ABSENT and LEAVE cannot be supplied with punch times",
        )

    status = (
        body.status
        if body.status is not None
        else record.get("status", "PRESENT")
    )

    if status in ("ABSENT", "LEAVE"):
        pin = None
        pout = None
    else:
        pin = (
            epoch_ms_to_datetime(body.punch_in)
            if body.punch_in is not None
            else record.get("punch_in")
        )
        pout = (
            epoch_ms_to_datetime(body.punch_out)
            if body.punch_out is not None
            else record.get("punch_out")
        )

        if pin is None:
            raise HTTPException(
                422,
                "Presence statuses require punch_in",
            )

        if get_attendance_date(
            pin,
            emp["shift_start"],
            emp["shift_end"],
        ) != date:
            raise HTTPException(
                422,
                "punch_in must belong to the record attendance date",
            )

        if pout is not None and (
            pout <= pin or pout > pin + timedelta(hours=24)
        ):
            raise HTTPException(
                422,
                "punch_out must be after punch_in and within 24 hours",
            )

    final = {
        "status": status,
        "punch_in": pin,
        "punch_out": pout,
    }

    changes = {}

    for key, new_value in final.items():
        old_value = record.get(key)
        if old_value != new_value:
            changes[key] = {
                "from": old_value,
                "to": new_value,
            }

    if pin is None:
        derived = {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }
    else:
        late = compute_late_minutes(
            pin,
            emp["shift_start"],
            date,
        )
        hours = compute_work_hours(pin, pout) if pout is not None else None
        overtime = (
            compute_overtime(
                pout,
                emp["shift_start"],
                emp["shift_end"],
                date,
            )
            if pout is not None
            else 0
        )
        derived = {
            "late_minutes": late,
            "work_hours": hours,
            "overtime_minutes": overtime,
            "half_day": hours < 4.50 if hours is not None else False,
        }

    for key, new_value in derived.items():
        old_value = record.get(key)
        if old_value != new_value:
            changes[key] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(
            422,
            "The request does not change the record",
        )

    entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    old_history = record.get("history", [])

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "history": old_history},
        {
            "$set": {**final, **derived},
            "$push": {"history": entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            409,
            "Attendance record changed concurrently; retry the correction",
        )

    updated = db.attendance_logs.find_one(query, {"_id": 0})
    return attendance_response(updated)

# --------------------------- aggregation helpers ----------------------------

def date_range_filter(month_start: date, month_end: date) -> dict:
    return {
        "date": {
            "$gte": month_start.isoformat(),
            "$lte": month_end.isoformat(),
        }
    }


def working_days_pipeline_fields(
    start_date: date,
    end_date: date,
    joined_expr="$joined_on",
):
    # Count weekdays in MongoDB, beginning at the employee's join date
    # or the start of the requested reporting period, whichever is later.
    return [
        {
            "$set": {
                "_periodStart": {
                    "$max": [
                        {
                            "$dateFromString": {
                                "dateString": joined_expr,
                            }
                        },
                        {
                            "$dateFromString": {
                                "dateString": start_date.isoformat(),
                            }
                        },
                    ]
                },
                "_periodEnd": {
                    "$dateFromString": {
                        "dateString": end_date.isoformat(),
                    }
                },
            }
        },
        {
            "$set": {
                "_days": {
                    "$cond": [
                        {"$lte": ["$_periodStart", "$_periodEnd"]},
                        {
                            "$map": {
                                "input": {
                                    "$range": [
                                        0,
                                        {
                                            "$add": [
                                                {
                                                    "$dateDiff": {
                                                        "startDate": "$_periodStart",
                                                        "endDate": "$_periodEnd",
                                                        "unit": "day",
                                                    }
                                                },
                                                1,
                                            ]
                                        },
                                    ]
                                },
                                "as": "i",
                                "in": {
                                    "$dateAdd": {
                                        "startDate": "$_periodStart",
                                        "unit": "day",
                                        "amount": "$$i",
                                    }
                                },
                            }
                        },
                        [],
                    ]
                }
            }
        },
        {
            "$set": {
                "working_days": {
                    "$size": {
                        "$filter": {
                            "input": "$_days",
                            "as": "d",
                            "cond": {
                                "$and": [
                                    {
                                        "$gte": [
                                            {"$isoDayOfWeek": "$$d"},
                                            1,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$isoDayOfWeek": "$$d"},
                                            5,
                                        ]
                                    },
                                ]
                            },
                        }
                    }
                }
            }
        },
        {"$unset": ["_periodStart", "_periodEnd", "_days"]},
    ]


def employee_monthly_pipeline(emp_code: str, month: str):
    start, end = parse_month(month)

    return [
        {
            "$match": {
                "emp_code": emp_code,
                **date_range_filter(start, end),
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {
            "$set": {
                "_present": {
                    "$cond": [
                        {"$in": ["$status", list(PRESENCE)]},
                        {"$cond": ["$half_day", 0.5, 1]},
                        0,
                    ]
                },
                "_weekday": {
                    "$lte": [
                        {
                            "$isoDayOfWeek": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                }
                            }
                        },
                        5,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "employee": {"$first": "$employee"},
                "present_days": {
                    "$sum": {
                        "$cond": ["$_weekday", "$_present", 0]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
        *working_days_pipeline_fields(start, end, "$employee.joined_on"),
        {
            "$set": {
                "attendance_pct": {
                    "$cond": [
                        {"$gt": ["$working_days", 0]},
                        {
                            "$round": [
                                {
                                    "$multiply": [
                                        {
                                            "$divide": [
                                                "$present_days",
                                                "$working_days",
                                            ]
                                        },
                                        100,
                                    ]
                                },
                                4,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "month": {"$literal": month},
                "working_days": 1,
                "present_days": {"$round": ["$present_days", 2]},
                "leave_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "total_overtime_minutes": 1,
                "attendance_pct": 1,
            }
        },
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    require_employee(emp_code)
    parse_month(month)

    rows = list(
        db.attendance_logs.aggregate(
            employee_monthly_pipeline(emp_code, month)
        )
    )

    if rows:
        return rows[0]

    # Also return a report when this employee has no attendance records.
    start, end = parse_month(month)

    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "joined_on": 1,
            }
        },
        *working_days_pipeline_fields(start, end),
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "month": {"$literal": month},
                "working_days": 1,
                "present_days": {"$literal": 0.0},
                "leave_days": {"$literal": 0},
                "late_count": {"$literal": 0},
                "total_late_minutes": {"$literal": 0},
                "total_overtime_minutes": {"$literal": 0},
                "attendance_pct": {
                    "$cond": [
                        {"$gt": ["$working_days", 0]},
                        0.0,
                        None,
                    ]
                },
            }
        },
    ]

    return list(db.employees.aggregate(pipeline))[0]


def department_summary_pipeline(month: str):
    start, end = parse_month(month)

    return [
        {"$match": {"joined_on": {"$lte": end.isoformat()}}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "code": "$emp_code",
                    "joined": "$joined_on",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", start.isoformat()]},
                                    {"$lte": ["$date", end.isoformat()]},
                                    {"$gte": ["$date", "$$joined"]},
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "_weekday": {
                                "$lte": [
                                    {
                                        "$isoDayOfWeek": {
                                            "$dateFromString": {
                                                "dateString": "$date",
                                            }
                                        }
                                    },
                                    5,
                                ]
                            },
                            "_present": {
                                "$cond": [
                                    {"$in": ["$status", list(PRESENCE)]},
                                    {"$cond": ["$half_day", 0.5, 1]},
                                    0,
                                ]
                            },
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            "$logs._weekday",
                            "$logs._present",
                            0,
                        ]
                    }
                },
                "avg_work_hours_values": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            list(PRESENCE),
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]},
                                ]
                            },
                            "$logs.work_hours",
                            None,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            "$logs.late_minutes",
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$headcount"},
                "avg_work_hours": {
                    "$cond": [
                        {
                            "$gt": [
                                {
                                    "$size": {
                                        "$filter": {
                                            "input": "$avg_work_hours_values",
                                            "as": "h",
                                            "cond": {"$ne": ["$$h", None]},
                                        }
                                    }
                                },
                                0,
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$avg": {
                                        "$filter": {
                                            "input": "$avg_work_hours_values",
                                            "as": "h",
                                            "cond": {"$ne": ["$$h", None]},
                                        }
                                    }
                                },
                                2,
                            ]
                        },
                        None,
                    ]
                },
                "present_days": {"$round": ["$present_days", 2]},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    parse_month(month)
    pipeline = department_summary_pipeline(month)

    if department:
        pipeline.insert(0, {"$match": {"department": department}})

    return {
        "month": month,
        "items": list(db.employees.aggregate(pipeline)),
    }


def late_leaderboard_pipeline(
    month: str,
    department: Optional[str],
    limit: int,
):
    start, end = parse_month(month)

    pipeline = [
        {
            "$match": {
                **date_range_filter(start, end),
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend(
        [
            {
                "$set": {
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "emp_code": 1,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": 1,
                    "name": 1,
                    "department": 1,
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )

    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
):
    parse_month(month)

    if department and db.employees.count_documents(
        {"department": department}
    ) == 0:
        raise HTTPException(404, "Department not found")

    return {
        "month": month,
        "items": list(
            db.attendance_logs.aggregate(
                late_leaderboard_pipeline(month, department, limit)
            )
        ),
    }


def department_trend_pipeline(
    department: str,
    start: date,
    end: date,
):
    n_days = (end - start).days + 1

    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "_seed": {"$literal": 1},
                "_days": {
                    "$map": {
                        "input": {"$range": [0, n_days]},
                        "as": "i",
                        "in": {
                            "$dateAdd": {
                                "startDate": {
                                    "$dateFromString": {
                                        "dateString": start.isoformat(),
                                    }
                                },
                                "unit": "day",
                                "amount": "$$i",
                            }
                        },
                    }
                },
            }
        },
        {"$unwind": "$_days"},
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "date": "$_days",
                        "format": "%Y-%m-%d",
                    }
                },
                "is_working_day": {
                    "$lte": [{"$isoDayOfWeek": "$_days"}, 5]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"d": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {"$lte": ["$joined_on", "$$d"]},
                        }
                    },
                    {"$count": "n"},
                ],
                "as": "hc",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"d": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$date", "$$d"]}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "e",
                        }
                    },
                    {"$unwind": "$e"},
                    {"$match": {"e.department": department}},
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$in": ["$status", list(PRESENCE)]},
                                        {"$cond": ["$half_day", 0.5, 1]},
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": ["$late_minutes", 0]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "total_late_minutes": {
                                "$sum": "$late_minutes"
                            },
                            "total_overtime_minutes": {
                                "$sum": "$overtime_minutes"
                            },
                        }
                    },
                ],
                "as": "daily",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily.present_count", 0]},
                        0,
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily.late_count", 0]},
                        0,
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$multiply": [
                                        {
                                            "$divide": [
                                                "$present_count",
                                                "$headcount",
                                            ]
                                        },
                                        100,
                                    ]
                                },
                                4,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {
                            "$eq": [
                                {"$type": "$moving_avg_7d"},
                                "missing",
                            ]
                        },
                        None,
                        {"$round": ["$moving_avg_7d", 4]},
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": {"$round": ["$present_count", 2]},
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    start = parse_date(from_date, "from")
    end = parse_date(to_date, "to")

    if end < start:
        raise HTTPException(422, "to must be on or after from")

    if (end - start).days + 1 > 92:
        raise HTTPException(
            422,
            "Date range cannot exceed 92 days",
        )

    if db.employees.count_documents(
        {"department": department}
    ) == 0:
        raise HTTPException(404, "Department not found")

    items = list(
        db.employees.aggregate(
            department_trend_pipeline(department, start, end)
        )
    )

    for item in items:
        if item.get("moving_avg_7d") is None:
            item["moving_avg_7d"] = None

    return {"department": department, "items": items}




# ------------------------------- explain API --------------------------------

def explain_find(
    collection,
    query,
    sort=None,
    skip=0,
    limit=None,
):
    find_command = {
        "find": collection,
        "filter": query,
        "projection": {"_id": 0},
    }

    if sort:
        find_command["sort"] = {key: value for key, value in sort}

    if skip:
        find_command["skip"] = skip

    if limit:
        find_command["limit"] = limit

    return db.command(
        {
            "explain": find_command,
            "verbosity": "executionStats",
        }
    )


def explain_aggregate(collection, pipeline):
    command = {
        "aggregate": collection,
        "pipeline": pipeline,
        "cursor": {},
    }

    return db.command(
        {
            "explain": command,
            "verbosity": "executionStats",
        }
    )


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    if endpoint == "attendance_list":
        query = build_attendance_query(
            emp_code,
            date_from,
            date_to,
            status,
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_find(
                "attendance_logs",
                query,
                [
                    ("date", DESCENDING),
                    ("emp_code", ASCENDING),
                ],
                (page - 1) * page_size,
                page_size,
            ),
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                422,
                "emp_code and month are required",
            )

        require_employee(emp_code)
        parse_month(month)

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_aggregate(
                "attendance_logs",
                employee_monthly_pipeline(emp_code, month),
            ),
        }

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")

        parse_month(month)
        pipeline = department_summary_pipeline(month)

        if department:
            pipeline.insert(
                0,
                {"$match": {"department": department}},
            )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_aggregate(
                "employees",
                pipeline,
            ),
        }

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")

        parse_month(month)

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_aggregate(
                "attendance_logs",
                late_leaderboard_pipeline(
                    month,
                    department,
                    limit,
                ),
            ),
        }

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                422,
                "department, from, and to are required",
            )

        start = parse_date(from_date, "from")
        end = parse_date(to_date, "to")

        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(
                422,
                "Invalid trend date range",
            )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_aggregate(
                "employees",
                department_trend_pipeline(
                    department,
                    start,
                    end,
                ),
            ),
        }

    raise HTTPException(
        422,
        "endpoint must be attendance_list, employee_monthly, "
        "department_summary, late_leaderboard, or department_trend",
    )