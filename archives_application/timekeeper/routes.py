import os
import sys
from datetime import datetime, timedelta

import flask
import flask_sqlalchemy
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from typing import List
from flask import current_app
from flask_login import login_required, current_user
from matplotlib import ticker
from sqlalchemy import and_, func


from .forms import TimekeepingForm, TimeSheetForm, TimeSheetAdminForm
from archives_application import utils
from archives_application.models import UserModel, TimekeeperEventModel, ArchivedFileModel, db

timekeeper = flask.Blueprint('timekeeper', __name__)


def web_exception_subroutine(flash_message, thrown_exception, app_obj):
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
    
    # Remove the sqlalchemy state column
    sa = '_sa_instance_state'
    if sa in df.columns:
        df.drop(columns=[sa], inplace=True)
    return df


def temp_file_url(filename: str): 
    """
    Pattern for getting the url for a temp file which has already been saved to the server.
    """
    return flask.url_for(r"static", filename="temp_files/" + filename)


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
        for _, row in time_in_events_df.iterrows():
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
@utils.roles_required(['ADMIN', 'ARCHIVIST'])
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

        #TODO query by datetime: todays_events_query2 = TimekeeperEventModel.query.filter(user_id=1, datetime=datetime.now().date())
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
        return web_exception_subroutine(flash_message="Error when checking if user is clocked in",
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
                    return web_exception_subroutine(flash_message="Error recording user clock-out event",
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
                    if 'ARCHIVIST' in current_user.roles:
                        return flask.redirect(flask.url_for('timekeeper.archiving_dashboard', archiver_id=current_user_id))
                    
                    return flask.redirect(flask.url_for('main.home'))
                except Exception as e:
                    return web_exception_subroutine(flash_message="Error recording user clock-in event",
                                                      thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('timekeeper.html', title='Timekeeper', form=form,  clocked_in=clocked_in,
                                 id=current_user_id)


@timekeeper.route("/timekeeper/<employee_id>", methods=['GET', 'POST'])
@login_required
@utils.roles_required(['ADMIN', 'ARCHIVIST'])
def user_timesheet(employee_id):

    form = TimeSheetForm()
    timesheet_df = None

    # get user information from the database
    try:
        employee = UserModel.query.filter(UserModel.id == employee_id).one_or_none()
        archivist_dict = {'email': employee.email, 'id': employee.id}

    except Exception as e:
        web_exception_subroutine(flash_message=f"Error trying to get user info from the database for user id {employee_id}",
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
        web_exception_subroutine(flash_message="Error getting user timekeeper events from database: ",
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

            # get the daily archiving metrics if applicable
            if 'ARCHIVIST' in current_user.roles or 'ADMIN' in current_user.roles:
                archived_files_query = ArchivedFileModel.query.filter(ArchivedFileModel.archivist_id == employee_id,
                                                                      ArchivedFileModel.date_archived >= range_date,
                                                                      ArchivedFileModel.date_archived <= range_date + timedelta(days=1))
                arched_files_df = db_query_to_df(query=archived_files_query)
                day_data["Archived Files"] = arched_files_df.shape[0]
                day_data["Archived Megabytes"] = 0
                if not arched_files_df.empty:
                    day_data["Archived Megabytes"] = (arched_files_df["file_size"].sum()/1000000).round(2)

            # Mush all journal entries together into a single journal entry
            compiled_journal = compile_journal(range_date, timesheet_df, " \ ")
            day_data["Journal"] = compiled_journal
            all_days_data.append(day_data)

    except Exception as e:
        web_exception_subroutine(flash_message="Error creating table of hours worked: ",
                                   thrown_exception=e, app_obj=flask.current_app)

    aggregate_hours_df = pd.DataFrame.from_dict(all_days_data)
    archivist_dict["html_table"] = aggregate_hours_df.to_html(index=False, classes="table-hover table-dark")

    return flask.render_template('timesheet_tables.html', title="Timesheet", form=form, archivist_info_list=[archivist_dict])


@timekeeper.route("/timekeeper/all", methods=['GET', 'POST'])
@login_required
@utils.roles_required(['ADMIN', 'ARCHIVIST'])
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
        return web_exception_subroutine(
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
        web_exception_subroutine(flash_message="Error creating dataframe for all archivists: ",
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
                    
                    # get the daily archiving metrics if applicable
                    if 'ARCHIVIST' in current_user.roles or 'ADMIN' in current_user.roles:
                        archived_files_query = ArchivedFileModel.query.filter(ArchivedFileModel.archivist_id == archivist_dict['id'],
                                                                ArchivedFileModel.date_archived >= range_date,
                                                                ArchivedFileModel.date_archived <= range_date + timedelta(days=1))
                        arched_files_df = db_query_to_df(query=archived_files_query)
                        day_data["Archived Files"] = arched_files_df.shape[0]
                        day_data["Archived Megabytes"] = 0
                        if not arched_files_df.empty:
                            day_data["Archived Megabytes"] = (arched_files_df["file_size"].sum()/1000000).round(2)

                    # Mush all journal entries together into a single journal entry
                    compiled_journal = compile_journal(date=range_date,
                                                       timecard_df=user_timesheet_df,
                                                       delimiter_str=" \ ")
                    day_data["Journal"] = compiled_journal
                    all_days_data.append(day_data)
                    
                archivist_dict["timesheet_df"] = pd.DataFrame.from_dict(all_days_data)
                archivist_dict["html_table"] = archivist_dict["timesheet_df"].to_html(index=False, classes="table-hover table-dark")

    except Exception as e:
        web_exception_subroutine(flash_message="Error creating individualized timesheet tables: ",
                                   thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('timesheet_tables.html', title="Timesheets", form=form, archivist_info_list=archivists)


@timekeeper.route("/timekeeper/admin", methods=['GET', 'POST'])
@login_required
@utils.roles_required(['ADMIN'])
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
        return web_exception_subroutine(flash_message="Error trying to elicit or process employee email for making a timesheet :",
                                          thrown_exception=e, app_obj=flask.current_app)
    return flask.render_template('timekeeper_admin.html', form=form)


@timekeeper.route("/archiving_dashboard/<archiver_id>", methods=['GET', 'POST'])
@login_required
@utils.roles_required(['ADMIN', 'ARCHIVIST'])
def archiving_dashboard(archiver_id):
    """
    Endpoint to display archiving metrics for a specific archivist.
    """
    def bytes_to_mb(bytes):
        return round((bytes/(1024**2)), 3)
    

    def create_plot_dataframes_and_ticks(input_df: pd.DataFrame, date_range: pd.core.indexes.datetimes.DatetimeIndex, rolling_avg_days: int):
        """
        Generates the dataframes used to create the metrics plot from a query dataframe.
        :param input_df: dataframe of archived files. Should already be filtered to only include files from desired dates.
        :param date_range: pandas DatetimeIndex of dates to include in the plot.
        :param rolling_avg_days: number of days to use for the rolling average.
        :return: tuple of dataframes. First element is the dataframe used to create the bars in the plot. Second element
        is the dataframe used to create the lines in the plot. Third element is the max value of the data used in the plot.
        """
        # number of ticks to use for our tick labels and tick builder for later use.
        number_of_ticks = 6
        tick_maker = ticker.MaxNLocator(nbins=number_of_ticks)

        # convert the timestamp to a date
        timestamp_date = lambda ts: ts.date()
        input_df["date_archived"] = input_df["date_archived"].map(timestamp_date)
        
        #
        date_groups = input_df.groupby("date_archived")
        sizes = date_groups.size()
        sizes.name = "total_files"
        agg_df = pd.concat([date_groups.aggregate({"file_size": "sum"}), sizes], axis=1, join="outer")

        # Use the date range to fill in missing dates
        date_range = date_range[date_range.weekday < 5] # filter out weekends
        date_range = date_range.date # convert to date
        date_range_df = pd.DataFrame(index=date_range)
        agg_df = pd.concat([date_range_df, agg_df], axis=1, join="outer")
        agg_df = agg_df.fillna(0)

        # calculate the rolling averages
        agg_df["size_rolling_avg"] = agg_df["file_size"].rolling(window=rolling_avg_days, min_periods=rolling_avg_days).mean()
        agg_df["files_rolling_avg"] = agg_df["total_files"].rolling(rolling_avg_days).mean()
        agg_df = agg_df.dropna(subset=["size_rolling_avg", "files_rolling_avg"])
        
        # Create min-max normalized columns for file_size and size_rolling_avg
        max_mb = max((agg_df["file_size"].max(), agg_df["size_rolling_avg"].max()))
        
        mb_ticks = tick_maker.tick_values(0, bytes_to_mb(max_mb))
        norm_files_size = lambda f_size: f_size / (max(mb_ticks) * (1024**2)) 
        agg_df["file_size_norm"] = agg_df["file_size"].map(norm_files_size)
        agg_df["size_rolling_avg_norm"] = agg_df["size_rolling_avg"].map(norm_files_size)

        # use normalized columns to create equivalent columns scaled to be measured in "number of files"
        # units used in the total_files column
        max_files = max(agg_df["total_files"].max(), agg_df["files_rolling_avg"].max())
        files_ticks = tick_maker.tick_values(0, max_files)
        norm_to_files = lambda norm_score: norm_score * max(files_ticks)
        agg_df["file_size_as_files"] = agg_df["file_size_norm"].map(norm_to_files)
        agg_df["size_rolling_avg_as_files"] = agg_df["size_rolling_avg_norm"].map(norm_to_files)

        bars_df = pd.melt(agg_df.reset_index(),
                          id_vars='index',
                          value_vars=['total_files','file_size_as_files'],
                          var_name='measure_type',
                          value_name='files_count')
        bars_df = bars_df.rename(columns={'index': 'Date', 'files_count': 'Files'})
        bars_df['measure_type'] = bars_df['measure_type'].replace({'total_files': '# of Files',
                                                                   'file_size_as_files': 'MB of Files'})

        lines_df = pd.melt(agg_df.reset_index(),
                           id_vars='index',
                           value_vars=['files_rolling_avg', 'size_rolling_avg_as_files'],
                           var_name='measure_type',
                           value_name='files_count')
        lines_df = lines_df.rename(columns={'index': 'Date', 'files_count': 'Files'})
        lines_df['measure_type'] = lines_df['measure_type'].replace({'files_rolling_avg': f'{rolling_avg_days} Day Rolling Average File Count',
                                                                     'size_rolling_avg_as_files': f'{rolling_avg_days} Day Rolling Average Data Volume (MB)'})
        max_data = max(agg_df["file_size"].max(), agg_df["size_rolling_avg"].max())        
        return bars_df, lines_df, max_data, mb_ticks, files_ticks
    

    def metrics_plot_file(lines_df: pd.DataFrame, bars_df: pd.DataFrame, mb_ticks, file_count_ticks, file_destination: str, archiver_name: str = None):
        
        plt.clf()
        sns.set_theme(style="darkgrid")
        fig, ax1 = plt.subplots(figsize=(30,10))
        bar_colors = ["#007988", "#fdc700"] 
        line_colors = ["#003c6c", "#f29813"]    
        sns.barplot(data=bars_df, x='Date', y='Files', hue='measure_type', ax=ax1, palette=bar_colors)
        sns.pointplot(data=lines_df, x='Date', y='Files', hue='measure_type', ax=ax1, palette=line_colors, linestyles='--')
        ax1.set_xticklabels(labels=ax1.get_xticklabels(), rotation=45)
        ax1.set_yticks(file_count_ticks)
        ax1.set_ylim(min(file_count_ticks), max(file_count_ticks))
        ax1.legend_.set_title('')
        
        ax2 = ax1.twinx()
        ax2.set_yticks(mb_ticks)
        ax2.set_ylim(min(mb_ticks), max(mb_ticks))
        ax2.grid(False)
        ax2.set_ylabel('MB Archived')
        title = f'Archiving Metrics for {archiver_name}' if archiver_name else 'Total Archiving Metrics'
        plt.title(title, fontsize=20)
        plt.savefig(file_destination)
        return file_destination


    # archivists should only be able to view their own metrics. Get unauthorized if they try to view another's
    try:
        if 'ADMIN' not in current_user.roles:
            current_user_id = UserModel.query.filter_by(email=current_user.email).first().id
            if str(current_user_id) != str(archiver_id):
                return flask.Response("Unauthorized", status=401)

    except Exception as e:
        web_exception_subroutine(flash_message="Error checking user roles:",
                                 thrown_exception=e,
                                 app_obj=current_app)    
    
    try:
        default_chart_window = 30 # measured in days
        rolling_avg_window = 10 # measured in days
        query_start_date = datetime.now() - timedelta(days = default_chart_window)
        query_start_date = query_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        query_end_date = datetime.now()
        query_end_date = query_end_date.replace(hour=23, minute=0, second=0, microsecond=0)
        collective_plot_url, archiver_plot_url, archivist_total_data, archivist_total_files = None, None, None, None
        archiver_name = UserModel.query.filter_by(id=archiver_id).first().first_name
        form = TimeSheetForm()

        if form.validate_on_submit():
            user_start_date = form.timesheet_begin.data
            user_end_date = form.timesheet_end.data
            query_start_date = datetime(year=user_start_date.year, month=user_start_date.month, day=user_start_date.day)
            query_end_date = datetime(year=user_end_date.year, month=user_end_date.month, day=user_end_date.day)
            if form.rolling_avg_window.data:
                rolling_avg_window = int(form.rolling_avg_window.data)
        
        # We start the query with a date that is the rolling_avg_window days before the chosen start date,
        # so that the rolling average of the first included day can be calculated
        query_start_date = query_start_date - timedelta(days=rolling_avg_window)
        query = db.session.query(ArchivedFileModel)\
            .filter(ArchivedFileModel.date_archived.between(query_start_date, query_end_date))
        df = utils.db_query_to_df(query= query)
        archivist_df = df.query(f'archivist_id == {archiver_id}')
        date_range = pd.date_range(start=query_start_date, end=query_end_date)
        if df.shape[0] != 0:
            collective_bars_df, collective_lines_df, _, collective_mb_ticks, collective_files_ticks = create_plot_dataframes_and_ticks(input_df=df,
                                                                                                                                       date_range=date_range,
                                                                                                                                       rolling_avg_days=rolling_avg_window)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            collective_filename = f"collective_metrics_{timestamp}.png"
            collective_chart_path = utils.create_temp_file_path(collective_filename)
            collective_chart_path = metrics_plot_file(lines_df=collective_lines_df,
                                                      bars_df=collective_bars_df,
                                                      mb_ticks=collective_mb_ticks,
                                                      file_count_ticks=collective_files_ticks,                                                      
                                                      file_destination=collective_chart_path)
            
            collective_plot_url = temp_file_url(collective_filename)
            # Record image path to session so it can be deleted upon logout
            if not flask.session[current_user.email].get('temporary files'):
                flask.session[current_user.email]['temporary files'] = []
            
            flask.session[current_user.email]['temporary files'].append(collective_chart_path)


        # if the archivist has no data for the selected date range, we don't bother with their individual chart
        if archivist_df.shape[0] != 0:
            archivist_bars_df, archivist_lines_df, _, archvivist_mb_ticks, archivist_files_ticks = create_plot_dataframes_and_ticks(input_df=archivist_df,
                                                                                                        date_range=date_range,
                                                                                                        rolling_avg_days=rolling_avg_window)
            archiver_filename = f"{archiver_name}_metrics_{timestamp}.png"
            archiver_chart_path = utils.create_temp_file_path(archiver_filename)
            archiver_chart_path = metrics_plot_file(lines_df=archivist_lines_df,
                                                    bars_df=archivist_bars_df,
                                                    mb_ticks=archvivist_mb_ticks,
                                                    file_count_ticks=archivist_files_ticks,
                                                    file_destination=archiver_chart_path,
                                                    archiver_name=archiver_name)
            archiver_plot_url = temp_file_url(archiver_filename)
            # Record image path to session so it can be deleted upon logout
            flask.session[current_user.email]['temporary files'].append(archiver_chart_path)

        # For the given archivist, retrieve the total count odf archived files 
        # and total quantity of data archived.
        archivist_total_files = db.session.query(ArchivedFileModel)\
            .filter(ArchivedFileModel.archivist_id == archiver_id)\
            .count()

        archivist_total_data = db.session.query(func.sum(ArchivedFileModel.file_size))\
            .filter(ArchivedFileModel.archivist_id == archiver_id)\
            .scalar()
        
        # convert archivist_total_data bytes to gigabytes and round to 3 decimal places
        archivist_total_data = round((archivist_total_data / (1024**3)), 3)

        start_date_str = query_start_date.strftime(current_app.config.get('DEFAULT_DATETIME_FORMAT')[:-10])
        end_date_str = query_end_date.strftime(current_app.config.get('DEFAULT_DATETIME_FORMAT')[:-10])

        return flask.render_template('archivist_dashboard.html',
                                     form=form,
                                     archivist_name=archiver_name,
                                     archivist_files_count=archivist_total_files,
                                     archivist_data_quantity=archivist_total_data,
                                     plot_start_date=start_date_str,
                                     plot_end_date=end_date_str,
                                     total_plot= collective_plot_url,
                                     archivist_plot= archiver_plot_url)

    except Exception as e:
        m = "Error creating or rendering dashboard:\n"
        return web_exception_subroutine(flash_message=m, thrown_exception=e, app_obj=current_app)
