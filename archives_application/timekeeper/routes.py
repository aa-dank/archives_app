# archives_application/timekeeper/routes.py

import flask
import matplotlib
matplotlib.use('Agg') # Required to generate plots without a display
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from datetime import datetime, timedelta, time
from flask import current_app
from flask_login import login_required, current_user
from matplotlib import ticker
from sqlalchemy import func

from .forms import TimekeepingForm, TimeSheetForm, TimeKeeperAdminForm
from archives_application import utils
from archives_application.models import UserModel, TimekeeperEventModel, ArchivedFileModel, db

timekeeper = flask.Blueprint('timekeeper', __name__)

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

def get_previous_sunday(start_date: datetime):
    """
    This function returns the previous Sunday from the given date. Returns the same date if the given date is a Sunday.
    """
    # Check if start_date is a Sunday
    if start_date.weekday() == 6:
        return start_date
    else:
        # Calculate the difference to the previous Sunday
        days_to_sunday = start_date.weekday() + 1  # Monday is 0, Sunday is 6
        previous_sunday = start_date - timedelta(days=days_to_sunday)
        return previous_sunday

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

    timesheet_df = utils.FlaskAppUtils.db_query_to_df(query=query)
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
            hours_worked += delta.total_seconds() / 3600

    return hours_worked, clock_ins_have_clock_outs


def compile_journal(date: datetime.date, timecard_df: pd.DataFrame, delimiter_str: str):
    """
    Combines journalcolumn into a single str
    @param date:
    @param timecard_df:
    @param delimiter_str:
    @param delimiter_str:
    @return:
    """
    # Convert the date to a datetime object representing the start of the day
    date_start = datetime.combine(date, time.min)
    date_end = datetime.combine(date, time.max)
    
    # Filter the DataFrame for the specific date
    filtered_df = timecard_df[(timecard_df["datetime"] >= date_start) & (timecard_df["datetime"] <= date_end)]
    
    # Join the 'journal' entries with the delimiter
    compiled_journal = delimiter_str.join([journal for journal in filtered_df["journal"].tolist() if journal])
    
    return compiled_journal


