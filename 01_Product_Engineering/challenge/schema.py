from pydantic import BaseModel, Field, model_validator
from enum import Enum
import csv
from typing import TypeVar, Generic, Literal

T = TypeVar("T")

class Status(str, Enum):
    READ = "read"
    UNREADABLE = "unreadable"
    LOW_CONFIDENCE = "low_confidence"

class Extracted(BaseModel, Generic[T]):
    value: T | None = None
    status: Status
    @model_validator(mode="after")
    def unreadable_has_no_value(self):
        if self.status == Status.UNREADABLE and self.value is not None:
            raise ValueError("an unreadable field cannot carry a value")
        return self

class Request(BaseModel):
    """JPEG Client Payroll Form"""

    media_type: Literal["image/jpeg"] = Field(default="image/jpeg", description="MIME type of the scan")
    scan: bytes = Field(description="Raw bytes of one scanned JPEG payroll form")

class Response(BaseModel):
    emp_num: Extracted[str]
    emp_first_name: Extracted[str]
    emp_last_name: Extracted[str]
    pay_rate: Extracted[float]
    reg_hours: Extracted[float]
    ot_hours: Extracted[float]
    vac_hours: Extracted[float]
    sick_hours: Extracted[float]
    holiday_hours: Extracted[float]
    pto_hours: Extracted[float]
    other_hours: Extracted[float]
    cash_tip_amt: Extracted[float]
    credit_tip_amt: Extracted[float]
    bonus_amt: Extracted[float]
    comm_amt: Extracted[float]
    other_amt: Extracted[float]
    department: Extracted[str]
    deduct_code: Extracted[str]
    deduction_amt: Extracted[float]
    project: Extracted[str]
    job: Extracted[str]

    
def solve(request: Request) -> Response:
    raise NotImplementedError("implement against this signature")

