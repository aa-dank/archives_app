from archiver import db, login_manager
from datetime import datetime
from flask_login import UserMixin

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    first_name = db.Column(db.String, unique=True, nullable=False)
    las_name = db.Column(db.String, unique=True, nullable=False)
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
        #TODO does date_archived need to be changed to
        return f"Archived File('{self.destination_path}', '{self.project_number}', '{self.file_size}', '{self.date_archived}')"

class ServerChange(db.Model):
    __tablename__ = 'server_changes'
    id = db.Column(db.Integer, primary_key=True)
    old_path = db.Column(db.String)
    new_path = db.Column(db.String)
    change_type = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow())
    archivist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    def __repr__(self):
        return f"Server Change('{self.change_type}', '{self.date}', '{self.old_path}', '{self.new_path}')"
