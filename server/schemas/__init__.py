from datetime import date, time, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class AdminLogin(BaseModel):
    id: str
    password: str


class UserLogin(BaseModel):
    phone: str
    password: str


class OperatorLogin(BaseModel):
    phone: str
    password: str
    photo_base64: str
    name: Optional[str] = None


class OperatorSessionLogin(BaseModel):
    exam_id: UUID
    shift_id: UUID
    center_id: UUID
    device_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Centers & Shifts ──────────────────────────────────────────────────────────

class CenterCreate(BaseModel):
    code: str
    name: Optional[str] = None
    address: Optional[str] = None
    supervisor_name: Optional[str] = None
    vendor_name: Optional[str] = None


class CenterOut(BaseModel):
    id: UUID
    code: str
    name: Optional[str]
    address: Optional[str]
    supervisor_name: Optional[str] = None
    vendor_name: Optional[str] = None
    created_at: datetime


class ShiftCreate(BaseModel):
    shift_code: Optional[str] = None
    date: date
    start_time: time


class ShiftOut(BaseModel):
    id: UUID
    shift_code: Optional[str]
    date: date
    start_time: time
    created_at: datetime


# ── Exams ─────────────────────────────────────────────────────────────────────

class ExamCreate(BaseModel):
    name: str
    type: str = "capture"
    qr_string: Optional[str] = None
    num_supervisors: int = Field(ge=1, le=10)
    num_operators: int = Field(ge=1, le=10)
    supervisor_phones: list[str]
    supervisor_passwords: list[str]
    operator_phones: list[str]
    operator_passwords: list[str]

    @model_validator(mode="after")
    def check_list_lengths(self):
        if len(self.supervisor_phones) != self.num_supervisors:
            raise ValueError(f"supervisor_phones must have exactly {self.num_supervisors} entries")
        if len(self.supervisor_passwords) != self.num_supervisors:
            raise ValueError(f"supervisor_passwords must have exactly {self.num_supervisors} entries")
        if len(self.operator_phones) != self.num_operators:
            raise ValueError(f"operator_phones must have exactly {self.num_operators} entries")
        if len(self.operator_passwords) != self.num_operators:
            raise ValueError(f"operator_passwords must have exactly {self.num_operators} entries")
        return self


class ExamEdit(BaseModel):
    archived: Optional[bool] = None
    qr_string: Optional[str] = None


class ExamOut(BaseModel):
    id: UUID
    name: str
    type: str
    qr_string: Optional[str]
    qr_data: Optional[str]
    archived: bool
    archived_at: Optional[datetime]
    sync_key: str
    created_at: datetime


# ── Candidates ────────────────────────────────────────────────────────────────

class CandidateAdd(BaseModel):
    exam_id: UUID
    center_code: str
    center_name: Optional[str] = None
    center_address: Optional[str] = None
    supervisor_name: Optional[str] = None
    vendor_name: Optional[str] = None
    shift_date: date
    shift_start_time: time
    shift_code: Optional[str] = None
    candidate_no: str
    name: str
    roll_no: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    dob: Optional[date] = None
    photo_base64: Optional[str] = None
    fingerprint_base64: Optional[str] = None   # match exam only
    iris_base64: Optional[str] = None          # match exam only


# Returned by GET /operator/capture-details (one per distinct candidate_no_plain)
class OpCandidateDetailOut(BaseModel):
    candidate_id: UUID           # seed CandidateCapture row id
    candidate_no: str
    name: str
    roll_no: Optional[str]
    father_name: Optional[str]
    mother_name: Optional[str]
    dob: Optional[date]
    attended: bool
    photo_data: Optional[str]    # base64 reference photo
    has_photo: bool
    has_fingerprint: bool
    has_iris: bool
    photo_match_status: Optional[str]


# Returned by GET /operator/candidate (match exam single-candidate lookup)
class OpCandidateMatchOut(BaseModel):
    candidate_id: UUID
    candidate_no: str
    name: str
    roll_no: Optional[str]
    father_name: Optional[str]
    mother_name: Optional[str]
    dob: Optional[date]
    attended: bool
    photo_data: Optional[str]            # base64 reference photo
    photo_match_status: Optional[str]
    fingerprint_match_status: Optional[str]
    iris_match_status: Optional[str]


# Returned by GET /operator/mt1 (capture submission rows)
class OpCaptureRowOut(BaseModel):
    id: UUID
    candidate_no: str
    name: str
    attended: bool
    photo_match_status: Optional[str]
    has_photo: bool
    has_fingerprint: bool
    has_iris: bool
    created_at: datetime


