# archives_application/project_tools/project_tools_tasks.py

import flask
import flask_sqlalchemy
import logging
import os
from typing import Optional

from archives_application import create_app, utils
from archives_application.models import ProjectModel

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()


def _project_location_relative_to_archive(project_location: str, archives_location: str) -> str:
    return os.path.relpath(project_location, archives_location).replace(os.sep, "/")


def confirm_project_locations_task(queue_id: str, projects_list: Optional[list] = None):
    """Refresh ``projects.file_server_location`` from the project folders on the file server.

    ``projects.file_server_location`` stores a path relative to ``ARCHIVES_LOCATION``.
    If ``projects_list`` is supplied, only those project numbers are checked; otherwise
    every project in the database is checked.
    """
    with app.app_context():
        os.environ["no_proxy"] = "*"
        db: flask_sqlalchemy.SQLAlchemy = flask.current_app.extensions["sqlalchemy"]
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
        archives_location = flask.current_app.config.get("ARCHIVES_LOCATION")
        task_log = {
            "projects checked": {"completed": False, "count": 0},
            "projects updated": [],
            "projects cleared": [],
            "projects missing": [],
            "projects not found": [],
            "errors": []
        }
        progress_update = lambda log: utils.RQTaskUtils.update_task_subroutine(
            q_id=queue_id,
            sql_db=db,
            task_results=log
        )

        try:
            # Limit the scan to requested project numbers when provided, and
            # report any requested numbers that do not exist in the database.
            if projects_list:
                projects = ProjectModel.query.filter(ProjectModel.number.in_(projects_list)).all()
                found_project_numbers = {project.number for project in projects}
                task_log["projects not found"] = [
                    project_number
                    for project_number in projects_list
                    if project_number not in found_project_numbers
                ]
            else:
                projects = ProjectModel.query.order_by(ProjectModel.number.asc()).all()

            for project in projects:
                task_log["projects checked"]["count"] += 1
                try:
                    project_location, _ = utils.FileServerUtils.path_to_project_dir(
                        project_number=project.number,
                        archives_location=archives_location
                    )

                    # If the folder cannot be resolved, clear any stale stored
                    # location and record the project as missing from the server.
                    if not project_location:
                        if project.file_server_location:
                            old_file_server_location = project.file_server_location
                            project.file_server_location = None
                            task_log["projects cleared"].append({
                                "project": project.number,
                                "old file_server_location": old_file_server_location,
                                "new file_server_location": project.file_server_location
                            })
                        task_log["projects missing"].append(project.number)
                        continue

                    relative_project_location = _project_location_relative_to_archive(
                        project_location=project_location,
                        archives_location=archives_location
                    )

                    # Store only relative paths so records are portable across
                    # environments with different ARCHIVES_LOCATION roots.
                    if project.file_server_location != relative_project_location:
                        old_file_server_location = project.file_server_location
                        logging.info(
                            "Updating location for project %s from %s to %s",
                            project.number,
                            old_file_server_location,
                            relative_project_location
                        )
                        project.file_server_location = relative_project_location
                        task_log["projects updated"].append({
                            "project": project.number,
                            "old file_server_location": old_file_server_location,
                            "new file_server_location": project.file_server_location
                        })

                    # Commit periodically so a large full-database scan can
                    # report progress and avoid holding every change until the end.
                    if task_log["projects checked"]["count"] % 200 == 0:
                        db.session.commit()
                        progress_update(log=task_log)

                except utils.ArchivesPathException:
                    # Path helper failures mean the expected project directory
                    # was not found; keep processing the remaining projects.
                    task_log["projects missing"].append(project.number)
                except Exception as e:
                    # Capture per-project errors without failing the entire task.
                    task_log["errors"].append({
                        "message": f"Error confirming location for {project.number}:",
                        "exception": str(e)
                    })

            db.session.commit()
            task_log["projects checked"]["completed"] = True
            progress_update(log=task_log)

        except Exception as e:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            task_log["errors"].append({
                "message": "Error confirming project locations:",
                "exception": str(e)
            })
            progress_update(log=task_log)

        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=task_log)
        return task_log
