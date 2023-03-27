import os
import redis
from rq import Worker, Queue, Connection

listen = ['default']

redis_url = os.environ.get('REDISTOGO_URL', 'redis://localhost:6379')

conn = redis.from_url(redis_url)

# For Windows we need to use this:
# https://github.com/michaelbrooks/rq-win
if __name__ == '__main__':
    with Connection(conn):
        worker = Worker(list(map(Queue, listen)))
        worker.work()