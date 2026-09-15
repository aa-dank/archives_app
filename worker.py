# /worker.py
import os
import redis
from rq import Worker, Queue
from archives_application.task_context import initialize_task_app

listen = ['default']
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
conn = redis.from_url(redis_url)
def run_worker():
    """Initialize Flask once, then start the RQ worker."""
    initialize_task_app()
    print("starting worker")
    queues = [Queue(name, connection=conn) for name in listen]
    worker = Worker(queues, connection=conn)
    print("worker initialized. starting work.")
    worker.work()


# For Windows we need to use this:
# https://github.com/michaelbrooks/rq-win
if __name__ == '__main__':
    run_worker()
