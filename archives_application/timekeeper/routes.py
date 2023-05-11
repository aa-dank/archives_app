import os
import sys
from datetime import datetime, timedelta

import flask
import flask_sqlalchemy
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from dateutil import parser
from flask import current_app
from flask_login import login_required, current_user
from sqlalchemy import and_

from archives_application.timekeeper.forms import TimekeepingForm, TimeSheetForm, TimeSheetAdminForm
from archives_application import utilities
from archives_application.models import UserModel, TimekeeperEventModel, ArchivedFileModel, db

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

def db_query_to_df(query: flask_sqlalchemy.query.Query):
    results = query.all()
    df = pd.DataFrame([row.__dict__ for row in results])
    return df


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


def hours_worked_in_day(day: datetime.date, user_id: int):

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

    # query db to retrieve all entries for given user id and day date. Put them in dataframe.
    days_end = day + timedelta(days=1)
    query = TimekeeperEventModel.query.filter(TimekeeperEventModel.user_id == user_id,
                                              TimekeeperEventModel.datetime >= day.strftime("%Y-%m-%d"),
                                              TimekeeperEventModel.datetime < days_end.strftime("%Y-%m-%d"))

    timesheet_df = db_query_to_df(query=query)
    if timesheet_df.shape[0] == 0:
        clock_ins_have_clock_outs = True
    else:
        timesheet_df.sort_values(by='datetime', inplace=True)
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


def compile_journal(date: datetime, timecard_df: pd.DataFrame, delimiter_str: str):
    """
    Combines journalcolumn into a single str
    @param date:
    @param timecard_df:
    @param delimiter_str:
    @param delimiter_str:
    @return:
    """
    strftime_dt = lambda dt: dt.strftime("%Y-%m-%d")
    timecard_df = timecard_df[timecard_df["datetime"].map(strftime_dt) == date.strftime("%Y-%m-%d")]
    compiled_journal = delimiter_str.join([journal for journal in timecard_df["journal"].tolist() if journal])
    return compiled_journal

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
        todays_events_df = db_query_to_df(todays_events_query)



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
            if form.clock_out.data or form.clock_out_log_out.data:
                try:
                    event_model = TimekeeperEventModel(user_id=current_user_id,
                                                       datetime=datetime.now(),
                                                       journal=form.journal.data,
                                                       clock_in_event=False)
                    db.session.add(event_model)
                    db.session.commit()

                    if form.clock_out_log_out.data:
                        return flask.redirect(flask.url_for('users.logout'))

                    flask.flash("Successfully clocked out.", 'success')
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

    form = TimeSheetForm()
    timesheet_df = None

    # get user information from the database
    try:
        employee = UserModel.query.filter(UserModel.id == employee_id).one_or_none()
        archivist_dict = {'email': employee.email, 'id': employee.id}

    except Exception as e:
        exception_handling_pattern(flash_message=f"Error trying to get user info from the database for user id {employee_id}",
                                   thrown_exception=e,
                                   app_obj=flask.current_app)

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
                                                  TimekeeperEventModel.datetime >= query_start_date,
                                                  TimekeeperEventModel.datetime <= query_end_date)
        timesheet_df = db_query_to_df(query=query)


        # issues with sending commands to DB can result in pd.read_sql returning None instead of Dataframe
        assert type(timesheet_df) == type(pd.DataFrame()), "pd.read_sql did not return a Dataframe."

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
    archivist_dict["html_table"] = aggregate_hours_df.to_html(index=False, classes="table-hover table-dark")

    return flask.render_template('timesheet_tables.html', title="Timesheet", form=form, archivist_info_list=[archivist_dict])


