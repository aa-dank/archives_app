import contextlib
import flask
import sqlite3
import sys
import pandas as pd
from datetime import datetime

from .. import db
from ..utilities import roles_required
from ..models import UserModel, TimekeeperEventModel
from flask import current_app
from flask_login import login_required, current_user
from .forms import TimekeepingForm


timekeeper = flask.Blueprint('timekeeper', __name__)


def fetchall_query_to_dataframe(query_string: str, db_path:str):
    """
    Based on:
    https://stackoverflow.com/questions/9561832/what-if-i-dont-close-the-database-connection-in-python-sqlite/47501337#47501337
    :param query_string:
    :param db_path:
    :return:
    """
    df = pd.DataFrame()
    with contextlib.closing(sqlite3.connect(db_path)) as conn:  # auto-closes
        with conn:  # auto-commits
            cols = TimekeeperEventModel.__table__.columns.keys()
            df = pd.read_sql(sql=query_string, con=conn, columns=cols)
    return df


def pop_dialect_from_sqlite_uri(sql_uri:str):
    """
    turns the sqlite uri into a normal path by removing the sqlite prefix on the database path required by sqlalchemy.
    Useful for using normal sqlite3 queries on the database.
    :param sql_uri:
    :return:
    """
    new_url = sql_uri
    if sql_uri.startswith("sqlite"):
        # if the platform is not linux or mac, it is assumed to be windows
        if sys.platform.lower() not in ['linux', 'linux2', 'darwin']: #TODO are windows paths stored with back slash
            return "\\\\" + new_url.split("/")[-1]
        else:
            sqlite_prefix = new_url.split("/")[0]
            new_url = new_url[len(sqlite_prefix):]
            # remove first char while the first char is '/'.
            while new_url[0] == "/":
                new_url = new_url[1:]
    return new_url


@timekeeper.route("/timekeeper", methods=['GET', 'POST'])
@login_required
@roles_required(['ADMIN', 'ARCHIVIST'])
def timekeeper_event():
    """
    Main timekeeper endpoint that spits out html form for clocking in and clocking out. Clocking events
    :return:
    """

    def exception_handling_pattern(flash_message, thrown_exception):
        """
        subroutine for dealing with exceptions that pop up during time keeper api calls
        :param flash_message:
        :param thrown_exception:
        :return:
        """
        flash_message = flash_message + f": {thrown_exception}"
        flask.flash(flash_message, 'error')
        current_app.logger.error(thrown_exception, exc_info=True)
        return flask.redirect(flask.url_for('main.home'))


    def is_clocked_in(user_id):
        """
        uses the user id to query the database to see if the most recent timekeeper event from today was a clock_in_event

        :param user_id:
        :return: Tuple where first element is whether the user is clocked in, and the second element is any errors that
        may have occured while looking this up
        """
        user_id = int(user_id)
        query = f"SELECT * FROM timekeeper WHERE strftime('%Y-%m-%d', datetime) = strftime('%Y-%m-%d', date('now')) AND user_id = {user_id}"
        db_path = pop_dialect_from_sqlite_uri(current_app.config["SQLALCHEMY_DATABASE_URI"])
        try:

            df = fetchall_query_to_dataframe(query_string=query, db_path=db_path)
        except Exception as e:
            current_app.logger.error(f"Issue querying the timekeeper table: {e}")
            return (False, e)

        # if there are no records for the user, they are not clocked in
        if df.shape[0] == 0:
            return (False, '')

        df.sort_values(by='datetime', ascending=True, inplace=True)
        if df.head(1)["clock_in_event"] == True:
            return (True, '')

        return (False, '')

    current_user_id = UserModel.query.filter_by(email=current_user.email).first().id
    form = TimekeepingForm()
    clocked_in = False
    try:
        clocked_in, clock_in_check_error = is_clocked_in(user_id=current_user_id)
        if clock_in_check_error:
            raise Exception(clock_in_check_error)
    except Exception as e:
        return exception_handling_pattern(flash_message="Error when checking if user is clocked in", thrown_exception=e)

    if form.validate_on_submit():
        if clocked_in:
            if form.clock_out.data:
                try:
                    event_model = TimekeeperEventModel(user_id=current_user_id,
                                                       journal=form.journal.data,
                                                       clock_in_event=False)
                    db.session.add(event_model)
                    db.session.commit()
                    flask.flash("Successfully clocked out. Please, don't forget to log-out. Good-Bye.", 'success')
                    flask.redirect(flask.url_for('main.home'))
                except Exception as e:
                    return exception_handling_pattern(flash_message="Error recording user clock-out event", thrown_exception=e)

        else:
            if form.clock_in.data:
                try:
                    event_model = TimekeeperEventModel(user_id=current_user_id,
                                                       journal='',
                                                       clock_in_event=True)
                    db.session.add(event_model)
                    db.session.commit()
                    flask.flash(f'Successfully clocked in.', 'success')
                    flask.redirect(flask.url_for('main.home'))
                except Exception as e:
                    return exception_handling_pattern(flash_message="Error recording user clock-in event", thrown_exception=e)

    return flask.render_template('timekeeper.html', title='Timekeeper', form=form,  clocked_in=clocked_in)


