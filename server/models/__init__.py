import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, Date, Time, DateTime, ForeignKey, Text,
    UniqueConstraint, Integer, Index, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship

from utils.encryption import EncryptedString, EncryptedBytes


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─────────────────────────────────────────────────────────────────────────────


class Exam(Base, TimestampMixin):
    __tablename__ = "exams"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(EncryptedString(512), nullable=False)
    name_lower = Column(String(512), unique=True, nullable=False)
    type = Column(String(10), nullable=False, default="capture")
    qr_string = Column(EncryptedString(2048), nullable=True)
    qr_data = Column(Text, nullable=True)
    archived = Column(Boolean, default=False)
    archived_at = Column(DateTime, nullable=True)
    sync_key = Column(EncryptedString(512), nullable=False)
    sync_key_plain = Column(String(512), nullable=False, unique=True)
    operators = relationship("Operator", back_populates="exam")
    supervisors = relationship("Supervisor", back_populates="exam")


class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_code = Column(String(50), nullable=True)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    __table_args__ = (UniqueConstraint("date", "start_time", name="uq_shift_date_time"),)


class Center(Base, TimestampMixin):
    __tablename__ = "centers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(EncryptedString(256), nullable=False)
    code_plain = Column(String(256), unique=True, nullable=False)
    name = Column(EncryptedString(512), nullable=True)
    address = Column(EncryptedString(1024), nullable=True)
    supervisor_name = Column(String(512), nullable=True)
    vendor_name = Column(String(512), nullable=True)


class Operator(Base, TimestampMixin):
    __tablename__ = "operators"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(EncryptedString(256), nullable=False)
    phone_plain = Column(String(256), nullable=False, unique=True)
    password = Column(String(256), nullable=False)
    exam_id = Column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    exam = relationship("Exam", back_populates="operators")


class Supervisor(Base, TimestampMixin):
    __tablename__ = "supervisors"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(EncryptedString(256), nullable=False)
    phone_plain = Column(String(256), nullable=False, unique=True)
    password = Column(String(256), nullable=False)
    exam_id = Column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    exam = relationship("Exam", back_populates="supervisors")


class Photo(Base, TimestampMixin):
    __tablename__ = "photos"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_path = Column(Text, nullable=True)
    compressed_file_path = Column(Text, nullable=True)
    is_load_test = Column(Boolean, default=False, nullable=False, server_default="false")
    __table_args__ = (
        Index("ix_photo_no_compressed", "id",
              postgresql_where=text("compressed_file_path IS NULL AND file_path IS NOT NULL")),
    )


class Fingerprint(Base, TimestampMixin):
    __tablename__ = "fingerprints"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_path = Column(Text, nullable=True)
    compressed_file_path = Column(Text, nullable=True)
    template = Column(EncryptedBytes, nullable=True)
    is_load_test = Column(Boolean, default=False, nullable=False, server_default="false")
    __table_args__ = (
        Index("ix_fp_no_template",   "id", postgresql_where=text("template IS NULL")),
        Index("ix_fp_no_compressed", "id",
              postgresql_where=text("compressed_file_path IS NULL AND file_path IS NOT NULL")),
    )


class Iris(Base, TimestampMixin):
    __tablename__ = "irises"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_path = Column(Text, nullable=True)
    compressed_file_path = Column(Text, nullable=True)
    template = Column(EncryptedBytes, nullable=True)
    is_load_test = Column(Boolean, default=False, nullable=False, server_default="false")
    __table_args__ = (
        Index("ix_iris_no_template",   "id", postgresql_where=text("template IS NULL")),
        Index("ix_iris_no_compressed", "id",
              postgresql_where=text("compressed_file_path IS NULL AND file_path IS NOT NULL")),
    )


class CandidateCapture(Base, TimestampMixin):
    """
    One row per capture session per candidate (duplicates allowed).
    Seed rows (new_photo_id IS NULL) are created by admin CSV upload.
    Submission rows (new_photo_id IS NOT NULL) are created by app biometric sync.
    Duplicates = candidate_no_plain values with >1 submission row.
    """
    __tablename__ = "candidate_captures"
    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_id              = Column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    shift_id             = Column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=False)
    center_id            = Column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    candidate_no         = Column(EncryptedString(256), nullable=False)
    candidate_no_plain   = Column(String(256), nullable=False)
    name                 = Column(EncryptedString(512), nullable=False)
    roll_no              = Column(EncryptedString(256), nullable=True)
    roll_no_plain        = Column(String(256), nullable=True)
    father_name          = Column(EncryptedString(512), nullable=True)
    mother_name          = Column(EncryptedString(512), nullable=True)
    dob                  = Column(Date, nullable=True)
    attendance           = Column(String(10), default="absent", nullable=False)
    attendance_marked_at = Column(DateTime, nullable=True)
    photo_id             = Column(UUID(as_uuid=True), ForeignKey("photos.id"), nullable=True)
    new_photo_id         = Column(UUID(as_uuid=True), ForeignKey("photos.id"), nullable=True)
    photo_match_status   = Column(String(10), nullable=True)   # null|pending|match|mismatch
    matched_at           = Column(DateTime, nullable=True)
    fingerprint_id       = Column(UUID(as_uuid=True), ForeignKey("fingerprints.id"), nullable=True)
    iris_id              = Column(UUID(as_uuid=True), ForeignKey("irises.id"), nullable=True)
    is_load_test         = Column(Boolean, default=False, nullable=False, server_default="false")
    __table_args__ = (
        Index("ix_cc_exam_shift_center", "exam_id", "shift_id", "center_id"),
        Index("ix_cc_cno", "exam_id", "shift_id", "center_id", "candidate_no_plain"),
        Index("ix_cc_roll_no_plain", "exam_id", "shift_id", "center_id", "roll_no_plain"),
        Index("ix_cc_pending_match", "exam_id",
              postgresql_where=text("photo_match_status = 'pending' AND new_photo_id IS NOT NULL")),
    )


