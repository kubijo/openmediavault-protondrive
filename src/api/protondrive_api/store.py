"""Transactional job admission and replayable events owned by the broker."""

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import ForeignKey, String, UniqueConstraint, create_engine, event, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from protondrive.restore_journal import Publication

from .v1 import control_pb2 as wire


class Base(DeclarativeBase):
    pass


class JobRecord(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))
    operation: Mapped[int]
    state: Mapped[int]
    message: Mapped[str]
    sequence: Mapped[int]
    created_at: Mapped[str]
    parameters: Mapped[bytes | None]


class EventRecord(Base):
    __tablename__ = 'job_events'
    __table_args__: tuple[UniqueConstraint] = (UniqueConstraint('job_id', 'sequence'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    operation: Mapped[int]
    state: Mapped[int]
    message: Mapped[str]
    sequence: Mapped[int]


class PublicationRecord(Base):
    __tablename__ = 'publications'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), primary_key=True)
    parent: Mapped[str]
    parent_device: Mapped[int]
    parent_inode: Mapped[int]
    staging: Mapped[str]
    destination: Mapped[str]
    device: Mapped[int]
    inode: Mapped[int]
    phase: Mapped[str]


def message(record: JobRecord | EventRecord) -> wire.Job:
    return wire.Job(
        id=record.id if isinstance(record, JobRecord) else record.job_id,
        operation=wire.Operation.ValueType(record.operation),
        state=wire.JobState.ValueType(record.state),
        message=record.message,
        sequence=record.sequence,
    )


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError('Job database must not be a symlink')
        self.engine = create_engine(f'sqlite:///{path}', connect_args={'timeout': 10})
        # SQLAlchemy's documented engine hook avoids sqlite3's legacy transaction
        # mode: DDL, reads and journal writes participate in explicit transactions.
        event.listen(self.engine, 'begin', self._begin)
        self._migrate()
        path.chmod(0o600)

    @staticmethod
    def _begin(connection: object) -> None:
        from sqlalchemy.engine import Connection

        if not isinstance(connection, Connection):
            raise TypeError('Expected a SQLAlchemy connection')
        connection.exec_driver_sql('BEGIN IMMEDIATE')

    def _migrate(self) -> None:
        config = Config()
        config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
        config.set_main_option('sqlalchemy.url', str(self.engine.url))
        with self.engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')

    def create(self, request: wire.StartOperationRequest, actor: str) -> tuple[wire.Job, bool]:
        expected = {
            wire.OPERATION_INSPECT_ARCHIVE: 'inspect_archive',
            wire.OPERATION_EXTRACT_FILES: 'extract_files',
        }.get(request.operation)
        if request.WhichOneof('parameters') != expected:
            raise ValueError('Operation parameters do not match the requested operation')
        parameters = request.SerializeToString(deterministic=True)
        with Session(self.engine) as session, session.begin():
            existing = session.get(JobRecord, request.request_id)
            if existing is not None:
                if (
                    existing.operation != request.operation
                    or existing.actor != actor
                    or existing.parameters is not None
                    and existing.parameters != parameters
                ):
                    raise ValueError('Request identifier already belongs to a different operation')
                return message(existing), False
            active = list(session.scalars(select(JobRecord).where(JobRecord.state.in_([1, 2]))))
            if active and (
                request.operation != wire.OPERATION_CANCEL_BACKUP
                or any(record.operation != wire.OPERATION_BACKUP for record in active)
            ):
                raise ValueError('Another operation is running')
            record = JobRecord(
                id=request.request_id,
                actor=actor,
                operation=request.operation,
                state=wire.JOB_STATE_QUEUED,
                message='Queued',
                sequence=1,
                created_at=datetime.now(UTC).isoformat(),
                parameters=parameters,
            )
            session.add(record)
            session.flush()
            session.add(
                EventRecord(
                    job_id=record.id,
                    operation=record.operation,
                    state=record.state,
                    message=record.message,
                    sequence=record.sequence,
                )
            )
            return message(record), True

    def parameters(self, identifier: str) -> wire.StartOperationRequest:
        with Session(self.engine) as session:
            record = session.get(JobRecord, identifier)
            if record is None:
                raise LookupError('Job not found')
            if record.parameters is None:
                return wire.StartOperationRequest(
                    operation=wire.Operation.ValueType(record.operation), request_id=identifier
                )
            return wire.StartOperationRequest.FromString(record.parameters)

    def get(self, identifier: str) -> wire.Job:
        with Session(self.engine) as session:
            record = session.get(JobRecord, identifier)
            if record is None:
                raise LookupError('Job not found')
            return message(record)

    def update(
        self, identifier: str, state: wire.JobState.ValueType, detail: str, *, clear_publication: bool = False
    ) -> wire.Job:
        with Session(self.engine) as session, session.begin():
            record = session.get(JobRecord, identifier)
            if record is None:
                raise LookupError('Job not found')
            record.sequence += 1
            record.state = state
            record.message = detail
            if clear_publication:
                publication = session.get(PublicationRecord, identifier)
                if publication is not None:
                    session.delete(publication)
            session.add(
                EventRecord(
                    job_id=record.id,
                    operation=record.operation,
                    state=state,
                    message=detail,
                    sequence=record.sequence,
                )
            )
            return message(record)

    def events(self, identifier: str, after: int) -> list[wire.Job]:
        self.get(identifier)
        with Session(self.engine) as session:
            records = session.scalars(
                select(EventRecord)
                .where(
                    EventRecord.job_id == identifier,
                    EventRecord.sequence > after,
                )
                .order_by(EventRecord.sequence)
                .limit(256)
            )
            return [message(record) for record in records]

    def interrupt_abandoned(self) -> None:
        with Session(self.engine) as session:
            identifiers = list(session.scalars(select(JobRecord.id).where(JobRecord.state.in_([1, 2]))))
        for identifier in identifiers:
            self.update(
                identifier, wire.JOB_STATE_INTERRUPTED, 'Controller restarted; inspect operation status before retrying'
            )

    def active(self) -> list[wire.Job]:
        with Session(self.engine) as session:
            return [message(record) for record in session.scalars(select(JobRecord).where(JobRecord.state.in_([1, 2])))]

    def checkpoint(self, job: str, value: Publication) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(
                PublicationRecord(
                    job_id=job,
                    parent=value.parent,
                    parent_device=value.parent_device,
                    parent_inode=value.parent_inode,
                    staging=value.staging,
                    destination=value.destination,
                    device=value.device,
                    inode=value.inode,
                    phase=value.phase,
                )
            )

    def recovery_jobs(self) -> list[wire.Job]:
        """Include unresolved extraction journals even after a terminal error."""
        with Session(self.engine) as session:
            records = session.scalars(
                select(JobRecord).where(
                    or_(
                        JobRecord.state.in_([wire.JOB_STATE_QUEUED, wire.JOB_STATE_RUNNING]),
                        (JobRecord.operation == wire.OPERATION_EXTRACT_FILES)
                        & (JobRecord.state != wire.JOB_STATE_SUCCEEDED)
                        & JobRecord.id.in_(select(PublicationRecord.job_id)),
                    )
                )
            )
            return [message(record) for record in records]

    def publication(self, job: str) -> Publication | None:
        with Session(self.engine) as session:
            record = session.get(PublicationRecord, job)
            if record is None:
                return None
            phase = record.phase
            if phase not in ('preparing', 'extracting', 'publishing', 'published'):
                raise ValueError('Invalid extraction journal phase')
            return Publication(
                record.parent,
                record.parent_device,
                record.parent_inode,
                record.staging,
                record.destination,
                record.device,
                record.inode,
                phase,
            )

    def close(self) -> None:
        self.engine.dispose()
