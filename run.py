from archives_application import create_app, ext_celery

app = create_app()
celery = ext_celery.celery

if __name__ == '__main__':
    app.run(debug=True)
