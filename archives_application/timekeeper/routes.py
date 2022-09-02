import flask
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




def pop_dialect_from_sqlite_uri(sql_uri:str):
    """
    turns the sqlite uri into a normal path by removing the sqlite prefix on the database path required by sqlalchemy.
    Useful for using normal sqlite3 queries on the database.
    More info: https://docs.sqlalchemy.org/en/14/core/engines.html#database-urls
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

        #TODO query by datetime: todays_events_query2 = TimekeeperEventModel.query.filter_by(user_id=1, datetime=datetime.now().date())
        start_of_today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        db.session.commit()
        todays_events_query = TimekeeperEventModel.query.filter(TimekeeperEventModel.user_id == user_id,
                                                                TimekeeperEventModel.datetime > start_of_today)
        todays_events_df = pd.read_sql(todays_events_query.statement, todays_events_query.session.bind)

        # if there are no records for the user, they are not clocked in
        if todays_events_df.shape[0] == 0:
            return False

        todays_events_df.sort_values(by='datetime', ascending=False, inplace=True)
        if todays_events_df.iloc[0]['clock_in_event']:
            return True

        return False

    current_user_id = UserModel.query.filter_by(email=current_user.email).first().id
    form = TimekeepingForm()
    clocked_in = False
    try:
        clocked_in = is_clocked_in(user_id=current_user_id)

    except Exception as e:
        return exception_handling_pattern(flash_message="Error when checking if user is clocked in", thrown_exception=e)

    if form.validate_on_submit():
        if clocked_in:
            if form.clock_out.data:
                try:
                    event_model = TimekeeperEventModel(user_id=current_user_id,
                                                       datetime=datetime.now(),
                                                       journal=form.journal.data,
                                                       clock_in_event=False)
                    db.session.add(event_model)
                    db.session.commit()
                    flask.flash("Successfully clocked out. Please, don't forget to log-out. Good-Bye.", 'success')
                    return flask.redirect(flask.url_for('main.home'))
                except Exception as e:
                    return exception_handling_pattern(flash_message="Error recording user clock-out event", thrown_exception=e)

        else:
            if form.clock_in.data:
                try:
                    event_model = TimekeeperEventModel(user_id=current_user_id,
                                                       datetime=datetime.now(),
                                                       journal='',
                                                       clock_in_event=True)
                    db.session.add(event_model)
                    db.session.commit()
                    flask.flash(f'Successfully clocked in.', 'success')
                    return flask.redirect(flask.url_for('main.home'))
                except Exception as e:
                    return exception_handling_pattern(flash_message="Error recording user clock-in event", thrown_exception=e)

    return flask.render_template('timekeeper.html', title='Timekeeper', form=form,  clocked_in=clocked_in)


