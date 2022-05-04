from archiver import db
from datetime import datetime

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    archived_files = db.relationship('ArchivedFile', backref='archivist', lazy=True)

    def __repr__(self):
        return f"User('{self.email}')"


class ArchivedFile(db.Model):
    __tablename__ = 'archived_files'
    id = db.Column(db.Integer, primary_key=True)
    destination_path = db.Column(db.String, nullable=False)
    project_number = db.Column(db.String, nullable=False)
    document_date = db.Column(db.String)
    destination_directory = db.Column(db.String, nullable=False)
    file_code = db.Column(db.String, nullable=False)
    file_size = db.Column(db.Float, nullable=False)
    date_archived = db.Column(db.DateTime, nullable=False, default=datetime.utcnow())
    archivist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes = db.column(db.String)
    filename = db.column(db.String)
    extension = db.column(db.String)

    def __repr__(self):
        return f"Archived File('{self.destination_path}', '{self.project_number}', '{self.file_size}', '{self.date_archived}')"
