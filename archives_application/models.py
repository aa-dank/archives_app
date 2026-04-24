# archives_application/models.py

from archives_application import db, login_manager
from datetime import datetime
from flask_login import UserMixin
from pgvector.sqlalchemy import Vector
from sqlalchemy import func, CheckConstraint


@login_manager.user_loader
def load_user(user_id):
    return UserModel.query.get(int(user_id))


class UserModel(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    first_name = db.Column(db.String)
    last_name = db.Column(db.String)
    roles = db.Column(db.String)
    password = db.Column(db.String(60))
    active = db.Column(db.Boolean, default=True, nullable=False)
    archived_files = db.relationship('ArchivedFileModel', backref='archivist', lazy=True)

    def __repr__(self):
        return f"User('{self.email}')"


class ArchivedFileModel(db.Model):
    __tablename__ = 'archived_files'
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('files.id'))
    destination_path = db.Column(db.String, nullable=False)
    project_number = db.Column(db.String)
    document_date = db.Column(db.String)
    destination_directory = db.Column(db.String)
    file_code = db.Column(db.String)
    file_size = db.Column(db.Float, nullable=False)
    date_archived = db.Column(db.DateTime, nullable=False)
    archivist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes = db.Column(db.String)
    filename = db.Column(db.String)

    def __repr__(self):
        return f"Archived File('{self.destination_path}', '{self.project_number}', '{self.file_size}')"


class ServerChangeModel(db.Model):
    __tablename__ = 'server_changes'
    id = db.Column(db.Integer, primary_key=True)
    old_path = db.Column(db.String)
    new_path = db.Column(db.String)
    change_type = db.Column(db.String, nullable=False)
    files_effected = db.Column(db.Integer)
    data_effected = db.Column(db.Numeric)
    date = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    def __repr__(self):
        return f"Server Change('{self.change_type}', '{self.old_path}', '{self.new_path}')"


class TimekeeperEventModel(db.Model):
    __tablename__ = 'timekeeper'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    datetime = db.Column(db.DateTime, nullable=False)
    clock_in_event = db.Column(db.Boolean, default=False, nullable=False)
    journal = db.Column(db.Text)

    def __repr__(self):
        return f"Timekeeper event: ('{self.change_type}', '{self.old_path}', '{self.new_path}')"