@timekeeper.route("/timekeeper/all", methods=['GET', 'POST'])
@login_required
@utilities.roles_required(['ADMIN', 'ARCHIVIST'])
def all_timesheets():
    """
    Endpoint to display all timesheets for archivists.

    Route: '/timekeeper/all'
    Methods: GET, POST
    Access: Only accessible to users with 'ADMIN' or 'ARCHIVIST' roles and requires login.

    Form data:
    * timesheet_begin: Start date of timesheet range to display
    * timesheet_end: End date of timesheet range to display

    Data retrieval:
    1. Retrieves 'active' archivist emails from the UserModel database
    2. Filters timekeeper events between the start and end dates for all active archivists
    3. Creates a dataframe from the filtered timekeeper events
    4. Aggregates data for each archivist for each day within the date range
    5. Generates a table for each archivist with columns for date, hours worked, and journal

    Data processing:
    * Calculates hours worked and aggregates journal entries for each day in the date range
    * Adds the calculated data to the archivist's data in a dictionary

    Data return:
    * Renders the 'timesheet_tables.html' template with the following data:
    - form: the TimeSheetForm object
    - title: "Timesheets"
    - archivist_info_list: A list of dictionaries containing information for each archivist, including:
    - email: email of the archivist
    - id: id of the archivist
    - timesheet_df: a dataframe with the timesheet data
    - html_table: the timesheet data in HTML format
    """

    form = TimeSheetForm()
    try:
        # Get 'active' employee emails to use in dropdown choices
        archivists = [{'email': employee.email, 'id': employee.id} for employee in
                      UserModel.query.filter(UserModel.roles.contains('ARCHIVIST'), UserModel.active.is_(True))]

    except Exception as e:
        return exception_handling_pattern(
            flash_message="Error retrieving active archivists from database:",
            thrown_exception=e, app_obj=flask.current_app)

    timesheet_df = pd.DataFrame()
    try:
        # Create datetime objects for start and end dates. Includes end and start dates.
        query_start_date = datetime.now() - timedelta(days = 14)
        query_start_date = query_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        query_end_date = datetime.now()
        query_end_date = query_end_date.replace(hour=23, minute=0, second=0, microsecond=0)
        if form.validate_on_submit():
            user_start_date = form.timesheet_begin.data
            user_end_date = form.timesheet_end.data
            query_start_date = datetime(year=user_start_date.year, month=user_start_date.month, day=user_start_date.day)
            query_end_date = datetime(year=user_end_date.year, month=user_end_date.month, day=user_end_date.day)

        query = db.session.query(TimekeeperEventModel)\
            .join(UserModel, TimekeeperEventModel.user_id == UserModel.id)\
            .filter(and_(UserModel.active == True, UserModel.roles.like("%ARCHIVIST%"),
                         TimekeeperEventModel.datetime.between(query_start_date, query_end_date)))

        timesheet_df = db_query_to_df(query=query)

    except Exception as e:
        exception_handling_pattern(flash_message="Error creating dataframe for all archivists: ",
                                   thrown_exception=e, app_obj=flask.current_app)

    try:
        users_timesheet_dict = dict(list(timesheet_df.groupby('user_id')))
        for archivist_dict in archivists:
            user_timesheet_df = users_timesheet_dict.get(archivist_dict['id'])
            if type(user_timesheet_df) == type(pd.DataFrame()):
                archivist_dict["raw_df"] = user_timesheet_df

                # Create a list of dictionaries, where each dictionary is the aggregated data for that day
                all_days_data = []
                for range_date in daterange(start_date=query_start_date.date(), end_date=query_end_date.date()):
                    day_data = {"Date": range_date.strftime('%Y-%m-%d')}

                    # calculate hours and/or determine if entering them is incomplete
                    hours, timesheet_complete = hours_worked_in_day(day=range_date, user_id=archivist_dict['id'])
                    if not timesheet_complete:
                        day_data["Hours Worked"] = "TIME ENTRY INCOMPLETE"
                    else:
                        day_data["Hours Worked"] = str(hours)

                    # Mush all journal entries together into a single journal entry
                    compiled_journal = compile_journal(date=range_date,
                                                       timecard_df=user_timesheet_df,
                                                       delimiter_str=" \ ")
                    day_data["Journal"] = compiled_journal
                    all_days_data.append(day_data)

                archivist_dict["timesheet_df"] = pd.DataFrame.from_dict(all_days_data)
                archivist_dict["html_table"] = archivist_dict["timesheet_df"].to_html(index=False, classes="table-hover table-dark")

    except Exception as e:
        exception_handling_pattern(flash_message="Error creating individualized timesheet tables: ",
                                   thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('timesheet_tables.html', title="Timesheets", form=form, archivist_info_list=archivists)



@timekeeper.route("/timekeeper/admin", methods=['GET', 'POST'])
@login_required
@utilities.roles_required(['ADMIN'])
def choose_employee():
    try:
        form = TimeSheetAdminForm()

        # Get 'active' employee emails to use in dropdown choices
        is_archivist = lambda user: 'ARCHIVIST' in user.roles.split(",")
        employee_emails = [employee.email for employee in UserModel.query.all() if is_archivist(employee) and employee.active]

        # add 'ALL' option to email dropdown
        form.employee_email.choices = ['ALL'] + employee_emails

        if form.validate_on_submit():
            # get selected employee id and use it to redirect to correct timesheet endpoint
            employee_email = form.employee_email.data

            # if user has not selected 'ALL', they have selected an email account...
            if not employee_email == 'ALL':
                employee_id = UserModel.query.filter_by(email=employee_email).first().id
                return flask.redirect(flask.url_for('timekeeper.user_timesheet', employee_id=employee_id))

            else:
                return flask.redirect(flask.url_for('timekeeper.all_timesheets'))


    except Exception as e:
        return exception_handling_pattern(flash_message="Error trying to elicit or process employee email for making a timesheet :",
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

        df = db_query_to_df(query=query)

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
        #TODO default image
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