class CandidateMatch(Base, TimestampMixin):
    """
    One row per candidate for match exams (unique constraint).
    Admin uploads reference biometrics (photo_id, fingerprint_id, iris_id).
    App writes live biometrics via check-* endpoints.
    """
    __tablename__ = "candidate_matches"
    id                       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_id                  = Column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    shift_id                 = Column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=False)
    center_id                = Column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    candidate_no             = Column(EncryptedString(256), nullable=False)
    candidate_no_plain       = Column(String(256), nullable=False)
    name                     = Column(EncryptedString(512), nullable=False)
    roll_no                  = Column(EncryptedString(256), nullable=True)
    roll_no_plain            = Column(String(256), nullable=True)
    father_name              = Column(EncryptedString(512), nullable=True)
    mother_name              = Column(EncryptedString(512), nullable=True)
    dob                      = Column(Date, nullable=True)
    attendance               = Column(String(10), default="absent", nullable=False)
    attendance_marked_at     = Column(DateTime, nullable=True)
    photo_id                 = Column(UUID(as_uuid=True), ForeignKey("photos.id"), nullable=True)
    new_photo_id             = Column(UUID(as_uuid=True), ForeignKey("photos.id"), nullable=True)
    photo_match_status       = Column(String(10), nullable=True)   # null|match|mismatch
    matched_at               = Column(DateTime, nullable=True)
    fingerprint_id           = Column(UUID(as_uuid=True), ForeignKey("fingerprints.id"), nullable=True)
    new_fingerprint_id       = Column(UUID(as_uuid=True), ForeignKey("fingerprints.id"), nullable=True)
    fingerprint_match_status = Column(String(10), nullable=True)   # null|match|mismatch
    fingerprint_matched_at   = Column(DateTime, nullable=True)
    iris_id                  = Column(UUID(as_uuid=True), ForeignKey("irises.id"), nullable=True)
    new_iris_id              = Column(UUID(as_uuid=True), ForeignKey("irises.id"), nullable=True)
    iris_match_status        = Column(String(10), nullable=True)   # null|match|mismatch
    iris_matched_at          = Column(DateTime, nullable=True)
    __table_args__ = (
        UniqueConstraint("exam_id", "shift_id", "center_id", "candidate_no_plain", name="uq_cm"),
        Index("ix_cm_exam_shift_center", "exam_id", "shift_id", "center_id"),
        Index("ix_cm_cno", "exam_id", "shift_id", "center_id", "candidate_no_plain"),
        Index("ix_cm_roll_no_plain", "exam_id", "shift_id", "center_id", "roll_no_plain"),
    )


class DeviceSession(Base, TimestampMixin):
    """Tracks active operator devices per center for live device count in All Centers view."""
    __tablename__ = "device_sessions"
    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id      = Column(String(64), nullable=False)
    operator_id    = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False)
    exam_id        = Column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    center_id      = Column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    shift_id       = Column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=False)
    matching_table = Column(Integer, default=1)
    last_heartbeat = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("device_id", "exam_id", "center_id", "shift_id",
                         name="uq_device_session"),
        Index("ix_ds_center_shift", "center_id", "shift_id"),
        Index("ix_ds_heartbeat",    "last_heartbeat"),
    )


class AuthLog(Base, TimestampMixin):
    __tablename__ = "auth_logs"
    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mobile      = Column(String(50), nullable=True)
    logged_in_at = Column(DateTime, default=datetime.utcnow)
    name        = Column(String(256), nullable=True)
    photo_id    = Column(UUID(as_uuid=True), ForeignKey("photos.id"), nullable=True)
    role        = Column(String(20), nullable=True)


class AdminLog(Base, TimestampMixin):
    __tablename__ = "admin_logs"
    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id  = Column(String(50), nullable=False)
    action    = Column(String(128), nullable=False)
    details   = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class SupervisorLog(Base, TimestampMixin):
    __tablename__ = "supervisor_logs"
    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supervisor_id = Column(UUID(as_uuid=True), nullable=False)
    action        = Column(String(128), nullable=False)
    details       = Column(Text, nullable=True)
    timestamp     = Column(DateTime, default=datetime.utcnow)


class OperatorLog(Base, TimestampMixin):
    __tablename__ = "operator_logs"
    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), nullable=False)
    action      = Column(String(128), nullable=False)
    details     = Column(Text, nullable=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