class FileModel(db.Model):
    __tablename__ = "files"
    id = db.Column(db.Integer, primary_key=True)
    hash = db.Column(db.String, unique=True, index=True, nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    extension = db.Column(db.String)
    content = db.relationship(
        "FileContentModel",
        back_populates="file",
        uselist=False,
        passive_deletes=True,   # let postgres on-delete-cascade do its job
        cascade="all, delete-orphan",  # optional but sane
    )
    content_failure = db.relationship(
        "FileContentFailureModel",
        back_populates="file",
        uselist=False,
        passive_deletes=True,
        cascade="all, delete-orphan",
    )
    date_mentions = db.relationship(
        "FileDateMentionModel",
        back_populates="file",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"file: {self.id}, {self.hash}, {self.size}, {self.extension}"


class FileLocationModel(db.Model):

    __tablename__ = "file_locations"
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=False)
    file = db.relationship('FileModel', backref=db.backref('locations', lazy=True))
    file_server_directories = db.Column("file_server_directories", db.String)
    filename = db.Column("filename", db.String)
    existence_confirmed = db.Column("existence_confirmed", db.DateTime)
    hash_confirmed = db.Column("hash_confirmed", db.DateTime)

    def __repr__(self):
        return f"File Location: {self.id}, {self.file_id}, {self.file_server_directories}, {self.filename}, {self.existence_confirmed}, {self.hash_confirmed}"
    
    @classmethod
    def filepath_search_query(cls, query_str, full_path=True):
        """
        Search the file locations for a query string.
        
        Resources:
        https://amitosh.medium.com/full-text-search-fts-with-postgresql-and-sqlalchemy-edc436330a0c
        https://stackoverflow.com/questions/42388956/create-a-full-text-search-index-with-sqlalchemy-on-postgresql

        :param query_str: str: The query string to search for.
        :param full_path: bool: If true, search the full path and filename, otherwise just search the filename.
        """
        # replace periods with spaces in the filename to ensure file extensions are treated as separate words
        adjusted_filename = func.regexp_replace(cls.filename, r'\.', ' ', 'gi')
        
        # create the tsvector and tsquery
        vector = func.to_tsvector('english', adjusted_filename)
        query_vector = func.websearch_to_tsquery(query_str)

        # if full_path is true, then run the query against the combined path and filename
        if full_path:
            path_vector = func.to_tsvector('english', cls.file_server_directories)
            vector = vector.op('||')(path_vector)
        
        query = cls.query.filter(vector.op('@@')(query_vector))
        return query


class WorkerTaskModel(db.Model):
    __tablename__ = 'worker_tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(255), nullable=False)
    time_enqueued = db.Column(db.DateTime, nullable=False)
    time_completed = db.Column(db.DateTime)
    origin = db.Column(db.String(255), nullable=False)
    function_name = db.Column(db.String(255))
    status = db.Column(db.String)
    task_results = db.Column(db.JSON)

    def __repr__(self):
        return f"Enqueued Task: {self.id}, {self.task_id}, {self.time_enqueued}, {self.origin}, {self.function_name}, {self.time_completed}, {self.status}, {self.task_results}"


class ProjectCaanModel(db.Model):
    __tablename__ = 'project_caans'
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), primary_key=True)
    caan_id = db.Column(db.Integer, db.ForeignKey('caans.id'), primary_key=True)
    project = db.relationship('ProjectModel', back_populates='project_caans', overlaps="caans,project_caans,projects")
    caan = db.relationship('CAANModel', back_populates='project_caans', overlaps="caans,project_caans,projects")


class ProjectModel(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String, nullable=False)
    name = db.Column(db.String, nullable=False)
    file_server_location = db.Column(db.String)
    drawings = db.Column(db.Boolean)
    fmp_id_primary = db.Column(db.Integer, unique=True)
    closed = db.Column(db.Boolean)
    campus_client = db.Column(db.String)
    last_synced_at = db.Column(db.DateTime(timezone=True))
    caans = db.relationship('CAANModel', secondary=ProjectCaanModel.__table__, back_populates='projects', overlaps="project_caans,project,caan")
    contracts = db.relationship('ContractModel', backref='project', lazy=True)
    project_caans = db.relationship('ProjectCaanModel', back_populates='project', overlaps="caans,projects")

    def __repr__(self):
        return f"project: {self.id}, {self.number}, {self.name}, {self.file_server_location}, {self.drawings}"
    

class CAANModel(db.Model):
    __tablename__ = "caans"
    id = db.Column(db.Integer, primary_key=True)
    caan = db.Column(db.String, nullable=False, unique=True)
    name = db.Column(db.String)
    description = db.Column(db.String)
    fmp_id_primary = db.Column(db.Integer, unique=True)
    address_street = db.Column(db.String)
    address_city = db.Column(db.String)
    address_zip = db.Column(db.String)
    area = db.Column(db.String)
    last_synced_at = db.Column(db.DateTime(timezone=True))
    projects = db.relationship('ProjectModel', secondary=ProjectCaanModel.__table__, back_populates='caans', overlaps="project_caans,project,caan")
    project_caans = db.relationship('ProjectCaanModel', back_populates='caan', overlaps="caans,projects")
    
    def __repr__(self):
        return f"caan: {self.id}, {self.caan}, {self.name}, {self.description}"

