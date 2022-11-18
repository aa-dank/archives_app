import flask
import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from datetime import datetime, timedelta
from dateutil import parser

from .. import db
from .. import utilities
from ..models import UserModel, TimekeeperEventModel, ArchivedFileModel
from flask import current_app
from flask_login import login_required, current_user
from .forms import TimekeepingForm, TimeSheetForm, TimeSheetAdminForm


timekeeper = flask.Blueprint('timekeeper', __name__)


def exception_handling_pattern(flash_message, thrown_exception, app_obj):
    """
    Sub-process for handling patterns
    @param flash_message:
    @param thrown_exception:
    @param app_obj:
    @return:
    """
    flash_message = flash_message + f": {thrown_exception}"
    flask.flash(flash_message, 'error')
    app_obj.logger.error(thrown_exception, exc_info=True)
    return flask.redirect(flask.url_for('main.home'))


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


def daterange(start_date: datetime, end_date: datetime):
    """
    Generator for iterating through dates
    https://stackoverflow.com/questions/1060279/iterating-through-a-range-of-dates-in-pythonz
    @param start_date:
    @param end_date:
    @return:
    """
    for n in range(int((end_date - start_date).days)):
        yield start_date + timedelta(n)


def hours_worked_in_day(day, user_id):

    def clocked_out_everytime(event_type_col: pd.Series):
        """
        Checks that clock_in_event column is alternating. that the last event was a clock out event.
        Note that the column passed to this function needs to sorted by time.
        """
        if event_type_col.shape[0] < 2 or not event_type_col.iloc[0]:
            return False
        last = event_type_col.iloc[0]
        for current in event_type_col[1:]:
            if last == current:
                return False

            last = current

        # if the last entry is clock-in event return false
        if event_type_col.iloc[-1]:
            return False

        return True

    hours_worked = 0

    # query sqlite db to retrieve all entries for given user id and day date. Put them in dataframe.
    days_end = day + timedelta(days=1)
    query = TimekeeperEventModel.query.filter(TimekeeperEventModel.user_id == user_id,
                                              TimekeeperEventModel.datetime >= day.strftime("%Y-%m-%d"),
                                              TimekeeperEventModel.datetime < days_end.strftime("%Y-%m-%d"))
    timesheet_df = pd.read_sql(query.statement, query.session.bind)
    timesheet_df.sort_values(by='datetime', inplace=True)
    if timesheet_df.shape[0] == 0:
        clock_ins_have_clock_outs = True #
    else:
        clock_ins_have_clock_outs = clocked_out_everytime(timesheet_df["clock_in_event"])

        # iterate over time in events...
        time_in_events_df = timesheet_df[timesheet_df['clock_in_event']]
        time_in_events_df.sort_values(by='datetime', inplace=True)
        for idx, row in time_in_events_df.iterrows():
            if not clock_ins_have_clock_outs:
                break

            time_in = row['datetime']

            # create new dataframe of time-out events that happened after time-in event.
            time_out_events_df = timesheet_df[~timesheet_df['clock_in_event']]
            time_out_events_df = time_out_events_df[time_out_events_df["datetime"] > time_in]
            time_out_events_df.sort_values(by='datetime', inplace=True)

            # if there are no timeout events return values
            if time_out_events_df.shape[0] == 0:
                break

            # calculate the differences between this clock in and the subsequent clock out.
            delta = time_out_events_df.iloc[0].loc["datetime"] - time_in
            hours_worked += delta.days * 24
            hours_worked += delta.seconds // 3600

    return hours_worked, clock_ins_have_clock_outs


