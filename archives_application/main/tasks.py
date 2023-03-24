from celery import shared_task
#from celery.result import AsyncResult


#@shared_task(bind=True, ignore_result=False)
@shared_task
#@shared_task(ignore_result=False)
def divide(x, y):
    return x / y