class FileContentModel(db.Model):
    __tablename__ = 'file_contents'
    __table_args__ = (
        db.Index('ix_file_contents_minilm_emb', 'minilm_emb', postgresql_using='ivfflat',
                 postgresql_ops={'minilm_emb': 'vector_cosine_ops'}, postgresql_with={'lists': 100}),
        db.Index('ix_file_contents_mpnet_emb', 'mpnet_emb', postgresql_using='ivfflat',
                 postgresql_ops={'mpnet_emb': 'vector_cosine_ops'}, postgresql_with={'lists': 100}),
    )
    file_hash = db.Column(db.String,
                          db.ForeignKey("files.hash", ondelete="CASCADE"),
                          primary_key=True)
    file = db.relationship("FileModel", back_populates="content", uselist=False)
    source_text = db.Column(db.Text)
    minilm_model = db.Column(db.Text, default='all-minilm-l6-v2')
    minilm_emb = db.Column(Vector(384))
    mpnet_model = db.Column(db.Text)
    mpnet_emb = db.Column(Vector(768))
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    text_length = db.Column(db.Integer)

    def __repr__(self):
        return f"FileContent: {self.file_hash}, text_length={self.text_length}, updated_at={self.updated_at}"


class FileContentFailureModel(db.Model):
    __tablename__ = 'file_content_failures'
    __table_args__ = (
        CheckConstraint("stage in ('extract', 'embed')", name='file_content_failures_stage_check'),
    )

    file_hash = db.Column(
        db.String,
        db.ForeignKey("files.hash", ondelete="CASCADE"),
        primary_key=True,
    )
    file = db.relationship("FileModel", back_populates="content_failure", uselist=False)
    stage = db.Column(db.String, nullable=False)
    error = db.Column(db.Text)
    attempts = db.Column(db.Integer, nullable=False, server_default="1")
    last_failed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())


class FileDateMentionModel(db.Model):
    __tablename__ = 'file_date_mentions'

    file_hash = db.Column(
        db.String,
        db.ForeignKey("files.hash", ondelete="CASCADE"),
        primary_key=True,
    )
    mention_date = db.Column(db.Date, nullable=False, primary_key=True)
    granularity = db.Column(db.Text, nullable=False, primary_key=True, default='day')
    mentions_count = db.Column(db.Integer, nullable=False, server_default="1")
    extractor = db.Column(db.Text)
    extracted_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    file = db.relationship("FileModel", back_populates="date_mentions")

    __table_args__ = (
        db.Index('ix_fdm_date', 'mention_date'),
        db.Index('ix_fdm_date_gran', 'mention_date', 'granularity'),
        db.Index('ix_fdm_file', 'file_hash'),
    )

    def __repr__(self):
        return f"FileDateMention: {self.file_hash}, {self.mention_date}, granularity={self.granularity}, count={self.mentions_count}"


class ContractModel(db.Model):
    __tablename__ = 'contracts'

    id = db.Column(db.Integer, primary_key=True)
    fmp_id_primary = db.Column(db.Integer, unique=True)
    contract_number = db.Column(db.Integer)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'))
    # Dates
    contract_date = db.Column(db.Date)
    ntp_start_date = db.Column(db.Date)
    beneficial_occupancy_date = db.Column(db.Date)
    substantial_completion_date = db.Column(db.Date)
    certificate_of_occupancy_date = db.Column(db.Date)
    noc_completion_date = db.Column(db.Date)
    noc_recorded_date = db.Column(db.Date)
    termination_date = db.Column(db.Date)
    bid_date = db.Column(db.Date)
    change_order_revised_expected_end = db.Column(db.Date)
    # Financial
    cost_estimate = db.Column(db.Numeric(14, 2))
    original_contract_cost = db.Column(db.Numeric(14, 2))
    change_order_total = db.Column(db.Numeric(14, 2))
    change_order_revised_cost = db.Column(db.Numeric(14, 2))
    account_number = db.Column(db.String)
    funding_number = db.Column(db.String)
    # Duration (days)
    original_project_duration = db.Column(db.Integer)
    change_order_time_total = db.Column(db.Integer)
    change_order_revised_duration = db.Column(db.Integer)
    # Parties & description
    contractor_org_name = db.Column(db.String)
    executive_design_org_name = db.Column(db.String)
    scope_description = db.Column(db.Text)
    # Sync metadata
    last_synced_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"Contract: {self.id}, project_id={self.project_id}, contractor={self.contractor_org_name}"
