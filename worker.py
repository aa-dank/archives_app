"""Run RQ's default queue using the application's shared Redis endpoint."""

import redis
from rq import Worker, Queue

from archives_application import app_config, config_json

listen = ['default']
# Read the same selected JSON file as the web app; the shared resolver also
# preserves REDIS_URL environment overrides for process-supervisor deployments.
redis_url = app_config.redis_url_from_config_file(config_json)
conn = redis.from_url(redis_url)

# For Windows we need to use this:
# https://github.com/michaelbrooks/rq-win
if __name__ == '__main__':
    print("starting worker")
    queues = [Queue(name, connection=conn) for name in listen]
    worker = Worker(queues, connection=conn)
    print("worker initialized. starting work.")
    worker.work()
