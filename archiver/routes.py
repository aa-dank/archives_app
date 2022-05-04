from flask import render_template, url_for, flash, redirect
from archiver.forms import *
from archiver import app

posts = [
    {
        'author': 'Corey Schafer',
        'title': 'Blog Post 1',
        'content': 'First post content',
        'date_posted': 'April 20, 2018'
    },
    {
        'author': 'Jane Doe',
        'title': 'Blog Post 2',
        'content': 'Second post content',
        'date_posted': 'April 21, 2018'
    }
]


@app.route("/")
@app.route("/home")
def home():
    return render_template('home.html', posts=posts)


@app.route("/register", methods=['GET', 'POST'])
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        flash(f'Account created for {form.username.data}!', 'success')
        return redirect(url_for('register'))
    return render_template('register.html', title='Register', form=form)


@app.route("/login", methods=['GET', 'POST'])
def login():
    form = LoginForm()
    return render_template('login.html', title='Login', form=form)


@app.route("/upload_file", methods=['GET', 'POST'])
def new_submittal():
    form = uploadFileForm()
    if form.validate_on_submit():
        # flash(f'Submittal {form.submittal_name.data} sent!', 'success')
        flash(f'File received!', 'success')
        return redirect(url_for('upload_file'))
    return render_template('upload_file.html', title='Upload File to Archive', form=form)