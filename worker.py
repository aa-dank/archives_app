# /worker.py
import os
import redis
from rq import Worker, Queue
from rq.connections import RedisConnection

listen = ['default']

redis_url = 'redis://localhost:6379'

conn = redis.from_url(redis_url)

# For Windows we need to use this:
# https://github.com/michaelbrooks/rq-win
if __name__ == '__main__':
    print("Starting worker")
    with RedisConnection(conn):
        worker = Worker(list(map(Queue, listen)))
        print("Worker initialized. Starting work.")
        worker.work()