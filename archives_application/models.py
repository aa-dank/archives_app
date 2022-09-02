from archives_application import db, login_manager
from datetime import datetime
from flask_login import UserMixin


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
    archived_files = db.relationship('ArchivedFileModel', backref='archivist', lazy=True)

    def __repr__(self):
        return f"User('{self.email}')"


class ArchivedFileModel(db.Model):
    __tablename__ = 'archived_files'
    id = db.Column(db.Integer, primary_key=True)
    destination_path = db.Column(db.String, nullable=False)
    project_number = db.Column(db.String, nullable=False)
    document_date = db.Column(db.String)
    destination_directory = db.Column(db.String, nullable=False)
    file_code = db.Column(db.String, nullable=False)
    file_size = db.Column(db.Float, nullable=False)
    date_archived = db.Column(db.DateTime, nullable=False, default=datetime.now()) #TODO remove these and fill the datetime columns during api call
    archivist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes = db.Column(db.String)
    filename = db.Column(db.String)
    extension = db.Column(db.String)

    def __repr__(self):
        # TODO does date_archived need to be changed to string
        return f"Archived File('{self.destination_path}', '{self.project_number}', '{self.file_size}')"

class ServerChangeModel(db.Model):
    __tablename__ = 'server_changes'
    id = db.Column(db.Integer, primary_key=True)
    old_path = db.Column(db.String)
    new_path = db.Column(db.String)
    change_type = db.Column(db.String, nullable=False)
    files_effected = db.Column(db.Integer)
    data_effected = db.Column(db.Numeric)
    date = db.Column(db.DateTime, nullable=False, default=datetime.now())
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