@timekeeper.route("/timekeeper", methods=['GET', 'POST'])
@login_required
@utilities.roles_required(['ADMIN', 'ARCHIVIST'])
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

        #TODO query by datetime: todays_events_query2 = TimekeeperEventModel.query.filter_by(user_id=1, datetime=datetime.now().date())
        start_of_today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        db.session.commit()
        todays_events_query = TimekeeperEventModel.query.filter(TimekeeperEventModel.user_id == user_id,
                                                                TimekeeperEventModel.datetime > start_of_today)

        # sometimes the bind was none, so this hopefully resolves this.
        eng = todays_events_query.session.bind
        if not eng:
            eng = db.engine

        todays_events_df = pd.read_sql(todays_events_query.statement, eng)

        # if there are no records for the user, they are not clocked in
        if todays_events_df.shape[0] == 0:
            return False

        todays_events_df.sort_values(by='datetime', ascending=False, inplace=True)
        if todays_events_df.iloc[0]['clock_in_event']:
            return True

        return False


    try:
        current_user_id = UserModel.query.filter_by(email=current_user.email).first().id
        form = TimekeepingForm()
        clocked_in = is_clocked_in(user_id=current_user_id)

    except Exception as e:
        return exception_handling_pattern(flash_message="Error when checking if user is clocked in",
                                          thrown_exception=e, app_obj=flask.current_app)

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
                    return exception_handling_pattern(flash_message="Error recording user clock-out event",
                                                      thrown_exception=e, app_obj=flask.current_app)

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
                    return exception_handling_pattern(flash_message="Error recording user clock-in event",
                                                      thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('timekeeper.html', title='Timekeeper', form=form,  clocked_in=clocked_in,
                                 id=current_user_id)


@timekeeper.route("/timekeeper/<employee_id>", methods=['GET', 'POST'])
@login_required
@utilities.roles_required(['ADMIN', 'ARCHIVIST'])
def user_timesheet(employee_id):

    def compile_journal(date: datetime, timecard_df: pd.DataFrame, delimiter_str:str):
        """
        Combines journalcolumn into a single str
        @param date:
        @param timecard_df:
        @param delimiter_str:
        @return:
        """
        strftime_dt = lambda dt: dt.strftime("%Y-%m-%d")
        timecard_df = timecard_df[timecard_df["datetime"].map(strftime_dt) == date.strftime("%Y-%m-%d")]
        compiled_journal = delimiter_str.join([journal for journal in timecard_df["journal"].tolist() if journal])
        return compiled_journal

    form = TimeSheetForm()
    timesheet_df = None
    try:

        query_start_date = datetime.now() - timedelta(days = 14)
        query_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        query_end_date = datetime.now()
        if form.validate_on_submit():
            user_start_date = form.timesheet_begin.data
            user_end_date = form.timesheet_end.data
            query_start_date = datetime(year=user_start_date.year, month=user_start_date.month, day=user_start_date.day)
            query_end_date = datetime(year=user_end_date.year, month=user_end_date.month, day=user_end_date.day)

        query = TimekeeperEventModel.query.filter(TimekeeperEventModel.user_id == employee_id,
                                              TimekeeperEventModel.datetime > query_start_date)
        timesheet_df = pd.read_sql(query.statement, query.session.bind)
    except Exception as e:
        exception_handling_pattern(flash_message="Error getting user timekeeper events from database: ",
                                   thrown_exception=e, app_obj=flask.current_app)

    if timesheet_df.shape[0] == 0:
        flask.flash(f"No clocked time recorded for request period for the id {employee_id}.", category='info')
        return flask.redirect(flask.url_for('main.home'))

    try:
        # Create a list of dictionaries, where each dictionary is the aggregated data for that day
        all_days_data = []
        for range_date in daterange(start_date=query_start_date.date(), end_date=query_end_date.date()):
            day_data = {"Date":range_date.strftime('%Y-%m-%d')}

            # calculate hours and/or determine if entering them is incomplete
            hours, timesheet_complete = hours_worked_in_day(range_date, employee_id)
            if not timesheet_complete:
                day_data["Hours Worked"] = "TIME ENTRY INCOMPLETE"
            else:
                day_data["Hours Worked"] = str(hours)

            # Mush all journal entries together into a single journal entry
            compiled_journal = compile_journal(range_date, timesheet_df, " \ ")
            day_data["journal"] = compiled_journal
            all_days_data.append(day_data)

    except Exception as e:
        exception_handling_pattern(flash_message="Error creating table of hours worked: ",
                                   thrown_exception=e, app_obj=flask.current_app)

    aggregate_hours_df = pd.DataFrame.from_dict(all_days_data)
    html_table = aggregate_hours_df.to_html(index=False)

    return flask.render_template('timesheet.html', title="Timesheet", form=form, table=html_table)


@timekeeper.route("/timekeeper/admin", methods=['GET', 'POST'])
@login_required
@utilities.roles_required(['ADMIN'])
def choose_employee():
    try: #TODO lazy try-except should be broken into two
        form = TimeSheetAdminForm()

        # Get employee emails to use in dropdown choices
        is_archivist = lambda user: 'ARCHIVIST' in user.roles.split(",")
        employee_emails = [emp.email for emp in UserModel.query.all() if is_archivist(emp)]
        form.employee_email.choices = employee_emails

        if form.validate_on_submit():
            # get selected employee id and use it to redirect to correct timesheet endpoint
            employee_email = form.employee_email.data
            employee_id = UserModel.query.filter_by(email=employee_email).first().id
            return flask.redirect(flask.url_for('timekeeper.user_timesheet', employee_id=employee_id))
    except Exception as e:
        return exception_handling_pattern(flash_message="Error trying elicit or process employee email for making a timesheet :",
                                          thrown_exception=e, app_obj=flask.current_app)
    return flask.render_template('timekeeper_admin.html', form=form)

@timekeeper.route("/timekeeper/aggregate_metrics")
@login_required
@utilities.roles_required(['ADMIN'])
def archived_metrics_dashboard():

    def generate_daily_chart_stats_df(start_date:datetime=None, end_date:datetime=None):
        """

        @param start_date:
        @param end_date:
        @return:
        """

        if not end_date:
            end_date = datetime.now()

        if not start_date:
            start_date = end_date - timedelta(days=28)


        start_date_str = start_date.strftime(current_app.config.get('DEFAULT_DATETIME_FORMAT'))
        end_date_str = end_date.strftime(current_app.config.get('DEFAULT_DATETIME_FORMAT'))
        query = ArchivedFileModel.query.filter(ArchivedFileModel.date_archived.between(start_date_str, end_date_str))

        eng = query.session.bind
        if not eng:
            eng = db.engine
        df = pd.read_sql(query.statement, eng)

        #replace datetime timestamp with just the date
        get_date = lambda dt: dt.date()
        df["date_archived"] = df["date_archived"].map(get_date)

        #groupby date to calculate sum of bytes archived and number of files archived on each day
        day_groups = df.groupby('date_archived')
        volume_sum_by_day = day_groups['file_size'].agg(np.sum)
        docs_by_day = day_groups.size()
        docs_by_day.name = "files_archived"
        data_by_day_df = pd.concat([volume_sum_by_day, docs_by_day], axis=1)

        bytes_to_megabytes = lambda b: b / 10000000
        data_by_day_df["megabytes_archived"] = data_by_day_df["file_size"].map(bytes_to_megabytes)
        data_by_day_df.drop(["file_size"], axis=1, inplace=True)
        data_by_day_df.reset_index(level=0, inplace=True)

        for date in daterange(start_date=start_date, end_date=end_date):
            if date.weekday() in [5,6]:
                continue

            if not date in data_by_day_df["date_archived"]:
                new_row_df = pd.DataFrame({"date_archived":[date.date()], "files_archived":[0], "megabytes_archived":[0]})
                data_by_day_df = pd.concat([data_by_day_df, new_row_df])

        data_by_day_df = data_by_day_df.sort_values(by=["date_archived"]).reset_index(drop=True)
        return data_by_day_df

    def archiving_production_barchart(df:pd.DataFrame):
        # Function for formatting datetimes for disp[lay on the plot
        reformat_dt_str = lambda x: parser.parse(x).strftime("%m/%d/%Y")

        # retrieve plot title dates
        first_date_str = str(df.loc[0]['date_archived'])
        plot_start_str = reformat_dt_str(first_date_str)
        last_date_str = str(df.loc[df.shape[0] - 1]['date_archived'])
        plot_end_str = reformat_dt_str(last_date_str)


        # plot settings
        sns.set(font_scale=1.3)
        sns.set_style("ticks")
        fig = plt.figure(figsize=(15, 8))
        width_scale = .45

        # create bytes charts
        bytes_axis = sns.barplot(x="date_archived", y="megabytes_archived", data=df)
        bytes_axis.set(title=f"Files and Megabytes Archived from {plot_start_str} to {plot_end_str}",
                       xlabel="Date",
                       ylabel="MegaBytes")
        for bar in bytes_axis.containers[0]:
            bar.set_width(bar.get_width() * width_scale)

        # create files axis
        file_num_axis = bytes_axis.twinx()
        files_axis = sns.barplot(x="date_archived", y="files_archived", data=df, hatch='xx',
                                 ax=file_num_axis)
        files_axis.set(ylabel="Files")
        for bar in files_axis.containers[0]:
            bar_x = bar.get_x()
            bar_w = bar.get_width()
            bar.set_x(bar_x + bar_w * (1 - width_scale))
            bar.set_width(bar_w * width_scale)

        # reformat datetimes into smaller, more readable strings
        bytes_axis.set_xticklabels([reformat_dt_str(x.get_text()) for x in bytes_axis.get_xticklabels()], rotation=30)

        a_val = 0.6
        colors = ['#EA5739', '#FEFFBE', '#4BB05C']
        legend_patch_files = mpatches.Patch(facecolor=colors[0], alpha=a_val, hatch=r'xx', label='Files')
        legend_patch_bytes = mpatches.Patch(facecolor=colors[0], alpha=a_val, label='Megabytes')

        plt.legend(handles=[legend_patch_files, legend_patch_bytes])
        return fig

    df = pd.DataFrame()
    production_plot = plt.figure()
    jpg_path: str = None
    try:
        df = generate_daily_chart_stats_df()
    except Exception as e:
        exception_handling_pattern(flash_message="Error trying to generate aggregate daily data for plot:",
                                   thrown_exception=e,
                                   app_obj=current_app)

    if df.shape[0] == 0:
        #TODO
        pass
    else:
        try:
            production_plot = archiving_production_barchart(df=df)
        except Exception as e:
            exception_handling_pattern(flash_message="Error making the plot object:",
                                       thrown_exception=e,
                                       app_obj=current_app)

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        plot_jpg_filename = "total_prod_" + timestamp + ".jpg"
        plot_jpg_path = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files", plot_jpg_filename])
        production_plot.savefig(plot_jpg_path)

    plot_jpg_url = flask.url_for(r"static",
                                      filename="temp_files/" + utilities.split_path(plot_jpg_path)[-1])

    # Record image path to session so it can be deleted upon logout
    if not flask.session[current_user.email].get('temporary files'):
        flask.session[current_user.email]['temporary files'] = []

    # if we made a preview image, record the path in the session so it can be removed upon logout
    flask.session[current_user.email]['temporary files'].append(plot_jpg_path)

    return flask.render_template('archiving_metrics.html', title='Archiving Metrics', plot_image=plot_jpg_url)
















