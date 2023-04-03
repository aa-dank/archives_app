FROM python:3.9-slim-buster
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    libpango1.0-dev \
    libcairo2-dev \
    libgirepository1.0-dev \
    pkg-config \
    smbclient \
    redis-server \
    nfs-common \
    cifs-utils

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r requirements.txt

# Mount the Windows shares
RUN mkdir /app/Data

# Expose port 5000 for the Flask application
EXPOSE 5000

# Set the environment variable for Flask app
ENV FLASK_APP=run.py

# Run app.py and Redis server when the container launches
CMD ["sh", "-c", "service redis-server start && flask run --host=0.0.0.0"]