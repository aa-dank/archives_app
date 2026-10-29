"""Run RQ's default queue using the application's shared key-value store."""

from rq import Worker, Queue

from archives_application import app_config, config_json
from archives_application.key_value_store import rq_connection_from_url

listen = ['default']
# Read the same selected JSON file as the web app; the shared resolver also
# preserves endpoint environment overrides for process-supervisor deployments.
key_value_store_url = app_config.key_value_store_url_from_config_file(config_json)
conn = rq_connection_from_url(key_value_store_url)

# For Windows we need to use this:
# https://github.com/michaelbrooks/rq-win
if __name__ == '__main__':
    print("starting worker")
    queues = [Queue(name, connection=conn) for name in listen]
    worker = Worker(queues, connection=conn)
    print("worker initialized. starting work.")
    worker.work()
