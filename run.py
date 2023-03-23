from archives_application import create_app

app, celery = create_app(create_workers = True)
app.app_context().push()

if __name__ == '__main__':
    app.run(debug=True)