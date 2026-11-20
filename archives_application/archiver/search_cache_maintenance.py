"""Operator cleanup of Archive Search output while publishers are stopped."""

from dataclasses import dataclass, field
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
import redis

from archives_application import utils
from archives_application.cache import KeyValueCache


@dataclass
class SearchOutputPurge:
    """Report independent cache and filesystem outcomes, including partial work."""

    cache_keys_removed: int = 0
    export_files_removed: int = 0
    cache_errors: list[str] = field(default_factory=list)
    filesystem_errors: list[str] = field(default_factory=list)

    @property
    def complete(self):
        return not self.cache_errors and not self.filesystem_errors


def purge_archive_search_output(app, namespace: str) -> SearchOutputPurge:
    """Discard this namespace's search keys and this host's search workbooks.

    The operator must stop new searches/exports and drain active publishers
    before calling. This function neither changes suppression flags nor commits
    SQL. It also works with result caching disabled, when old keys may remain.
    """
    from archives_application.archiver.archive_search import is_archive_search_workbook

    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("A non-empty Archive Search cache namespace is required.")
    outcome = SearchOutputPurge()
    prefix = f"{namespace}:archive-search:"
    # Redis MATCH uses glob syntax. Treat the configured namespace literally.
    pattern = "".join("\\" + char if char in "\\*?[]" else char for char in prefix) + "*"
    try:
        cache = KeyValueCache.from_url(
            app.config["KEY_VALUE_STORE_URL"], app.config.get("CACHE_TIMEOUT_SECONDS", 0.5))
        try:
            for key in cache.client.scan_iter(match=pattern, count=100):
                if not key.startswith(prefix.encode("utf-8")):
                    continue
                try:
                    outcome.cache_keys_removed += cache.client.delete(key)
                except (redis.RedisError, OSError) as error:
                    outcome.cache_errors.append(f"Key deletion failed ({type(error).__name__}).")
        finally:
            cache.client.close()
    except (redis.RedisError, OSError, ValueError, TypeError, KeyError) as error:
        outcome.cache_errors.append(f"Cache purge did not finish ({type(error).__name__}).")

    try:
        # Use the same server-controlled storage path as the workbook writer.
        directory = Path(utils.FlaskAppUtils.create_temp_filepath(
            filename=".archive-search-purge", unique_filepath=False)).parent
        for path in directory.iterdir():
            is_export = is_archive_search_workbook(path.name)
            is_reservation = path.name.endswith(".reserve") and is_archive_search_workbook(path.name[:-8])
            is_staging = path.name.startswith(".archive-search-") and path.name.endswith(".xlsx")
            if not (is_export or is_reservation or is_staging):
                continue
            try:
                path.unlink()
                outcome.export_files_removed += 1
            except FileNotFoundError:
                pass  # Repeated cleanup is harmless.
            except OSError as error:
                outcome.filesystem_errors.append(f"Export deletion failed ({type(error).__name__}).")
    except OSError as error:
        outcome.filesystem_errors.append(f"Export purge did not finish ({type(error).__name__}).")
    return outcome


@click.command("purge-archive-search-output")
@click.option("--namespace", help="Override CACHE_NAMESPACE, for example to purge a previous namespace.")
@click.option("--publishers-stopped", is_flag=True, required=True,
              help="Confirm new searches/exports are stopped and active publishers have drained.")
@with_appcontext
def purge_archive_search_output_command(namespace, publishers_stopped):
    """Remove all Archive Search cached results and Excel files; preserve RQ."""
    namespace = namespace if namespace is not None else current_app.config.get("CACHE_NAMESPACE")
    try:
        result = purge_archive_search_output(current_app, namespace)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Cache keys removed: {result.cache_keys_removed}")
    click.echo(f"Export files removed: {result.export_files_removed}")
    for error in result.cache_errors:
        click.echo(f"Cache: {error}", err=True)
    for error in result.filesystem_errors:
        click.echo(f"Filesystem: {error}", err=True)
    if not result.complete:
        raise click.ClickException("Purge incomplete. Keep publishers stopped, resolve errors, and retry.")
    click.echo("Purge complete. Old export links are no longer available; rerun searches after resuming.")
