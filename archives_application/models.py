from archives_application import db, login_manager
from datetime import datetime
from flask_login import UserMixin
from sqlalchemy import func


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
    date_archived = db.Column(db.DateTime, nullable=False, default=datetime.now) #TODO remove these and fill the datetime columns during api call
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
    date = db.Column(db.DateTime, nullable=False, default=datetime.now)
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

    def __repr__(self):
        return f"file: {self.id}, {self.hash}, {self.size}, {self.extension}"


class FileLocationModel(db.Model):

    __tablename__ = "file_locations"
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=False)
    file_server_directories = db.Column("file_server_directories", db.String)
    filename = db.Column("filename", db.String)
    existence_confirmed = db.Column("existence_confirmed", db.DateTime)
    hash_confirmed = db.Column("hash_confirmed", db.DateTime)

    def __repr__(self):
        return f"File Location: {self.id}, {self.file_id}, {self.file_server_directories}, {self.filename}, {self.existence_confirmed}, {self.hash_confirmed}"
    
    @classmethod
    def search_filenames(cls, query_str):
        vector = func.to_tsvector('english', cls.filename)
        query_results = cls.query.filter(vector @@ func.plainto_tsquery(query_str)).all()
        return query_results

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


project_caans = db.Table(
    'project_caans',
    db.Column('project_id', db.Integer, db.ForeignKey('projects.id'), primary_key=True),
    db.Column('caan_id', db.Integer, db.ForeignKey('caans.id'), primary_key=True)
)


class ProjectModel(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String, nullable=False)
    name = db.Column(db.String, nullable=False)
    file_server_location = db.Column(db.String)
    drawings = db.Column(db.Boolean)
    caans = db.relationship('CAANModel', secondary=project_caans, back_populates='projects')

    def __repr__(self):
        return f"project: {self.id}, {self.number}, {self.name}, {self.file_server_location}, {self.drawings}"
    

class CAANModel(db.Model):
    __tablename__ = "caans"
    id = db.Column(db.Integer, primary_key=True)
    caan = db.Column(db.String, nullable=False)
    name = db.Column(db.String)
    description = db.Column(db.String)
    projects = db.relationship('ProjectModel', secondary=project_caans, back_populates='caans')
    
    def __repr__(self):
        return f"caan: {self.id}, {self.caan}, {self.name}, {self.description}"