# Returned by GET /operator/mt2 (match rows)
class OpMatchRowOut(BaseModel):
    id: UUID
    candidate_no: str
    name: str
    attended: bool
    photo_match_status: Optional[str]
    matched_at: Optional[datetime]
    fingerprint_match_status: Optional[str]
    fingerprint_matched_at: Optional[datetime]
    iris_match_status: Optional[str]
    iris_matched_at: Optional[datetime]


# Attendance row for admin/supervisor attendance endpoints
class CandidateAttendanceOut(BaseModel):
    candidate_no: str
    name: str
    roll_no: Optional[str]
    father_name: Optional[str]
    dob: Optional[date]
    attended: bool
    attendance_marked_at: Optional[datetime]
    center_code: str
    shift_code: Optional[str]
    photo_data: Optional[str]


# Matching row for admin/supervisor matching endpoints
class CandidateCaptureMatchOut(BaseModel):
    capture_id: UUID
    candidate_no: str
    name: str
    roll_no: Optional[str]
    dob: Optional[date]
    attended: bool
    center_code: str
    shift_code: Optional[str]
    photo_data: Optional[str]
    captured_photo_data: Optional[str]
    photo_match_status: Optional[str]
    fingerprint_data: Optional[str]
    iris_data: Optional[str]


class CandidateMatchDetailOut(BaseModel):
    candidate_id: UUID
    candidate_no: str
    name: str
    roll_no: Optional[str]
    dob: Optional[date]
    attended: bool
    attendance_marked_at: Optional[datetime]
    center_code: str
    shift_code: Optional[str]
    photo_data: Optional[str]
    captured_photo_data: Optional[str]
    photo_match_status: Optional[str]
    fingerprint_data: Optional[str]
    live_fingerprint_data: Optional[str]
    fingerprint_match_status: Optional[str]
    iris_data: Optional[str]
    live_iris_data: Optional[str]
    iris_match_status: Optional[str]


# ── Biometric upload / check ──────────────────────────────────────────────────

class BiometricUpload(BaseModel):
    """Used for check-photo / check-fingerprint / check-iris (operator_session auth)."""
    candidate_no: str
    data_base64: str


class SyncKeyBiometricUpload(BaseModel):
    """Used for add-photo / add-fingerprint / add-iris (sync_key auth via X-Sync-Key header)."""
    shift_id: UUID
    center_id: UUID
    candidate_no: str
    data_base64: str


class QrDataBody(BaseModel):
    qr_data: str


class MatchRequest(BaseModel):
    candidate_no: str


# ── Batch sync ────────────────────────────────────────────────────────────────

class BatchSyncItem(BaseModel):
    type: str        # "photo" | "fingerprint" | "iris"
    candidate_no: str
    shift_id: UUID
    center_id: UUID
    data_base64: str


class BatchSyncRequest(BaseModel):
    items: list[BatchSyncItem] = Field(max_length=10)


# ── Centers summary (All Centers tab) ─────────────────────────────────────────

class CenterSummaryRow(BaseModel):
    center_id: UUID
    center_name: Optional[str]
    center_code: str
    shift_id: UUID
    shift_code: Optional[str]
    supervisor_name: Optional[str]
    vendor_name: Optional[str]
    total: int
    present: int
    absent: int
    matched: int
    mismatched: int
    duplicates: int
    fp_matched: int = 0
    fp_mismatched: int = 0
    iris_matched: int = 0
    iris_mismatched: int = 0
    active_devices: int


# ── ZIP export ─────────────────────────────────────────────────────────────────

class DownloadZipRequest(BaseModel):
    exam_id: UUID


# ── Duplicate resolution ──────────────────────────────────────────────────────

class ResolveDuplicateBody(BaseModel):
    keep_capture_id: UUID    # CandidateCapture row id to keep


# ── Attendance sync ───────────────────────────────────────────────────────────

class AttendanceSyncItem(BaseModel):
    candidate_no: str
    shift_id: UUID
    center_id: UUID


class AttendanceSyncRequest(BaseModel):
    items: list[AttendanceSyncItem]


# ── Logs ──────────────────────────────────────────────────────────────────────

class AuthLogOut(BaseModel):
    id: UUID
    mobile: Optional[str]
    role: Optional[str]
    name: Optional[str]
    created_at: datetime
    photo_data: Optional[str] = None
