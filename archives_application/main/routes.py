import flask
import json
import logging
from . import forms
from .. utilities import roles_required

main = flask.Blueprint('main', __name__)


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


@main.route("/")
@main.route("/home")
def home():
    return flask.render_template('home.html')


@main.route("/admin")
def main_admin():
    #TODO add page of links to admin pages
    pass


@main.route("/admin/config", methods=['GET', 'POST'])
@roles_required(['ADMIN'])
def change_config_settings():



    config_dict = {}
    config_filepath = flask.current_app.config.get('CONFIG_JSON_PATH')
    form = None
    try:
        with open(config_filepath) as config_json_file:
            config_dict = json.load(config_json_file)
        dynamic_form_class = forms.form_factory(fields_dict=config_dict, form_class_name="ConfigChange")
        form = dynamic_form_class()
    except Exception as e:
        return exception_handling_pattern(flash_message='An error occurred opening the config file and creating a form from it:',
                                   thrown_exception=e, app_obj=flask.current_app)

    if form.validate_on_submit():
        try:
            # for each key in the config, we replace it with the value from the form if a value was entered in the form
            for k in list(config_dict.keys()):
                if getattr(form, k).data:
                    new_val = getattr(form, k).data
                    # if the value for this setting is a list, process input string into a list
                    if type(config_dict[k]['VALUE']) == type([]):

                        # To process into a list we remove
                        new_val = [x.strip() for x in new_val.split(",") if x != '']
                    config_dict[k]['VALUE'] = new_val

            with open(config_filepath, 'w') as config_file:
                json.dump(config_dict, config_file)

            flask.flash("Values entered were stored in the config file.", 'success')
            return flask.redirect(flask.url_for('main.home'))

        except Exception as e:
            return exception_handling_pattern(flash_message="Error processing form responses into json config file: ",
                                       thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('change_config_settings.html', title='Change Config File', form=form, settings_dict=config_dict)


@main.route("/test_error", methods=['GET', 'POST'])
@roles_required(['ADMIN'])
def test_logging():
    """
    endpoint for seeing how the system responds to errors
    @return:
    """
    flask.current_app.logger.debug("I'm a DEBUG message")
    flask.current_app.logger.info("I'm an INFO message")
    flask.current_app.logger.warning("I'm a WARNING message")
    flask.current_app.logger.error("I'm a ERROR message")
    flask.current_app.logger.critical("I'm a CRITICAL message")