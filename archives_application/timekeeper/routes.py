import contextlib
import flask
import sqlite3
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
            with contextlib.closing(conn.cursor()) as c:
                cols = [description[0] for description in c.description]
            df = pd.read_sql(sql=query_string, con=conn, columns=cols)
    return df


def pop_dialect_from_sqlite_uri(sql_uri:str):
    new_url = sql_uri
    if sql_uri.startswith("sqlite"):
        if
        new_url = sql_uri.split("/")[-1]


@timekeeper.route("/timekeeper", methods=['GET', 'POST'])
@login_required
@roles_required(['ADMIN', 'ARCHIVIST'])
def timekeeper_event():
    """
    Main timekeeper endpoint that spits out html form for clocking in and clocking out. Clocking events
    :return:
    """
    def is_clocked_in(user_id):
        """
        uses the user id to query the database to see if the most recent timekeeper event from today was a clock_in_event

        :param user_id:
        :return: Tuple where first element is whether the user is clocked in, and the second element is any errors that
        may have occured while looking this up
        """
        user_id = int(user_id)
        query = f"SELECT * FROM timekeeper WHERE strftime('%Y-%m-%d', datetime) = strftime('%Y-%m-%d', date('now')) AND user_id = {user_id}"
        try:

            df = fetchall_query_to_dataframe(query_string=query, db_path=current_app.config["Sqalchemy_Database_Location"])
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
        flask.flash(f'Error when checking if user is clocked in: {e}', 'error')
        current_app.logger.error(e)
        flask.redirect(flask.url_for('main.home'))

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
                    flask.flash(f'Error during clocking out process: {e}', 'error')
                    current_app.logger.error(e)
                    flask.redirect(flask.url_for('main.home'))

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
                    flask.flash(f'Error duriing logging in: {e}', 'error')
                    current_app.logger.error(e)
                    flask.redirect(flask.url_for('main.home'))

    return flask.render_template('timekeeper.html', title='Timekeeper', form=form,  clocked_in=clocked_in)