@timekeeper.route("/timekeeper", methods=['GET', 'POST'])
@login_required
@utils.FlaskAppUtils.roles_required(['ADMIN', 'ARCHIVIST'])
def timekeeper_event():
    """Handles clock-in and clock-out events for users.

    This endpoint provides a form for users to clock in and clock out. It records the events in the database
    and updates the user's status accordingly.

    Usage:
        - If the user is not clocked in, the form will display a "Clock In" button.
        - If the user is clocked in, the form will display a journal entry field and "Clock Out" and "Clock Out and Log Out" buttons.
        - The journal entry field allows users to briefly record what they worked on, including details like project number and print requester.

    Args:
        None

    Returns:
        Response: Renders the 'timekeeper.html' template with the form and current clock-in status, redirects to the home page, or redirext to user dashboard.
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
        todays_events_df = utils.FlaskAppUtils.db_query_to_df(todays_events_query)



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
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error when checking if user is clocked in",
            thrown_exception=e,
            app_obj=flask.current_app
        )

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
                    return utils.FlaskAppUtils.web_exception_subroutine(
                        flash_message="Error recording user clock-out event",
                        thrown_exception=e,
                        app_obj=flask.current_app
                    )

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
                    return utils.FlaskAppUtils.web_exception_subroutine(
                        flash_message="Error recording user clock-in event",
                        thrown_exception=e,
                        app_obj=flask.current_app
                    )

    return flask.render_template('timekeeper.html', title='Timekeeper', form=form,  clocked_in=clocked_in,
                                 id=current_user_id)


def generate_user_timesheet_dataframes(user_id, start_date=None, end_date=None, include_weekly = True):
    """
    Creates Dataframes aggregating timekeeper and archiving data for a user between the start and end dates.
    :param user_id: The user id of the user to generate the timesheet for
    :param start_date: The start date of the timesheet range. If None, defaults to 14 days before the current date.
    :param end_date: The end date of the timesheet range. If None, defaults to the current date.
    """
    
    def process_hours_worked_vals(hrs):
        """
        Converts hours worked to string and rounds to 2 decimal places. If hours are np.nan, returns 'TIME ENTRY INCOMPLETE'
        """
        return str(round(hrs, 2)) if hrs != "TIME ENTRY INCOMPLETE" else hrs
    
    query_start_date = start_date
    if not query_start_date:
        query_start_date = (datetime.now() - timedelta(days = 14)) if start_date is None else start_date
        query_start_date = query_start_date.date()
    
    query_end_date = datetime.now().date() if end_date is None else end_date
    
    
    # save original start date before manipulation
    original_start_date = query_start_date
    query_start_date = get_previous_sunday(original_start_date)

    # query to get all timekeeper events for the user between the start and end dates
    query = TimekeeperEventModel.query.filter(TimekeeperEventModel.user_id == user_id,
                                                TimekeeperEventModel.datetime >= query_start_date,
                                                TimekeeperEventModel.datetime <= query_end_date)
    timesheet_df = utils.FlaskAppUtils.db_query_to_df(query=query)

    # issues with sending commands to DB can result in pd.read_sql returning None instead of Dataframe
    assert type(timesheet_df) == type(pd.DataFrame()), "pd.read_sql did not return a Dataframe."

    if timesheet_df.shape[0] == 0:
        return pd.DataFrame(), pd.DataFrame()

    # Create a list of dictionaries, where each dictionary is the aggregated data for that day
    all_days_data = []
    for range_date in daterange(start_date=query_start_date, end_date=query_end_date):
        day_data = {"Date":range_date.strftime('%Y-%m-%d %A')}

        # calculate hours and/or determine if entering them is incomplete
        hours, timesheet_complete = hours_worked_in_day(range_date, user_id)
        if not timesheet_complete:
            day_data["Hours Worked"] = np.nan
        else:
            # round hours and convert to string
            day_data["Hours Worked"] = hours

        # get the daily archiving metrics if applicable
        if 'ARCHIVIST' in current_user.roles or 'ADMIN' in current_user.roles:
            
            # This query gets all archived files for the user on the given day
            archived_files_query = ArchivedFileModel.query\
                .filter(ArchivedFileModel.archivist_id == user_id,
                        ArchivedFileModel.date_archived >= range_date,
                        ArchivedFileModel.date_archived <= range_date + timedelta(days=1))
            
            arched_files_df = utils.FlaskAppUtils.db_query_to_df(query=archived_files_query)
            day_data["Archived Files"] = arched_files_df.shape[0]
            day_data["Archived Megabytes"] = 0
            if not arched_files_df.empty:
                day_data["Archived Megabytes"] = (arched_files_df["file_size"].sum()/1000000).round(2)

        # Mush all journal entries together into a single journal entry
        compiled_journal = compile_journal(range_date, timesheet_df, " \ ")
        day_data["Journal"] = compiled_journal
        all_days_data.append(day_data)
    
    # daily data converted to dataframe    
    all_days_df = pd.DataFrame.from_dict(all_days_data)
    all_days_df['Date'] = pd.to_datetime(all_days_df['Date'])
    all_days_df.set_index('Date', drop=True, inplace=True)

    # create weekly dataframe from daily data
    weekly_summary = pd.DataFrame()
    if include_weekly:
        weekly_summary = all_days_df.resample('W')\
            .sum(numeric_only=True)\
            .reset_index()
        weekly_summary["Hours Worked"] = weekly_summary["Hours Worked"].map(process_hours_worked_vals)
        
        # date column adjusted to begining of the week
        weekly_summary['Date'] = weekly_summary['Date'].apply(lambda x: x - timedelta(days=7))

        #remove weeks not in the original date range
        weekly_summary = weekly_summary[weekly_summary['Date'] >= pd.to_datetime(query_start_date)]
        weekly_summary = weekly_summary[weekly_summary['Date'] <= pd.to_datetime(query_end_date)]
        
        # Rename 'Date' Column to 'Week'
        weekly_summary.rename(columns={'Date': 'Week'}, inplace=True)

    # reduce daily data to only include days in the original date range
    all_days_df = all_days_df[all_days_df.index >= pd.to_datetime(original_start_date)]
    # replace 'hours worked' np.nan with 'TIME ENTRY INCOMPLETE'
    all_days_df["Hours Worked"] = all_days_df["Hours Worked"]\
        .apply(lambda x: "TIME ENTRY INCOMPLETE" if np.isnan(x) else x)
    all_days_df["Hours Worked"] = all_days_df["Hours Worked"].map(process_hours_worked_vals)

    # Move index back to 'Date' column
    all_days_df.reset_index(inplace=True)

    # Create strings from dates
    all_days_df['Date'] = all_days_df['Date'].dt.strftime('%Y-%m-%d %A')

    return all_days_df, weekly_summary


@timekeeper.route("/timekeeper/<employee_id>", methods=['GET', 'POST'])
@login_required
@utils.FlaskAppUtils.roles_required(['ADMIN', 'ARCHIVIST'])
def user_timesheet(employee_id):
    """Displays the timesheet for a specific user.

    This endpoint provides a form for selecting the date range for the timesheet and displays the timesheet data
    for the specified user within the selected date range.

    Args:
        employee_id (int): The ID of the employee whose timesheet is to be displayed.

    Returns:
        Response: Renders the 'timesheet_tables.html' template with the timesheet data for the specified user.
    """
    form = TimeSheetForm()
    timesheet_df = None

    # get user information from the database
    try:
        employee = UserModel.query.filter(UserModel.id == employee_id).one_or_none()
        archivist_dict = {'email': employee.email, 'id': employee.id}

    except Exception as e:
        utils.FlaskAppUtils.web_exception_subroutine(
            flash_message=f"Error trying to get user info from the database for user id {employee_id}",
            thrown_exception=e,
            app_obj=flask.current_app
        )

    try:
        # if user has submitted the form, get the start and end dates from the form
        if form.validate_on_submit():
            user_start_date = form.timesheet_begin.data
            user_end_date = form.timesheet_end.data
            timesheet_df, weekly_summary_df = generate_user_timesheet_dataframes(employee_id, user_start_date, user_end_date)

        else:
            timesheet_df, weekly_summary_df = generate_user_timesheet_dataframes(employee_id)    
        
        # if there are no timekeeper events, flash a message
        if timesheet_df is None or timesheet_df.shape[0] == 0:
                flask.flash("No timekeeper events found for the selected date range.", 'info')

    except Exception as e:
        utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error creating table of hours worked: ",
            thrown_exception=e,
            app_obj=flask.current_app
        )

    archivist_dict["daily_html_table"] = timesheet_df.to_html(index=False, classes="table-hover table-dark")
    archivist_dict["weekly_html_table"] = weekly_summary_df.to_html(index=False, classes="table-hover table-dark")

    return flask.render_template('timesheet_tables.html', title="Timesheet", form=form, archivist_info_list=[archivist_dict])


@timekeeper.route("/timekeeper/all", methods=['GET', 'POST'])
@login_required
@utils.FlaskAppUtils.roles_required(['ADMIN', 'ARCHIVIST'])
def all_timesheets():
    """Displays all timesheets for archivists.

    This endpoint provides a form for selecting the date range for the timesheets and displays the timesheet data
    for all active archivists within the selected date range.

    Args:
        None

    Form data:
        timesheet_begin (date): Start date of timesheet range to display.
        timesheet_end (date): End date of timesheet range to display.

    Returns:
        Response: Renders the 'timesheet_tables.html' template with the following data:
            - form: the TimeSheetForm object.
            - title: "Timesheets".
            - archivist_info_list: A list of dictionaries containing information for each archivist, including:
                - email: email of the archivist.
                - id: id of the archivist.
                - timesheet_df: a dataframe with the timesheet data.
                - html_table: the timesheet data in HTML format.
    """

    form = TimeSheetForm()
    try:
        # Get 'active' employee emails
        archivists = [{'email': employee.email, 'id': employee.id} for employee in
                      UserModel.query.filter(UserModel.roles.contains('ARCHIVIST'), UserModel.active.is_(True))]

    except Exception as e:
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error retrieving active archivists from database:",
            thrown_exception=e,
            app_obj=flask.current_app
        )

    try:
        timesheet_df = pd.DataFrame()
        start_date = None
        end_date = None
        if form.validate_on_submit():
            start_date = form.timesheet_begin.data
            end_date = form.timesheet_end.data
            start_date = datetime(year=start_date.year, month=start_date.month, day=start_date.day)
            end_date = datetime(year=end_date.year, month=end_date.month, day=end_date.day)
        
        # iterate over archivists to create individualized timesheet tables
        for archivist_dict in archivists:
            archivist_dict["timesheet_df"], archivist_dict["weekly_summary_df"] = generate_user_timesheet_dataframes(archivist_dict["id"], start_date, end_date)
            archivist_dict["daily_html_table"] = archivist_dict["timesheet_df"].to_html(index=False, classes="table-hover table-dark")
            archivist_dict["weekly_html_table"] = archivist_dict["weekly_summary_df"].to_html(index=False, classes="table-hover table-dark")

    except Exception as e:
        utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error creating individualized timesheet tables: ",
            thrown_exception=e,
            app_obj=flask.current_app
        )

    return flask.render_template('timesheet_tables.html', title="Timesheets", form=form, archivist_info_list=archivists)


@timekeeper.route("/timekeeper/admin", methods=['GET', 'POST'])
@login_required
@utils.FlaskAppUtils.roles_required(['ADMIN'])
def timekeeper_admin_interface():
    """
    Endpoint to display a form for selecting either:
    1. An employee to view their timesheet, or
    2. A date/time to view who was working at that time
    """
    try:
        form = TimeKeeperAdminForm()

        # Get 'active' employee emails to use in dropdown choices
        is_archivist = lambda user: 'ARCHIVIST' in user.roles.split(",")
        employee_emails = [employee.email for employee in UserModel.query.all() if is_archivist(employee) and employee.active]

        # add 'ALL' option to email dropdown
        form.employee_email.choices = ['ALL'] + employee_emails
        
        if form.validate_on_submit():
            operation = form.operation.data
            
            if operation == 'employee_timesheet':
                # get selected employee id and use it to redirect to correct timesheet endpoint
                employee_email = form.employee_email.data

                # if user has not selected 'ALL', they have selected an email account...
                if not employee_email == 'ALL':
                    employee_id = UserModel.query.filter_by(email=employee_email).first().id
                    return flask.redirect(flask.url_for('timekeeper.user_timesheet', employee_id=employee_id))
                else:
                    return flask.redirect(flask.url_for('timekeeper.all_timesheets'))
            
            elif operation == 'who_work_when':
                # Redirect to who_work_when with the selected date and time
                selected_date = form.selected_date.data
                selected_time = form.selected_time.data
                
                # Format the date as YYYY-MM-DD
                date_str = selected_date.strftime('%Y-%m-%d')
                
                if selected_time:
                    # Format the time as HH:MM
                    time_str = selected_time.strftime('%H:%M')
                    return flask.redirect(flask.url_for('timekeeper.who_work_when', 
                                                      date=date_str, 
                                                      time=time_str))
                else:
                    # If no time specified, just use the date
                    return flask.redirect(flask.url_for('timekeeper.who_work_when', 
                                                      date=date_str))

    except Exception as e:
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error processing timekeeper admin form:",
            thrown_exception=e,
            app_obj=flask.current_app
        )

    return flask.render_template('timekeeper_admin.html', form=form)

@timekeeper.route("/timekeeper/who_work_when", methods=['GET', 'POST'])
@login_required
@utils.FlaskAppUtils.roles_required(['ADMIN'])
def who_work_when():
    """
    Shows which employees were working during a specified date/time.
    
    This endpoint retrieves timekeeper events for the specified date (and optionally time)
    and displays which employees were clocked in, their working intervals, and their journal entries.
    
    Args:
        None
        
    URL Parameters:
        date (str): The date to check, in the format 'YYYY-MM-DD'.
        time (str, optional): The specific time to check, in the format 'HH:MM'.
        
    Returns:
        Response: Renders the 'who_work_when.html' template with a table showing which employees
        were working during the specified date/time.
    """
    try:
        # Get the date and time from URL parameters
        date_str = flask.request.args.get('date')
        time_str = flask.request.args.get('time')
        
        if not date_str:
            flask.flash("No date specified.", 'warning')
            return flask.redirect(flask.url_for('timekeeper.timekeeper_admin_interface'))
        
        # Parse date
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Start and end of the selected date
        day_start = datetime.combine(selected_date, time.min)
        day_end = datetime.combine(selected_date, time.max)
        
        # If a specific time is specified, adjust for checking who was working at that time
        specific_time = None
        if time_str:
            hour, minute = map(int, time_str.split(':'))
            specific_time = datetime.combine(selected_date, time(hour, minute))
        
        # Get all timekeeper events for the selected date
        events_query = TimekeeperEventModel.query.filter(
            TimekeeperEventModel.datetime >= day_start,
            TimekeeperEventModel.datetime <= day_end
        ).order_by(TimekeeperEventModel.user_id, TimekeeperEventModel.datetime)
        
        events_df = utils.FlaskAppUtils.db_query_to_df(query=events_query)
        
        if events_df.empty:
            flask.flash(f"No timekeeper events found for {date_str}.", 'info')
            return flask.redirect(flask.url_for('timekeeper.timekeeper_admin_interface'))
        
        # Get all users who have events on this day
        user_ids = [int(user_id) for user_id in events_df['user_id'].unique()]
        users = UserModel.query.filter(UserModel.id.in_(user_ids)).all()
        user_map = {user.id: user.email for user in users}
        
        # Initialize the results list
        work_data = []
        
        # Process each user's events
        for user_id in user_ids:
            user_events = events_df[events_df['user_id'] == user_id].sort_values('datetime')
            
            # Ensure events start with clock-in and end with clock-out
            if user_events.empty or not user_events.iloc[0]['clock_in_event']:
                # Skip users with incomplete timekeeper events
                continue
            
            if user_events.iloc[-1]['clock_in_event']:
                # If the last event is a clock-in, we assume they're still working
                # Add an artificial clock-out at the current time or end of day
                current_time = datetime.now()
                if current_time.date() == selected_date:
                    # If selected date is today, use current time
                    user_events = pd.concat([user_events, pd.DataFrame([{
                        'user_id': user_id,
                        'datetime': current_time,
                        'clock_in_event': False,
                        'journal': "Still working"
                    }])])
                else:
                    # If selected date is in the past, use end of day
                    user_events = pd.concat([user_events, pd.DataFrame([{
                        'user_id': user_id,
                        'datetime': day_end,
                        'clock_in_event': False,
                        'journal': "Clock-out time unknown"
                    }])])
            
            # Calculate clocked-in intervals
            intervals = []
            was_working_during_time = False
            
            # Process each pair of clock-in/clock-out events
            for i in range(0, len(user_events)-1, 2):
                if i+1 >= len(user_events):
                    break
                    
                clock_in_time = user_events.iloc[i]['datetime']
                clock_out_time = user_events.iloc[i+1]['datetime']
                
                # Format the interval as a string
                interval = f"{clock_in_time.strftime('%H:%M')} - {clock_out_time.strftime('%H:%M')}"
                intervals.append(interval)
                
                # Check if the user was working during the specified time
                if specific_time and clock_in_time <= specific_time <= clock_out_time:
                    was_working_during_time = True
            
            # Skip this user if specific time was provided and they weren't working then
            if specific_time and not was_working_during_time:
                continue
                
            # Compile all journal entries for the user on this day
            journal_entries = user_events[~user_events['clock_in_event'] & (user_events['journal'] != "")]
            compiled_journal = " / ".join([entry for entry in journal_entries['journal'].tolist() if entry])
            
            # Add user data to results
            work_data.append({
                'User': user_map.get(user_id, f"Unknown User (ID: {user_id})"),
                'Working Intervals': ", ".join(intervals),
                'Journal': compiled_journal
            })
        
        # Convert to dataframe for easy HTML rendering
        work_df = pd.DataFrame(work_data)
        
        # Generate HTML table
        if not work_df.empty:
            html_table = work_df.to_html(index=False, classes="table-hover table-dark")
        else:
            html_table = "<p>No employees were working during the specified time.</p>"
            
        page_title = f"Employees Working on {date_str}"
        if time_str:
            page_title += f" at {time_str}"
            
        return flask.render_template('who_work_when.html', 
                                    title=page_title,
                                    date=date_str,
                                    time=time_str,
                                    html_table=html_table)
                                    
    except Exception as e:
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error retrieving working employees:",
            thrown_exception=e,
            app_obj=flask.current_app
        )


@timekeeper.route("/archiving_dashboard/<archiver_id>", methods=['GET', 'POST'])
@login_required
@utils.FlaskAppUtils.roles_required(['ADMIN', 'ARCHIVIST'])
def archiving_dashboard(archiver_id):
    """Displays archiving metrics for a specific archivist.

    This endpoint provides a form for selecting the date range and rolling average window for the metrics.
    It displays the total number of files archived, the total data quantity archived, and generates charts
    showing the archiving activity over the selected date range.

    Args:
        archiver_id (int): The ID of the archivist whose metrics are to be displayed.

    Form data:
        timesheet_begin (date): Start date of the date range for the metrics.
        timesheet_end (date): End date of the date range for the metrics.
        rolling_avg_window (int): Number of days to use for the rolling average in the charts.

    Usage:
        - Users can select the start and end dates for the date range they want to analyze.
        - Users can specify the rolling average window to smooth out the data in the charts.
        - The endpoint displays the total number of files archived and the total data quantity archived.
        - The endpoint generates and displays charts showing the archiving activity over the selected date range.

    Returns:
        Response: Renders the 'archivist_dashboard.html' template with the following data:
            - archivist_name: The name of the archivist.
            - archivist_files_count: The total number of files archived by the archivist.
            - archivist_data_quantity: The total data quantity archived by the archivist.
            - total_plot: The path to the generated chart showing the total archiving activity.
            - archivist_plot: The path to the generated chart showing the archivist's archiving activity.
            - form: The TimeSheetForm object for selecting the date range and rolling average window.
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
        _, ax1 = plt.subplots(figsize=(30,10))
        
        bar_colors = ["#007988", "#fdc700"] 
        line_colors = ["#003c6c", "#f29813"]    
        
        # Create the plots
        sns.barplot(data=bars_df, x='Date', y='Files', hue='measure_type', ax=ax1, palette=bar_colors)
        sns.pointplot(data=lines_df, x='Date', y='Files', hue='measure_type', ax=ax1, palette=line_colors, linestyles='--')
        
        # Handle x-axis ticks and labels
        x_ticks = np.arange(len(bars_df['Date'].unique()))
        ax1.set_xticks(x_ticks)
        ax1.set_xticklabels(bars_df['Date'].unique(), rotation=45)
        
        # Handle y-axis ticks
        ax1.set_yticks(file_count_ticks)
        ax1.set_ylim(min(file_count_ticks), max(file_count_ticks))
        ax1.legend_.set_title('')
        
        # Handle secondary y-axis
        ax2 = ax1.twinx()
        ax2.set_yticks(mb_ticks)
        ax2.set_ylim(min(mb_ticks), max(mb_ticks))
        ax2.grid(False)
        ax2.set_ylabel('MB Archived')
        
        # Set title
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
        utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error checking user roles:",
            thrown_exception=e,
            app_obj=current_app
        )

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
        df = utils.FlaskAppUtils.db_query_to_df(query = query)
        archivist_df = df.query(f'archivist_id == {archiver_id}')
        date_range = pd.date_range(start=query_start_date, end=query_end_date)
        if df.shape[0] != 0:
            collective_bars_df, collective_lines_df, _, collective_mb_ticks, collective_files_ticks = create_plot_dataframes_and_ticks(input_df=df,
                                                                                                                                       date_range=date_range,
                                                                                                                                       rolling_avg_days=rolling_avg_window)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            collective_filename = f"collective_metrics_{timestamp}.png"
            collective_chart_path = utils.FlaskAppUtils.create_temp_filepath(collective_filename)
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
            archiver_chart_path = utils.FlaskAppUtils.create_temp_filepath(archiver_filename)
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

        start_date_str = (query_start_date + timedelta(days=rolling_avg_window)).strftime(current_app.config.get('DEFAULT_DATETIME_FORMAT')[:-10])
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
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message=m,
            thrown_exception=e,
            app_obj=current_app
        )

