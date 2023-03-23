from archives_application import create_app

app = create_app(create_workers = True)

if __name__ == '__main__':
    app.run(debug=True)
